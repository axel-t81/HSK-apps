# HSK 1 Listening Audio

A standalone listening course for the 300-word HSK 3.0 Band 1 exam list. This project is independent of the Anki deck and does not require or modify it.

The planned output is **10 MP3 tracks with 30 words each**, giving tracks of approximately 11–12 minutes. Every entry contains:

1. The Chinese word twice
2. Its full English meaning
3. The Chinese word once more
4. The full English meaning once more
5. A Chinese example sentence at 85% of the normal speaking rate
6. The English translation of that sentence

The selected voices are ElevenLabs **Sarah** (young female) and **Brian** (middle-aged male). Pauses are 1.5 seconds between the initial repetitions and 2 seconds between sections and entries.

To avoid unstable isolated-word pronunciation, every Chinese word prompt uses **Eleven v4**, forced to Mandarin with `language_code=zh`, in the complete phrase `请跟我读：…` (“Please read after me: …”). The carrier phrase is intentionally retained in the finished audio, and no word audio is trimmed. The same female prompt clip is reused for both initial repetitions so their pronunciation is identical. Brian's English meanings and translations use Eleven v4 with `language_code=en`; the remaining English and sentence recordings use Eleven Multilingual v2.

## Current deliverables

- [Track 01 — words 1–30](out/listening/HSK1_Track_01_1-30_v3.mp3)
- [Track 01 transcript](out/listening/HSK1_Track_01_1-30_v3_transcript.md)
- [Listening review checklist](out/listening/HSK1_listening_review_checklist.md)
- [Voice auditions](out/auditions/)

## Project files

- `data/hsk1_300.csv` — standalone copy of the 300-word vocabulary data
- `data/listening_sentences.json` — curated Chinese sentences and English translations
- `scripts/gen_listening_auditions.py` — voice audition generator
- `scripts/gen_listening_tracks.py` — thirty-word track generator
- `out/listening/` — finished tracks, transcripts, and review checklist
- `out/auditions/` — short voice-comparison recordings, not full listening lessons
- `build/` — local, git-ignored WAV cache used to avoid repeated TTS charges

## Setup

Python 3 and an ElevenLabs API key are required for regeneration. The finished MP3 files do not require Python.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Supply `ELEVENLABS_API_KEY` through a local permission-restricted environment file. Do not commit the key or environment file.

## Generate a track

After loading the API key into the environment, run:

```bash
.venv/bin/python -I scripts/gen_listening_tracks.py --track 1
```

Track numbers range from 1 to 10. The generator validates the vocabulary and sentence data, caches each generated WAV using its spoken content and voice settings, and writes an MP3 plus a transcript. Example sentences must be added to `data/listening_sentences.json` before generating later tracks.

To regenerate the voice auditions:

```bash
.venv/bin/python -I scripts/gen_listening_auditions.py
```

The repository's MIT license applies to original project code. ElevenLabs-generated speech remains subject to the applicable service terms.
