"""Merge the two extractions into a draft dataset with a full diff report.

Run with -I:  python -I scripts/merge.py
Inputs:  data/mb.json, data/hs.json
Outputs: data/merged_draft.json, prints QA report.
"""
import json
import re
import unicodedata

TONE_MAP = str.maketrans(
    "āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ",
    "aaaaeeeeiiiioooouuuuvvvv")


def base(s):
    """De-space, strip tone marks, lowercase -> comparison key."""
    s = s.replace("ü", "v").replace(":", "")
    s = unicodedata.normalize("NFC", s)
    return s.translate(TONE_MAP).replace(" ", "").lower().replace("/", "")


def hs_tag_to_pos(tag):
    """'(v./adj.)' -> 'verb, adjective'"""
    tag = tag.strip().strip("()")
    mapping = {"v": "verb", "n": "noun", "adj": "adjective", "adv": "adverb",
               "num": "number", "mw": "classifier", "part": "particle",
               "pref": "prefix", "suf": "suffix", "pron": "pronoun",
               "prep": "preposition", "conj": "conjunction",
               "interj": "interjection", "phr": "phrase", "comp": "complement"}
    parts = [p.strip().strip(".") for p in tag.split("/")]
    out = []
    for p in parts:
        if p in mapping:
            out.append(mapping[p])
        else:
            out.append(p)
    return ", ".join(out)


def clean_mb_pos(pos):
    pos = pos.replace("numberclassifier", "number-classifier")  # lost hyphen in PDF
    pos = pos.replace("、", ", ").replace("，", ", ")
    pos = re.sub(r"[（(]\s*", " (", pos)
    pos = re.sub(r"\s*[)）]", ")", pos)
    pos = re.sub(r"\s+", " ", pos).strip(" ,")
    return pos


def strip_hs_tag(meaning):
    meaning = meaning.strip()
    m = re.match(r"^(\([^)]*\))\s*(.*)$", meaning)
    if m:
        return m.group(1), m.group(2).strip()
    return "", meaning.strip()


def main():
    mb = json.load(open("data/mb.json", encoding="utf-8"))
    hs = json.load(open("data/hs.json", encoding="utf-8"))
    assert len(mb) == 300 and len(hs) == 300

    records = []
    problems = []

    for i in range(300):
        m, h = mb[i], hs[i]
        word_m, word_h = m["word"], h["word"]
        if word_m != word_h:
            problems.append(f"WORD MISMATCH #{m['no']}: mb='{word_m}' hs='{word_h}'")

        pinyin_m, pinyin_h = m["pinyin"].strip(), h["pinyin"].strip()
        bm, bh = base(pinyin_m), base(pinyin_h)
        canonical = pinyin_h  # start from HanziStroke (syllable-spaced)
        if bm != bh:
            fixed = None
            # candidate 1: append 'r' to last syllable (erhua written fused)
            toks = pinyin_h.split()
            if toks and base(toks[-1] + "r") and base("".join(toks[:-1]) + toks[-1] + "r") == bm:
                fixed = " ".join(toks[:-1]) + " " + toks[-1] + "r"
            # candidate 2: drop trailing standalone er-syllable, append r
            if fixed is None and len(toks) >= 2 and re.match(r"^[eěéèè]r?$", toks[-1].lower()):
                cand = " ".join(toks[:-2]) + " " + toks[-2] + "r" if len(toks) > 2 else toks[0] + "r"
                if base(cand) == bm:
                    fixed = cand
            if fixed:
                problems.append(f"PINYIN FIXED #{m['no']} {word_m}: hs='{pinyin_h}' mb='{pinyin_m}' -> '{fixed}'")
                canonical = fixed
            else:
                problems.append(f"PINYIN UNRESOLVED #{m['no']} {word_m}: hs='{pinyin_h}' mb='{pinyin_m}'")

        pos_tag, hs_mean = strip_hs_tag(h["meaning"])
        pos_mb = clean_mb_pos(m["pos"])
        meaning_m = m["meaning"].strip()

        records.append({
            "no": m["no"], "simplified": word_m,
            "pinyin": canonical, "pinyin_mb": pinyin_m,
            "pos": pos_mb if pos_mb else hs_tag_to_pos(pos_tag),
            "pos_source": "mb" if pos_mb else "hs",
            "meaning": meaning_m, "meaning_hs": hs_mean,
            "alts": h.get("alts", []),
        })

    with open("data/merged_draft.json", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)

    print(f"records: {len(records)}")
    print(f"problems: {len(problems)}")
    for p in problems:
        print("  " + p)
    print("\n--- meaning diffs (mb | hs) ---")
    for r in records:
        if r["meaning"] != r["meaning_hs"]:
            print(f"#{r['no']:>3} {r['simplified']:<4} | MB: {r['meaning']} || HS: {r['meaning_hs']}")


if __name__ == "__main__":
    main()
