"""Build the Anki deck (.apkg) from data/final.json + audio/.

Run with -I:  python -I scripts/build_deck.py
Output: out/HSK_3.0_Band_1_300.apkg
"""
import json
import os

import genanki

MODEL_ID = 1784590032
DECK_ID = 2059400111
DCONF_ID = 1784590033
DECK_NAME = "HSK 3.0 · Band 1 (300 words)"

CSS = """
.card { font-family: -apple-system, 'PingFang SC', 'Hiragino Sans GB', sans-serif;
        font-size: 22px; text-align: center; color: #1a1a1a;
        background-color: #f7f5f0; }
.card.night_mode { color: #e8e6e0; background-color: #1e1e22; }
.hanzi { font-size: 64px; line-height: 1.3; }
.hanzi-sm { font-size: 44px; }
.pinyin { font-size: 28px; color: #b3452c; margin-top: 8px; }
.card.night_mode .pinyin { color: #e07a5f; }
.meaning { font-size: 24px; margin-top: 12px; }
.pos { font-size: 16px; color: #888; margin-top: 10px; font-style: italic; }
.alt { font-size: 15px; color: #777; margin-top: 14px; }
.num { font-size: 13px; color: #aaa; margin-top: 20px; }
hr#answer { border: none; border-top: 2px solid #d8d4c8; margin: 18px 0; }
"""

RECOG_TMPL = {
    "name": "Recognition (Hanzi → Meaning)",
    "qfmt": '<div class="hanzi">{{Simplified}}</div>\n{{Audio}}',
    "afmt": '{{FrontSide}}\n<hr id=answer>\n'
            '<div class="pos">{{POS}}</div>\n'
            '<div class="pinyin">{{Pinyin}}</div>\n'
            '<div class="meaning">{{Meaning}}</div>\n'
            '{{#Alt}}<div class="alt">{{Alt}}</div>{{/Alt}}\n'
            '<div class="num">#{{No}}</div>',
}
PROD_TMPL = {
    "name": "Production (Meaning → Hanzi)",
    "qfmt": '<div class="meaning">{{Meaning}}</div>',
    "afmt": '{{FrontSide}}\n<hr id=answer>\n'
            '<div class="hanzi-sm">{{Simplified}}</div>\n'
            '<div class="pinyin">{{Pinyin}} <span style="font-size:16px;color:#999">{{PinyinPlain}}</span></div>\n'
            '<div class="pos">{{POS}}</div>\n'
            '{{Audio}}\n'
            '{{#Alt}}<div class="alt">{{Alt}}</div>{{/Alt}}\n'
            '<div class="num">#{{No}}</div>',
}

MODEL = genanki.Model(
    MODEL_ID, "HSK 3.0 Band 1 (zh → en, both directions)",
    fields=[{"name": n} for n in ["No", "Simplified", "Pinyin", "PinyinPlain",
                                  "PinyinNum", "POS", "Meaning", "Alt", "Audio"]],
    templates=[RECOG_TMPL, PROD_TMPL],
    css=CSS,
)


class ManualAudioPackage(genanki.Package):
    """Package with a deck-specific options group that disables autoplay."""

    def write_to_db(self, cursor, timestamp, id_gen):
        super().write_to_db(cursor, timestamp, id_gen)
        dconf_json, decks_json = cursor.execute(
            "SELECT dconf, decks FROM col").fetchone()
        dconf = json.loads(dconf_json)
        decks = json.loads(decks_json)

        manual = dict(dconf["1"])
        manual.update(id=DCONF_ID, name="HSK 3.0 · manual audio",
                      autoplay=False, mod=int(timestamp))
        dconf[str(DCONF_ID)] = manual
        decks[str(DECK_ID)]["conf"] = DCONF_ID

        cursor.execute("UPDATE col SET dconf = ?, decks = ?",
                       (json.dumps(dconf, ensure_ascii=False),
                        json.dumps(decks, ensure_ascii=False)))


def main():
    records = json.load(open("data/final.json", encoding="utf-8"))
    media = []
    for r in records:
        audio_file = f"audio/hsk1_{r['no']:03d}.m4a"
        if not os.path.isfile(audio_file) or os.path.getsize(audio_file) <= 1000:
            raise FileNotFoundError(f"missing or invalid audio: {audio_file}")
        media.append(audio_file)

    deck = genanki.Deck(DECK_ID, DECK_NAME)
    production_prompts = {}
    for r in records:
        prompt = (r["meaning"], r["pos"])
        if prompt in production_prompts:
            other = production_prompts[prompt]
            raise ValueError(
                f"ambiguous production prompt for {other} and {r['simplified']}: {prompt}")
        production_prompts[prompt] = r["simplified"]

        audio_ref = f"[sound:hsk1_{r['no']:03d}.m4a]"
        pos = r["pos"].split(",")[0].split(" (")[0].strip().lower()
        pos = pos.replace(".", "") or "other"
        if pos not in {"noun", "verb", "adjective", "adverb", "pronoun",
                       "number", "number-classifier", "classifier", "particle",
                       "preposition", "conjunction", "prefix", "suffix",
                       "auxiliary", "interjection", "phrase"}:
            pos = "other"
        note = genanki.Note(
            model=MODEL,
            fields=[f"{r['no']:03d}", r["simplified"], r["pinyin"],
                    r["pinyin_plain"], r["pinyin_num"], r["pos"], r["meaning"],
                    r["alts"], audio_ref],
            guid=genanki.guid_for(r["simplified"], r["pinyin"]),
            tags=[f"HSK3.0::band-1", f"pos::{pos}"],
        )
        deck.add_note(note)

    os.makedirs("out", exist_ok=True)
    pkg = ManualAudioPackage(deck)
    pkg.media_files = media
    out = "out/HSK_3.0_Band_1_300.apkg"
    pkg.write_to_file(out)
    print(f"deck: {out} | notes: {len(records)} | media: {len(media)}")
    print(f"size: {os.path.getsize(out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
