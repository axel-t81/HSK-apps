# HSK 1 Listening Audio

A standalone listening course for the 300-word HSK 3.0 Band 1 exam list. This project is independent of the Anki deck and does not require or modify it.

The course contains **10 MP3 tracks with 30 words each**, and every track is longer than ten minutes. Every entry contains:

1. The Chinese word twice
2. Its full English meaning
3. The Chinese word once more
4. The full English meaning once more
5. A Chinese example sentence at 85% of the normal speaking rate
6. The English translation of that sentence

The selected voices are ElevenLabs **Sarah** (young female) and **Brian** (middle-aged male). Pauses are 1.5 seconds between the initial repetitions and 2 seconds between sections and entries.

To avoid unstable isolated-word pronunciation, every Chinese word prompt uses **Eleven v4**, forced to Mandarin with `language_code=zh`, in the complete phrase `请跟我读：…` (“Please read after me: …”). The carrier phrase is intentionally retained in the finished audio, and no word audio is trimmed. The same female prompt clip is reused for both initial repetitions so their pronunciation is identical. Brian's English meanings and translations use Eleven v4 with `language_code=en`; the remaining English and sentence recordings use Eleven Multilingual v2.

## Listening tracks

| Track | Words | MP3 | Transcript |
|---:|---:|---|---|
| 01 | 1–30 | [MP3](out/listening/HSK1_Track_01_1-30.mp3) | [Transcript](out/listening/HSK1_Track_01_1-30_transcript.md) |
| 02 | 31–60 | [MP3](out/listening/HSK1_Track_02_31-60.mp3) | [Transcript](out/listening/HSK1_Track_02_31-60_transcript.md) |
| 03 | 61–90 | [MP3](out/listening/HSK1_Track_03_61-90.mp3) | [Transcript](out/listening/HSK1_Track_03_61-90_transcript.md) |
| 04 | 91–120 | [MP3](out/listening/HSK1_Track_04_91-120.mp3) | [Transcript](out/listening/HSK1_Track_04_91-120_transcript.md) |
| 05 | 121–150 | [MP3](out/listening/HSK1_Track_05_121-150.mp3) | [Transcript](out/listening/HSK1_Track_05_121-150_transcript.md) |
| 06 | 151–180 | [MP3](out/listening/HSK1_Track_06_151-180.mp3) | [Transcript](out/listening/HSK1_Track_06_151-180_transcript.md) |
| 07 | 181–210 | [MP3](out/listening/HSK1_Track_07_181-210.mp3) | [Transcript](out/listening/HSK1_Track_07_181-210_transcript.md) |
| 08 | 211–240 | [MP3](out/listening/HSK1_Track_08_211-240.mp3) | [Transcript](out/listening/HSK1_Track_08_211-240_transcript.md) |
| 09 | 241–270 | [MP3](out/listening/HSK1_Track_09_241-270.mp3) | [Transcript](out/listening/HSK1_Track_09_241-270_transcript.md) |
| 10 | 271–300 | [MP3](out/listening/HSK1_Track_10_271-300.mp3) | [Transcript](out/listening/HSK1_Track_10_271-300_transcript.md) |

Also available: the [listening review checklist](out/listening/HSK1_listening_review_checklist.md) and short [voice auditions](out/auditions/).

## Project files

- `data/hsk1_300.csv` — standalone copy of the 300-word vocabulary data
- `data/listening_sentences.json` — curated Chinese sentences and English translations
- `scripts/gen_listening_auditions.py` — voice audition generator
- `scripts/gen_listening_tracks.py` — thirty-word track generator
- `scripts/validate_listening_tracks.py` — complete-course audio and transcript validation
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

Track numbers range from 1 to 10. The generator validates the vocabulary and complete 300-entry sentence bank, caches each generated WAV using its spoken content and voice settings, and writes an MP3 plus a transcript. Rerunning a track reuses matching cached clips instead of consuming more credits.

To regenerate the voice auditions:

```bash
.venv/bin/python -I scripts/gen_listening_auditions.py
```

## Validate the complete course

On macOS, with the generator's local build manifests present:

```bash
.venv/bin/python -I scripts/validate_listening_tracks.py
```

The validator checks all ten filenames, 30 transcript entries per track, 210 sections per track, identical initial repetition pairs, complete Mandarin prompts, forced-language settings, sentence speed, full MP3 decoding, duration over ten minutes, and audio levels.

The repository's MIT license applies to original project code. ElevenLabs-generated speech remains subject to the applicable service terms.
