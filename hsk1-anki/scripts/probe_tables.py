"""Dump word coordinates from a table page so we can tune column thresholds."""
import sys
import pymupdf

path = sys.argv[1]
page_no = int(sys.argv[2])  # 1-based page number

doc = pymupdf.open(path)
page = doc[page_no - 1]
words = page.get_text("words")  # x0, y0, x1, y1, text, block, line, word_no
words.sort(key=lambda w: (round(w[1], 1), w[0]))
for w in words:
    x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
    print(f"y={y0:7.1f} x={x0:7.1f}-{x1:7.1f}  {text}")
