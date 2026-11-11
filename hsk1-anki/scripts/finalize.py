"""Apply curation overrides, validate pinyin with pypinyin, write final dataset.

Run with -I:  python -I scripts/finalize.py
Inputs:  data/merged_draft.json
Outputs: data/final.json, prints QA report.
"""
import json
import re
import unicodedata

from pypinyin import pinyin, Style

# tone-marked vowels -> bare vowels (ü preserved)
STRIP_MAP = str.maketrans("āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ",
                          "aaaaeeeeiiiioooouuuuüüüü")


def base(s):
    s = unicodedata.normalize("NFC", s)
    return s.translate(STRIP_MAP).replace(" ", "").lower().replace("/", "").replace("’", "")


def is_erhua_fused(last_syl):
    """True for syllables like 'nǎr'/'wánr' (erhua fused), False for 'ér'."""
    b = base(last_syl)
    return b.endswith("r") and b != "er"


def strip_tones(syl):
    return unicodedata.normalize("NFC", syl).translate(STRIP_MAP)


TONE_MARKS = {
    **dict.fromkeys("āēīōūǖ", 1),
    **dict.fromkeys("áéíóúǘ", 2),
    **dict.fromkeys("ǎěǐǒǔǚ", 3),
    **dict.fromkeys("àèìòùǜ", 4),
}


def tone_form(s):
    """Normalize orthography while preserving tone marks."""
    return (unicodedata.normalize("NFC", s).replace(" ", "")
            .replace("'", "").replace("’", "").lower())


def tone_number(syl):
    """Convert one curated tone-marked syllable to numeric pinyin."""
    variants = []
    for variant in syl.split("/"):
        normalized = unicodedata.normalize("NFC", variant)
        tones = [TONE_MARKS[c] for c in normalized if c in TONE_MARKS]
        tone = tones[0] if tones else 0
        plain = normalized.translate(STRIP_MAP).lower().replace("ü", "v")
        if tone and is_erhua_fused(variant):
            plain = plain[:-1] + str(tone) + "r"
        elif tone:
            plain += str(tone)
        variants.append(plain)
    return "/".join(variants)


# --- curation: meaning overrides chosen by reviewing all 180 source diffs ---
MEANING_OVERRIDES = {
    33: "o'clock; point, dot; measure word for small amounts",
    46: "many, much; how many, how much",
    49: "two (used in counting and numbers)",
    67: "still; also",
    71: "good; well",
    76: "number; date; size",
    82: "can; to know how to",
    86: "home, family; measure word for families",
    89: "measure word (for clothing)",
    97: "to open; to switch on",
    99: "to see; to look at; to watch; to read",
    101: "to see; to catch sight of",
    110: "inside; in",
    111: "two (used before a measure word)",
    121: "it's nothing; it doesn't matter",
    134: "where (colloquial, erhua)",
    140: "there (colloquial, erhua)",
    150: "you (polite)",
    166: "excuse me, may I ask",
    172: "day; sun",
    178: "morning (before noon)",
    195: "to sleep; to go to bed",
    203: "they (male or mixed group)",
    204: "they (for inanimate things)",
    205: "they (female group)",
    207: "day; sky",
    215: "late; evening",
    218: "hello (used when answering the phone)",
    220: "question; problem",
    235: "little friend; child (friendly term)",
    239: "some",
    244: "Sunday (formal)",
    245: "Sunday (colloquial)",
    271: "to be at; in; at",
    275: "early morning",
    278: "to look for; to find",
    280: "this side; here",
    283: "here (colloquial, erhua)",
    290: "Chinese (written language)",
}

# pinyin fixes not auto-resolvable (verified by hand against both sources)
PINYIN_OVERRIDES = {
    153: "nǚ ér",  # MB wrote nǚ’ér (apostrophe variant); 女儿 is nǚ'ér, not erhua
}


def main():
    records = json.load(open("data/merged_draft.json", encoding="utf-8"))
    assert len(records) == 300

    mismatches, source_tone_mismatches = [], []
    final = []
    for r in records:
        no = r["no"]
        word = r["simplified"]
        py = PINYIN_OVERRIDES.get(no, r["pinyin"]).strip()
        if tone_form(py) != tone_form(r["pinyin_mb"]):
            source_tone_mismatches.append(
                f"#{no} {word}: final '{py}' vs source '{r['pinyin_mb']}'")

        # --- pypinyin validation (base-form compare; sandhi invisible here) ---
        chars = list(word)
        got = py.split()
        fused = (len(chars) >= 2 and chars[-1] == "儿" and got
                 and is_erhua_fused(got[-1]))
        if fused:
            chars = chars[:-1]              # 儿 fused into previous syllable
            got = got[:-1] + [got[-1][:-1]]  # strip fused 'r' for comparison
        expect = [p[0] for p in pinyin(chars, style=Style.TONE)]
        if len(expect) != len(got):
            mismatches.append(f"#{no} {word}: syllable count {len(got)} vs pypinyin {len(expect)}")
        else:
            for g, e in zip(got, expect):
                if base(g) != base(e):
                    mismatches.append(f"#{no} {word}: '{g}' vs pypinyin '{e}'")
        # polyphone whitelist: readings both source PDFs agree on, where
        # pypinyin's per-character default picks the other common reading
        POLYPHONE_OK = {"便宜", "睡觉", "谁"}
        if word in POLYPHONE_OK:
            mismatches = [m for m in mismatches if f"#{no} " not in m]

        # --- tone-number pinyin: preserve the curated reading exactly ---
        py_num = " ".join(tone_number(s) for s in py.split())

        # --- alt readings note ---
        alt_bits = []
        if word != "谁":  # main pinyin field already shows shéi/shuí
            for a in r.get("alts", []):
                if a["pinyin"]:
                    m = re.sub(r"^\([^)]*\)\s*", "", a["meaning"]).strip()
                    alt_bits.append(f"also read {a['pinyin']}" + (f" — {m}" if m else ""))

        final.append({
            "no": no,
            "simplified": word,
            "pinyin": py,
            "pinyin_plain": " ".join(strip_tones(s) for s in py.split()),
            "pinyin_num": py_num,
            "pos": r["pos"],
            "meaning": MEANING_OVERRIDES.get(no, r["meaning"]),
            "alts": "; ".join(alt_bits),
        })

    # duplicate simplified check (distinct readings would still be flagged)
    seen = {}
    for f in final:
        seen.setdefault(f["simplified"], []).append(f["no"])
    dups = {k: v for k, v in seen.items() if len(v) > 1}

    with open("data/final.json", "w", encoding="utf-8") as fo:
        json.dump(final, fo, ensure_ascii=False, indent=1)

    print(f"final: {len(final)} records -> data/final.json")
    print(f"pypinyin base-form mismatches: {len(mismatches)}")
    for m in mismatches:
        print("  " + m)
    print(f"tone-marked source mismatches: {len(source_tone_mismatches)}")
    for m in source_tone_mismatches:
        print("  " + m)
    print(f"duplicate simplified forms: {dups if dups else 'none'}")
    print("\n--- POS values used ---")
    from collections import Counter
    for pos, n in Counter(f["pos"] for f in final).most_common():
        print(f"  {n:>3}  {pos!r}")


if __name__ == "__main__":
    main()
