# HSK 3.0 Band 1 Anki Deck

A ready-to-import Anki deck for the **300-word HSK 3.0 Band 1 exam list**, with curated pinyin, English meanings, alternate readings, and Chinese audio.

## Download and import

**[Download the Anki deck](out/HSK_3.0_Band_1_300.apkg)** and import it with Anki's **File → Import** command.

The package contains **300 notes, 600 cards, and 300 audio clips**:

- **Recognition:** the question shows only hanzi and an optional audio control; the answer shows part of speech, pinyin, and meaning.
- **Production:** the question shows the English meaning; the answer shows hanzi, pinyin, part of speech, and optional audio.

Audio does **not** autoplay. Use Anki's replay button or **R / F5**. The deck includes an options group named **HSK 3.0 · manual audio**; if Anki preserves an older options group when updating an existing import, select this group or enable **Deck Options → Audio → Don't play audio automatically**.

To study only Chinese → English, suspend the Production card type in Anki's card browser.

## Files

- [`out/HSK_3.0_Band_1_300.apkg`](out/HSK_3.0_Band_1_300.apkg) — ready-to-import deck
- [`out/hsk1_300.csv`](out/hsk1_300.csv) — the 300-word dataset in CSV form
- [`data/final.json`](data/final.json) — curated source data used by the deck builder
- [`out/QA_REPORT.md`](out/QA_REPORT.md) — provenance, validation results, and limitations
- [`scripts/`](scripts/) — extraction, curation, audio, CSV, and packaging scripts

## Rebuild on macOS

Python 3 and the macOS **Tingting** Chinese voice are required for regenerating audio. The ready-made `.apkg` does not require Python.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -I scripts/gen_audio.py
.venv/bin/python -I scripts/build_deck.py
.venv/bin/python -I scripts/export_csv.py
```

The audio generator uses macOS `say` and `afconvert`, and reuses existing valid clips. The deck builder fails on missing audio or duplicate production prompts.

For changes limited to templates or curated JSON, only the final two commands are needed.

## Recurate the source data

The normal rebuild starts from the tracked `data/final.json`. To repeat PDF extraction, obtain the two source PDFs from the sites below and place them at these local, git-ignored paths:

```text
data/raw/hanzistroke/HSK1-Vocabulary-3.0.pdf
data/raw/mandarinbean/New-HSK-Vocabulary-Level-1.pdf
```

Then run:

```bash
.venv/bin/python -I scripts/extract.py mb data/raw/mandarinbean/New-HSK-Vocabulary-Level-1.pdf data/mb.json
.venv/bin/python -I scripts/extract.py hs data/raw/hanzistroke/HSK1-Vocabulary-3.0.pdf data/hs.json
.venv/bin/python -I scripts/merge.py
.venv/bin/python -I scripts/finalize.py
```

Review the extraction and validation output before rebuilding.

## Sources and attribution

- [MandarinBean — New HSK 1 word list](https://mandarinbean.com/new-hsk-1-word-list): cross-validation, translations, and part-of-speech labels
- [HanziStroke](https://hanzistroke.com/): cross-validation, syllable-spaced pinyin, and alternate readings
- GF0025-2021, *国际中文教育中文水平等级标准*: reference standard; the downloaded scan was not redistributed
- [ivankra/hsk30](https://github.com/ivankra/hsk30): machine-readable reference transcription used for band-membership checks

The two source PDFs agreed on all 300 simplified word forms. Detailed validation is in the [QA report](out/QA_REPORT.md).

The repository's MIT license applies to original project code. Third-party source material and speech-generation services retain their own terms and rights; raw source PDFs are not included in this repository.
