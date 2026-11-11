"""Generate two six-step HSK listening auditions with ElevenLabs.

Run from any directory:
    set -a; source ~/.config/hsk-apps/elevenlabs.env; set +a
    .venv/bin/python -I scripts/gen_listening_auditions.py

Generated WAV clips are cached to avoid using credits again on a rerun.
"""
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
OUT = ROOT / "out" / "auditions"
CACHE = ROOT / "build" / "auditions"
SAMPLE_RATE = 24000
MODEL = "eleven_multilingual_v2"
DEFAULT_SPEED = 0.95

VOICES = {
    "sarah": ("EXAVITQu4vr4xnSDxMaL", "Sarah — young American female"),
    "brian": ("nPczCjzI2devNBz1zQrb", "Brian — middle-aged American male"),
    "jessica": ("cgSgspJ2msm6clMCkdW9", "Jessica — young American female"),
    "chen": ("cwzmKSYMCC9Aym1ymCnt", "Mr. Chen — middle-aged Beijing Mandarin male"),
    "eric": ("cjVigY5qzO86Huf0OWal", "Eric — middle-aged American male"),
}

AUDITIONS = {
    "A": {"female_zh": "sarah", "female_en": "sarah",
          "male_zh": "brian", "male_en": "brian"},
    "B": {"female_zh": "jessica", "female_en": "jessica",
          "male_zh": "chen", "male_en": "eric"},
}

# (role, spoken text, silence after the clip in seconds)
SEQUENCE = [
    ("female_zh", "喜欢", 0.7),
    ("female_zh", "喜欢", 1.0),
    ("female_en", "to like", 1.0),
    ("male_zh", "喜欢", 1.0),
    ("male_en", "to like", 1.0),
    ("male_zh", "我喜欢中国菜。", 1.0),
    ("male_en", "I like Chinese food.", 0.5),
]


def voice_settings(speed):
    return {
        "stability": 0.6,
        "similarity_boost": 0.75,
        "style": 0.0,
        "use_speaker_boost": True,
        "speed": speed,
    }


def clip_cache_path(audition, index, voice_key, text, speed=DEFAULT_SPEED):
    """Include every speech-affecting input in the reusable clip's identity."""
    signature = json.dumps(
        [MODEL, VOICES[voice_key][0], text, voice_settings(speed)],
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(signature).hexdigest()[:12]
    return CACHE / f"audition_{audition}_{index:02d}_{voice_key}_{digest}.wav"


def synthesize(text, voice_key, destination, speed=DEFAULT_SPEED):
    if destination.exists() and destination.stat().st_size > 1000:
        return

    voice_id = VOICES[voice_key][0]
    payload = {
        "text": text,
        "model_id": MODEL,
        "voice_settings": voice_settings(speed),
    }
    url = (f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
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
        return audio.readframes(audio.getnframes())


def silence(seconds):
    return b"\x00" * round(SAMPLE_RATE * seconds * 2)


def encode_mp3(pcm, destination):
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(128)
    encoder.set_in_sample_rate(SAMPLE_RATE)
    encoder.set_channels(1)
    encoder.set_quality(2)
    destination.write_bytes(encoder.encode(pcm) + encoder.flush())


def main():
    if not os.environ.get("ELEVENLABS_API_KEY"):
        raise SystemExit("ELEVENLABS_API_KEY is not set")
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest = {}

    for audition, roles in AUDITIONS.items():
        parts = []
        clip_details = []
        for index, (role, text, pause) in enumerate(SEQUENCE, 1):
            voice_key = roles[role]
            clip = clip_cache_path(audition, index, voice_key, text)
            print(f"Audition {audition}, clip {index}/7: {text}", flush=True)
            synthesize(text, voice_key, clip, DEFAULT_SPEED)
            pcm = read_wav(clip)
            parts.extend((pcm, silence(pause)))
            clip_details.append({
                "step": index,
                "text": text,
                "voice": VOICES[voice_key][1],
                "pause_after_seconds": pause,
                "duration_seconds": round(len(pcm) / (SAMPLE_RATE * 2), 3),
            })

        filename = OUT / f"HSK1_listening_audition_{audition}.mp3"
        encode_mp3(b"".join(parts), filename)
        manifest[audition] = {
            "file": filename.name,
            "duration_seconds": round(sum(len(part) for part in parts) / (SAMPLE_RATE * 2), 3),
            "clips": clip_details,
        }
        print(f"Wrote {filename}", flush=True)

    (OUT / "audition_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
