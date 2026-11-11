"""Extract HSK 1 vocabulary tables from the two source PDFs (line-based assembly).

Usage (run with -I, paths as args):
  python -I scripts/extract.py mb data/raw/mandarinbean/New-HSK-Vocabulary-Level-1.pdf data/mb.json
  python -I scripts/extract.py hs data/raw/hanzistroke/HSK1-Vocabulary-3.0.pdf data/hs.json
"""
import json
import re
import sys

import pymupdf

MB_PAGE_FIRST, MB_PAGE_LAST = 2, 15   # 1-based table pages
HS_PAGE_FIRST, HS_PAGE_LAST = 2, 15


def lines_of_bucket(bucket):
    """Cluster bucket words into visual lines: sort by y, split on >4px gaps."""
    ws = sorted(bucket, key=lambda w: (w[1], w[0]))
    lines, cur, cur_y = [], [], None
    for w in ws:
        if cur_y is None or w[1] - cur_y <= 4:
            cur.append(w)
            cur_y = w[1] if cur_y is None else cur_y
        else:
            lines.append(cur)
            cur, cur_y = [w], w[1]
    if cur:
        lines.append(cur)
    return [[sorted(l, key=lambda w: w[0]) for l in [line]][0] for line in lines]


def extract_mb(path):
    doc = pymupdf.open(path)
    entries = []
    for p in range(MB_PAGE_FIRST - 1, MB_PAGE_LAST):
        page = doc[p]
        words = [w for w in page.get_text("words") if w[1] < page.rect.height - 45]
        anchor_pred = lambda w: 35 <= w[0] < 60 and re.match(r"^\d+$", w[4])
        anchors = sorted([w for w in words if anchor_pred(w)], key=lambda w: w[1])
        if not anchors:
            continue
        buckets = {i: [] for i in range(len(anchors))}
        for w in words:
            if anchor_pred(w):
                continue
            cur = None
            for i, a in enumerate(anchors):
                if a[1] <= w[1] + 6:
                    cur = i
                else:
                    break
            if cur is not None:
                buckets[cur].append(w)
        for i, a in enumerate(anchors):
            lines = lines_of_bucket(buckets[i])
            no = int(a[4])
            c_word, c_pinyin, c_pos, c_mean = [], [], [], []
            for line in lines:
                wtxt = " ".join(w[4] for w in line if 90 <= w[0] < 140)
                ptxt = " ".join(w[4] for w in line if 170 <= w[0] < 265)
                postxt = " ".join(w[4] for w in line if 265 <= w[0] < 370)
                mtxt = " ".join(w[4] for w in line if w[0] >= 370)
                for lst, txt in ((c_word, wtxt), (c_pinyin, ptxt),
                                 (c_pos, postxt), (c_mean, mtxt)):
                    if txt:
                        lst.append(txt)
            entries.append({"no": no, "word": " ".join(c_word),
                            "pinyin": " ".join(c_pinyin), "pos": " ".join(c_pos),
                            "meaning": " ".join(c_mean)})
    return entries


def extract_hs(path):
    doc = pymupdf.open(path)
    entries = []
    for p in range(HS_PAGE_FIRST - 1, HS_PAGE_LAST):
        page = doc[p]
        words = [w for w in page.get_text("words") if w[1] < page.rect.height - 45]
        anchor_pred = lambda w: 75 <= w[0] < 130 and re.search(r"[\u4e00-\u9fff]", w[4])
        anchors = sorted([w for w in words if anchor_pred(w)], key=lambda w: w[1])
        # drop page-header row words ("Word/Pinyin/Definition" are Latin; the
        # anchor list only contains hanzi, so header is naturally excluded)
        if not anchors:
            continue
        buckets = {i: [] for i in range(len(anchors))}
        for w in words:
            if anchor_pred(w) or w[4] in ("Word", "Pinyin", "Definition"):
                continue
            cur = None
            for i, a in enumerate(anchors):
                if a[1] <= w[1] + 6:
                    cur = i
                else:
                    break
            if cur is not None:
                buckets[cur].append(w)
        for i, a in enumerate(anchors):
            lines = lines_of_bucket(buckets[i])
            for line in lines:
                ptxt = " ".join(w[4] for w in line if 160 <= w[0] < 285)
                mtxt = " ".join(w[4] for w in line if w[0] >= 285)
                if ptxt.startswith("also:"):
                    if entries:
                        entries[-1].setdefault("alts", []).append(
                            {"pinyin": ptxt[len("also:"):].strip(),
                             "meaning": mtxt.strip()})
                    continue
                if not entries or entries[-1].get("_closed"):
                    entries.append({"word": a[4], "pinyin": ptxt,
                                    "meaning": mtxt, "pos": "", "alts": []})
                else:
                    last = entries[-1]
                    if ptxt:
                        last["pinyin"] = (last["pinyin"] + " " + ptxt).strip()
                    if mtxt:
                        last["meaning"] = (last["meaning"] + " " + mtxt).strip()
            if entries:
                entries[-1]["_closed"] = True
    for e in entries:
        e.pop("_closed", None)
    return entries


def main():
    kind, path, out = sys.argv[1], sys.argv[2], sys.argv[3]
    data = extract_mb(path) if kind == "mb" else extract_hs(path)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"{kind}: {len(data)} entries -> {out}")


if __name__ == "__main__":
    main()
