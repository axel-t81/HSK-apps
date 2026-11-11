"""Export the curated HSK 1 dataset as CSV.

Run from the project directory with -I:  python -I scripts/export_csv.py
Output: out/hsk1_300.csv
"""
import csv
import json
import os


def main():
    with open("data/final.json", encoding="utf-8") as f:
        records = json.load(f)

    os.makedirs("out", exist_ok=True)
    with open("out/hsk1_300.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["no", "simplified", "pinyin", "pinyin_plain",
                         "pinyin_num", "pos", "meaning", "alt_readings"])
        for r in records:
            writer.writerow([r["no"], r["simplified"], r["pinyin"],
                             r["pinyin_plain"], r["pinyin_num"], r["pos"],
                             r["meaning"], r["alts"]])
    print(f"CSV: out/hsk1_300.csv | rows: {len(records)}")


if __name__ == "__main__":
    main()
