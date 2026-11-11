"""Validate the ten finished HSK listening tracks on macOS.

Run after generation:
    .venv/bin/python -I scripts/validate_listening_tracks.py

The generator's local build manifests are required for clip-level checks.
"""
import array
import json
import math
import re
import subprocess
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "listening"
SAMPLE_RATE = 24000
SENTENCE_SPEED = 0.95 * 0.85


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_track(track):
    first = (track - 1) * 30 + 1
    last = first + 29
    stem = f"HSK1_Track_{track:02d}_{first}-{last}"
    mp3 = OUT / f"{stem}.mp3"
    transcript_path = OUT / f"{stem}_transcript.md"
    manifest_path = ROOT / "build" / "listening" / f"track_{track:02d}_manifest.json"
    for path in (mp3, transcript_path, manifest_path):
        require(path.is_file() and path.stat().st_size > 0, f"Missing or empty: {path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["file"] == mp3.name, f"Manifest filename mismatch for track {track}")
    clips = manifest["clips"]
    require(len(clips) == 210, f"Track {track} does not have 210 sections")

    for position in range(30):
        group = clips[position * 7:(position + 1) * 7]
        number = first + position
        require([clip["clip"] for clip in group] == list(range(1, 8)),
                f"Section order mismatch for word {number}")
        require(all(clip["no"] == number for clip in group),
                f"Word number mismatch for {number}")
        require(group[0]["pcm_sha256"] == group[1]["pcm_sha256"],
                f"Initial repetitions differ for word {number}")
        for clip in (group[0], group[1], group[3]):
            require(clip["spoken_text"] == f"请跟我读：{clip['word']}。",
                    f"Incorrect Mandarin prompt for word {number}")
            require(clip["model"] == "eleven_v4" and clip["language_code"] == "zh",
                    f"Mandarin was not forced for word {number}")
        for clip in (group[4], group[6]):
            require(clip["model"] == "eleven_v4" and clip["language_code"] == "en",
                    f"Brian's English was not forced for word {number}")
        require(group[5]["speed"] == SENTENCE_SPEED,
                f"Sentence speed mismatch for word {number}")

    transcript = transcript_path.read_text(encoding="utf-8")
    require(len(re.findall(r"^## \d+\.", transcript, re.M)) == 30,
            f"Transcript does not have 30 entries for track {track}")
    for number in range(first, last + 1):
        require(re.search(rf"^## {number}\. ", transcript, re.M) is not None,
                f"Transcript is missing word {number}")

    with tempfile.TemporaryDirectory() as temporary:
        decoded = Path(temporary) / "decoded.wav"
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@24000", str(mp3), str(decoded)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        with wave.open(str(decoded), "rb") as audio:
            require(audio.getnchannels() == 1 and audio.getsampwidth() == 2,
                    f"Unexpected decoded format for track {track}")
            rate = audio.getframerate()
            frames = audio.getnframes()
            raw = audio.readframes(frames)

    duration = frames / rate
    require(duration > 600, f"Track {track} is not longer than ten minutes")
    require(abs(duration - manifest["duration_seconds"]) < 0.2,
            f"Decoded duration mismatch for track {track}")
    samples = array.array("h")
    samples.frombytes(raw)
    peak = max(abs(sample) for sample in samples) / 32768
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768
    clipped_samples = sum(abs(sample) >= 32767 for sample in samples)
    # Lossy MP3 decoding can produce an isolated full-scale sample even when
    # the source WAV is unclipped. Reject sustained clipping, not that overshoot.
    require(0.01 < peak <= 1.0 and rms > 0.005 and clipped_samples / len(samples) < 0.00001,
            f"Silent or persistently clipped audio suspected in track {track}")
    return {
        "track": track,
        "duration": duration,
        "bytes": mp3.stat().st_size,
        "peak": peak,
        "rms": rms,
        "clipped_samples": clipped_samples,
    }


def main():
    tracks = sorted(OUT.glob("HSK1_Track_*.mp3"))
    require(len(tracks) == 10, f"Expected ten track MP3s, found {len(tracks)}")
    require((OUT / "HSK1_listening_review_checklist.md").is_file(),
            "Missing listening review checklist")
    results = [validate_track(track) for track in range(1, 11)]
    for result in results:
        minutes = int(result["duration"] // 60)
        seconds = result["duration"] % 60
        print(f"Track {result['track']:02d}: PASS — {minutes}:{seconds:04.1f}, "
              f"210 sections, peak={result['peak']:.3f}, RMS={result['rms']:.3f}")
    total = sum(result["duration"] for result in results)
    print(f"PASS: 10 tracks, 300 words, 2,100 sections, {total / 3600:.2f} hours")


if __name__ == "__main__":
    main()
