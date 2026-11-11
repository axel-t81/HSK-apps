"""Generate Tingting TTS audio for all 300 words: say -> AIFF -> m4a (AAC).

Run with -I:  python -I scripts/gen_audio.py
"""
import json
import os
import subprocess
import sys

OUT = "audio"
os.makedirs(OUT, exist_ok=True)

records = json.load(open("data/final.json", encoding="utf-8"))
failures = []
for i, r in enumerate(records, 1):
    word = r["simplified"]
    no = r["no"]
    aiff = f"/tmp/hsk1_tts_{no:03d}.aiff"
    m4a = f"{OUT}/hsk1_{no:03d}.m4a"
    if os.path.exists(m4a) and os.path.getsize(m4a) > 1000:
        continue
    try:
        subprocess.run(["say", "-v", "Tingting", "-o", aiff, "--", word],
                       check=True, timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "48000",
                        aiff, m4a], check=True, timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        os.remove(aiff)
    except subprocess.CalledProcessError as e:
        failures.append((word, e.stderr.decode(errors="replace")[:120]))
    if i % 50 == 0:
        print(f"{i}/{len(records)} done", flush=True)

print(f"finished: {len(records) - len(failures)} OK, {len(failures)} failed")
for w, err in failures:
    print("FAIL", w, err)
sys.exit(1 if failures else 0)
