# HSK 3.0 Band 1 Anki Deck — QA Report

**Deck**: `out/HSK_3.0_Band_1_300.apkg` · 300 notes · 2 card types per note · Tingting TTS audio (.m4a)
**Scope**: the official new-HSK exam list, HSK Band/Level 1 = **300 words** (per the HSK 3.0 exam outline).

## Sources

| Source | Role |
|---|---|
| `HSK1-Vocabulary-3.0.pdf` (hanzistroke.com) | Primary extraction (syllable-spaced pinyin, alt readings) |
| `New-HSK-Vocabulary-Level-1.pdf` (mandarinbean.com) | Cross-validation + primary English translations + POS |
| GF0025-2021 standard (Beijing gov PDF, 260 pp.) | Downloaded; **scanned, no text layer** → not machine-parseable |
| [ivankra/hsk30](https://github.com/ivankra/hsk30) CSV (11,092 entries) | Machine-readable transcription of the standard's tables; used for band-membership cross-check |

## Verification results

1. **Word list integrity — 300/300 identical across both PDFs.** The two independent sources agree entry-for-entry on all 300 simplified forms and their order. No duplicates (incl. same-hanzi/different-reading check).
2. **Pinyin — base-form and tone-source checks pass.** All source disagreements were resolved: 6 erhua words where HanziStroke wrote e.g. "哪儿 nǎ ér" (correct: **nǎr**), plus 女儿 (apostrophe variant). pypinyin reports **0 base-form mismatches** after documented polyphone exceptions. Independently, the final tone-marked pinyin matches MandarinBean for **300/300 entries** after normalizing whitespace and apostrophes. The reference-CSV comparison found only the expected 一/不 sandhi differences.
3. **Tone-number pinyin** is derived directly from the curated tone-marked pinyin, preserving word-specific readings, neutral tones, erhua, 一/不 sandhi, and the alternate readings of 谁.
4. **English meanings** come from the two PDFs (the official standard has no translations), with curated overrides. All 11 previously duplicated production prompts were disambiguated with usage, politeness, or register notes; 您 now retains its polite distinction, and 点 uses the classifier sense "small amounts".
5. **Alt readings** preserved on 还 huán, 好 hào, 看 kān, 了 liǎo, 只 zhǐ, 谁 shéi/shuí.
6. **POS**: MandarinBean taxonomy (cleaned), falling back to HanziStroke for the 6 entries MandarinBean leaves untagged (不客气, 打电话, 没关系, 你好, 下雨, 做饭 → phrase/interjection/verb).
7. **Deck safeguards**: all 300 Meaning+POS production prompts are unique; the builder rejects duplicates and missing/invalid audio instead of silently shipping broken cards. Filtering tags preserve compound labels such as `pos::number-classifier` for 一下, 一点儿, and 一些.
8. **Manual audio playback**: the deck uses its own "HSK 3.0 · manual audio" options group with `autoplay: false`. Audio is not played on card reveal; use Anki's clickable replay control (or R/F5) when desired. The default options group is unchanged.
9. **Answer-only part of speech**: recognition questions contain only hanzi and the optional audio control. Production questions contain only the English meaning. POS is shown only after revealing either answer.

## Exam list vs. standard (why 300 ≠ 500)

The standard GF0025-2021 defines Band 1 = 500 words; the **exam outline** tests 300. Membership check of our 300 against the standard's band-1 500 (via hsk30.csv, whose level distribution 500/772/973/1000/1071/1140/5,636 matches the official tables exactly):

- **253 / 300** are in the standard's band-1 list.
- **~9** (爸爸, 妈妈, 哥哥, 弟弟, 姐姐, 妹妹, 们, 第, 零) are not listed as words in the standard's tables at all — kinship reduplications and grammatical suffixes the standard handles as grammar/character-level items.
- **~16** appear in a different form (玩 ↔ 玩儿 erhua orthography; compounds like 不要, 这个, 你好 treated as component + grammar pattern).
- **~22** are words the standard places at band 2–4 (超市 L2, 出租车 L2, 猫 L2, 苹果 L3, 些 L4, …) that the exam outline pulls down into its HSK 1 test list.

This misalignment between exam syllabus and proficiency standard is a documented feature of the 2021 transition — it is not an error in either source.

## Known limitations

- Audio is macOS TTS (Tingting, zh_CN), not native-human recordings. Quality is decent but robotic; for human audio, see Forvo or official HSK audio.
- 一点儿/有点儿 audio: Tingting pronounces erhua naturally.
- Translations for polysemous words are scoped to HSK-1 usage (see overrides above).
