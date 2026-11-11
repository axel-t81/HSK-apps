"""Generate thirty-word HSK listening tracks with ElevenLabs.

Run with credentials loaded, for example:
    set -a; source ~/.config/hsk-apps/elevenlabs.env; set +a
    .venv/bin/python -I scripts/gen_listening_tracks.py --track 1

Clips are cached using their voice, text, model, and speed so reruns do not
consume credits unless the spoken content or settings change. Chinese word
prompts retain the full Mandarin phrase “请跟我读：…” with Eleven v4; the same
female clip is used for both initial repeats. Brian's English uses forced
English with Eleven v4 to avoid short-phrase language instability.
"""
import argparse
import csv
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

import lameenc

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "listening"
CACHE = ROOT / "build" / "listening" / "clips"
SAMPLE_RATE = 24000
WORDS_PER_TRACK = 30
MODEL = "eleven_multilingual_v2"
NORMAL_SPEED = 0.95
SENTENCE_SPEED = NORMAL_SPEED * 0.85
WORD_MODEL = "eleven_v4"
FEMALE = ("EXAVITQu4vr4xnSDxMaL", "Sarah — young American female")
MALE = ("nPczCjzI2devNBz1zQrb", "Brian — middle-aged American male")


def synthesize(text, voice, speed, destination, model=MODEL, language_code=None):
    if destination.exists() and destination.stat().st_size > 1000:
        return

    payload = {
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": 0.6,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": speed,
        },
    }
    if language_code:
        payload["language_code"] = language_code
    url = (f"https://api.elevenlabs.io/v1/text-to-speech/{voice[0]}"
           "?output_format=wav_24000")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "xi-api-key": os.environ["ELEVENLABS_API_KEY"],
            "Content-Type": "application/json",
            "Accept": "audio/wav",
        },
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                audio = response.read()
            if not audio.startswith(b"RIFF") or len(audio) < 1000:
                raise RuntimeError("ElevenLabs returned invalid WAV audio")
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(audio)
            os.replace(temporary, destination)
            return
        except urllib.error.HTTPError as error:
            message = error.read().decode("utf-8", errors="replace")[:1000]
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"ElevenLabs HTTP {error.code}: {message}") from error
            time.sleep(2 ** attempt)


def read_wav(path):
    with wave.open(str(path), "rb") as audio:
        parameters = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
        if parameters != (1, 2, SAMPLE_RATE):
            raise RuntimeError(f"Unexpected WAV format for {path}: {parameters}")
        frames = audio.getnframes()
        if frames < SAMPLE_RATE // 10:
            raise RuntimeError(f"Suspiciously short audio clip: {path}")
        return audio.readframes(frames)


def silence(seconds):
    return b"\x00" * round(SAMPLE_RATE * seconds * 2)


def encode_mp3(pcm, destination):
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(128)
    encoder.set_in_sample_rate(SAMPLE_RATE)
    encoder.set_channels(1)
    encoder.set_quality(2)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(encoder.encode(pcm) + encoder.flush())
    os.replace(temporary, destination)


def load_track(track):
    with (ROOT / "data" / "hsk1_300.csv").open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    with (ROOT / "data" / "listening_sentences.json").open(encoding="utf-8") as source:
        sentence_rows = json.load(source)
    sentences = {row["no"]: row for row in sentence_rows}

    start = (track - 1) * WORDS_PER_TRACK
    selected = rows[start:start + WORDS_PER_TRACK]
    if len(selected) != WORDS_PER_TRACK:
        raise ValueError(f"Track {track} does not contain {WORDS_PER_TRACK} words")
    for row in selected:
        no = int(row["no"])
        sentence = sentences.get(no)
        if not sentence:
            raise ValueError(f"Missing example sentence for {no}: {row['simplified']}")
        if sentence["simplified"] != row["simplified"]:
            raise ValueError(f"Example sentence mismatch for {no}")
        if row["simplified"] not in sentence["sentence_zh"]:
            raise ValueError(f"Example sentence does not contain {row['simplified']}")
    return selected, sentences


def spoken_sequence(row, sentence, final_word):
    word = row["simplified"]
    meaning = row["meaning"]
    return [
        (FEMALE, word, NORMAL_SPEED, 1.5, "Chinese word, first repetition"),
        (FEMALE, word, NORMAL_SPEED, 2.0, "Chinese word, second repetition"),
        (FEMALE, meaning, NORMAL_SPEED, 2.0, "English meaning"),
        (MALE, word, NORMAL_SPEED, 2.0, "Chinese word"),
        (MALE, meaning, NORMAL_SPEED, 2.0, "English meaning"),
        (MALE, sentence["sentence_zh"], SENTENCE_SPEED, 2.0, "Chinese example sentence"),
        (MALE, sentence["sentence_en"], NORMAL_SPEED, 1.0 if final_word else 2.0,
         "English sentence translation"),
    ]


def write_transcript(track, rows, sentences, destination):
    lines = [
        f"# HSK 1 Listening — Track {track:02d}",
        "",
        f"Words {rows[0]['no']}–{rows[-1]['no']}. Voices: Sarah (female) and Brian (male).",
        "Chinese word prompts use 请跟我读：… (“Please read after me: …”).",
        "Chinese example sentences are spoken at 85% of the normal speaking rate used in the track.",
        "",
    ]
    for row in rows:
        no = int(row["no"])
        sentence = sentences[no]
        lines.extend([
            f"## {no}. {row['simplified']} — {row['pinyin']}",
            "",
            f"**Full meaning:** {row['meaning']}",
            "",
            f"**Example:** {sentence['sentence_zh']}",
            "",
            f"**Translation:** {sentence['sentence_en']}",
            "",
        ])
    destination.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--track", type=int, default=1, choices=range(1, 301 // WORDS_PER_TRACK + 1)
    )
    parser.add_argument("--suffix", default="")
    args = parser.parse_args()
    if not os.environ.get("ELEVENLABS_API_KEY"):
        raise SystemExit("ELEVENLABS_API_KEY is not set")

    rows, sentences = load_track(args.track)
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    parts = []
    manifest = []

    for position, row in enumerate(rows):
        no = int(row["no"])
        sentence = sentences[no]
        print(f"Track {args.track:02d}: {row['simplified']} "
              f"({position + 1}/{len(rows)})", flush=True)
        for clip_no, (voice, text, speed, pause, label) in enumerate(
                spoken_sequence(row, sentence, position == len(rows) - 1), 1):
            if clip_no in (1, 2, 4):
                generation_clip = 1 if clip_no in (1, 2) else 4
                spoken_text = f"请跟我读：{text}。"
                model = WORD_MODEL
                language_code = "zh"
                signature = json.dumps(
                    [model, voice[0], spoken_text, speed, language_code],
                    ensure_ascii=False,
                ).encode("utf-8")
                digest = hashlib.sha256(signature).hexdigest()[:12]
                clip = CACHE / f"{no:03d}_{generation_clip:02d}_{digest}.wav"
                synthesize(spoken_text, voice, speed, clip, model, language_code)
            else:
                spoken_text = text
                model = WORD_MODEL if clip_no in (5, 7) else MODEL
                language_code = "en" if clip_no in (5, 7) else None
                signature_values = [model, voice[0], text, speed]
                if language_code:
                    signature_values.append(language_code)
                signature = json.dumps(signature_values, ensure_ascii=False).encode("utf-8")
                digest = hashlib.sha256(signature).hexdigest()[:12]
                clip = CACHE / f"{no:03d}_{clip_no:02d}_{digest}.wav"
                synthesize(text, voice, speed, clip, model, language_code)
            pcm = read_wav(clip)
            parts.extend((pcm, silence(pause)))
            manifest.append({
                "no": no,
                "word": row["simplified"],
                "clip": clip_no,
                "label": label,
                "text": text,
                "spoken_text": spoken_text,
                "voice": voice[1],
                "model": model,
                "language_code": language_code,
                "speed": speed,
                "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
                "duration_seconds": round(len(pcm) / (SAMPLE_RATE * 2), 3),
                "pause_after_seconds": pause,
            })

    stem = f"HSK1_Track_{args.track:02d}_{rows[0]['no']}-{rows[-1]['no']}"
    if args.suffix:
        stem += f"_{args.suffix}"
    destination = OUT / f"{stem}.mp3"
    encode_mp3(b"".join(parts), destination)
    duration = sum(len(part) for part in parts) / (SAMPLE_RATE * 2)
    write_transcript(args.track, rows, sentences, OUT / f"{stem}_transcript.md")
    metadata = {
        "track": args.track,
        "file": destination.name,
        "duration_seconds": round(duration, 3),
        "clips": manifest,
    }
    (ROOT / "build" / "listening" / f"track_{args.track:02d}_manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {destination}")
    print(f"Duration: {duration:.2f} seconds")


if __name__ == "__main__":
    main()
