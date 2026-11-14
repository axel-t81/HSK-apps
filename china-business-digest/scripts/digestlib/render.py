import html
import re
from dataclasses import dataclass

from .util import CHINA_TZ, clean_text

CATEGORY_LABELS = {
    "official": "Official / 官方",
    "professional_media": "Professional media / 专业媒体",
    "practitioner_industry": "Practitioner & industry / 从业者与行业",
    "community": "Community / 社区",
}
ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:[’'_-][A-Za-z0-9]+)*")
URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)

LABELS = {
    "en": {
        "header": "CHINA BUSINESS DIGEST",
        "developments": "Key developments",
        "connection": "Bookkeeping → tax → small business",
        "pulse": "Industry / practitioner pulse",
        "pulse_note": "These are selected practitioner/industry perspectives, not a measure of consensus.",
        "learning": "Learning resource",
        "empty": "No new material met the relevance and freshness checks. No older items have been recycled to fill space.",
    },
    "zh-Hans": {
        "header": "中国商业简报",
        "developments": "主要动态",
        "connection": "记账 → 税务 → 小微企业",
        "pulse": "行业 / 从业者观察",
        "pulse_note": "以上为精选行业或从业者观点，不代表普遍共识。",
        "learning": "学习资源",
        "empty": "今天没有符合相关性和时效性要求的新材料，未用旧闻填充篇幅。",
    },
}


@dataclass
class RenderedDigest:
    subject: str
    text: str
    html: str
    word_count: int


def visible_word_count(text):
    """Count English words only, excluding URLs; Chinese text has a separate budget."""
    without_urls = URL_PATTERN.sub(" ", text)
    return len(ENGLISH_WORD_PATTERN.findall(without_urls))


def empty_digest(language="en"):
    chinese = language == "zh-Hans"
    return {
        "headline": "今日来源中没有重大更新" if chinese else "No material updates in today’s sources",
        "developments": [],
        "pulse": [],
        "learning_resource": None,
    }


def _citations(source_ids, references):
    return " ".join(f"[{references[source_id]}]" for source_id in source_ids)


def _ordered_references(digest, items):
    by_id = {item.id: item for item in items}
    references = {}
    ordered = []

    def add(source_id):
        if source_id not in references:
            references[source_id] = len(references) + 1
            ordered.append(by_id[source_id])

    for development in digest["developments"]:
        for source_id in development["source_ids"]:
            add(source_id)
    for pulse in digest["pulse"]:
        for source_id in pulse["source_ids"]:
            add(source_id)
    if digest.get("learning_resource"):
        add(digest["learning_resource"]["source_id"])
    return references, ordered


def _citation_links(source_ids, items, references):
    return " ".join(
        f'<a href="{html.escape(items[source_id].display_url, quote=True)}" style="color:#b42318">[{references[source_id]}]</a>'
        for source_id in source_ids
    )


def _narrative(digest, items, run_date, language, references):
    labels = LABELS[language]
    by_id = {item.id: item for item in items}
    text = [f"{labels['header']} · {run_date}", digest["headline"], "", labels["developments"].upper() if language == "en" else labels["developments"]]
    body = [
        f'<section lang="{language}">',
        f'<p style="color:#536273;font-size:13px;letter-spacing:.08em">{html.escape(labels["header"])} · {html.escape(run_date)}</p>',
        f'<h1 style="font-size:25px;line-height:1.25;margin:8px 0 24px">{html.escape(digest["headline"])}</h1>',
        f'<h2 style="font-size:18px;border-top:2px solid #b42318;padding-top:16px">{html.escape(labels["developments"])}</h2>',
    ]
    if digest["developments"]:
        for number, development in enumerate(digest["developments"], 1):
            citation = _citations(development["source_ids"], references)
            text.extend([
                f"{number}. {development['title']} {citation}",
                development["summary"],
                f"{labels['connection']}: {development['connection']}",
                "",
            ])
            body.extend([
                f'<h3 style="font-size:16px;margin:20px 0 6px">{html.escape(development["title"])} '
                f'{_citation_links(development["source_ids"], by_id, references)}</h3>',
                f'<p>{html.escape(development["summary"])}</p>',
                f'<p><strong>{html.escape(labels["connection"])}:</strong> {html.escape(development["connection"])}</p>',
            ])
    else:
        text.extend([labels["empty"], ""])
        body.append(f'<p>{html.escape(labels["empty"])}</p>')

    if digest["pulse"]:
        text.append(labels["pulse"].upper() if language == "en" else labels["pulse"])
        body.append(f'<h2 style="font-size:18px;border-top:1px solid #d8dee6;padding-top:16px">{html.escape(labels["pulse"])}</h2><ul>')
        for entry in digest["pulse"]:
            text.append(f"• {entry['text']} {_citations(entry['source_ids'], references)}")
            body.append(f'<li>{html.escape(entry["text"])} {_citation_links(entry["source_ids"], by_id, references)}</li>')
        text.extend([labels["pulse_note"], ""])
        body.append(f'</ul><p style="font-size:13px;color:#536273">{html.escape(labels["pulse_note"])}</p>')

    learning = digest.get("learning_resource")
    if learning:
        text.extend([
            labels["learning"].upper() if language == "en" else labels["learning"],
            f"{learning['title']} [{references[learning['source_id']]}]",
            learning["description"],
            "",
        ])
        body.extend([
            f'<h2 style="font-size:18px;border-top:1px solid #d8dee6;padding-top:16px">{html.escape(labels["learning"])}</h2>',
            f'<p><a href="{html.escape(by_id[learning["source_id"]].display_url, quote=True)}" style="color:#b42318"><strong>{html.escape(learning["title"])}</strong></a> [{references[learning["source_id"]]}]</p>',
            f'<p>{html.escape(learning["description"])}</p>',
        ])
    body.append("</section>")
    return text, "".join(body)


def _shared_sections(ordered, references, notes, bilingual):
    text = []
    body = []
    if ordered:
        text.append("SOURCES / 来源" if bilingual else "SOURCES")
        body.append(f'<h2 style="font-size:18px;border-top:1px solid #d8dee6;padding-top:16px">{"SOURCES / 来源" if bilingual else "Sources"}</h2><ol style="padding-left:22px">')
        for item in ordered:
            number = references[item.id]
            date = item.published_at.astimezone(CHINA_TZ).date().isoformat() if item.published_at else "date not supplied"
            category = CATEGORY_LABELS[item.category] if bilingual else CATEGORY_LABELS[item.category].split(" / ")[0]
            text.extend([
                f"[{number}] {item.source_name} · {category} · {date}",
                item.title,
                item.display_url,
            ])
            body.append(
                f'<li value="{number}" style="margin-bottom:12px">'
                f'<a href="{html.escape(item.display_url, quote=True)}" style="color:#b42318">{html.escape(item.title)}</a><br>'
                f'<span style="font-size:13px;color:#536273">{html.escape(item.source_name)} · {html.escape(category)} · {date}</span></li>'
            )
        text.append("")
        body.append("</ol>")
    if notes:
        heading = "COLLECTION NOTES / 采集说明" if bilingual else "COLLECTION NOTES"
        text.append(heading)
        text.extend(f"• {note}" for note in notes)
        text.append("")
        body.append(f'<h2 style="font-size:16px;border-top:1px solid #d8dee6;padding-top:16px">{heading}</h2><ul>')
        body.extend(f'<li style="font-size:13px;color:#536273">{html.escape(note)}</li>' for note in notes)
        body.append("</ul>")
    disclaimer = "Educational summary only; verify current rules with official guidance. Not tax or legal advice."
    if bilingual:
        disclaimer += " 本简报仅供学习，请以官方现行规定为准，不构成税务或法律建议。"
    text.append(disclaimer)
    body.append(f'<p style="font-size:12px;color:#687687;border-top:1px solid #d8dee6;padding-top:16px">{html.escape(disclaimer)}</p>')
    return text, "".join(body)


def _document(sections):
    return "".join([
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>',
        "<body style=\"margin:0;background:#f5f6f8;color:#17212b;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;line-height:1.5\">",
        '<main style="max-width:680px;margin:0 auto;padding:28px 20px;background:#ffffff">',
        *sections,
        "</main></body></html>",
    ])


def render_digest(digest, items, run_date, notes=()):
    """Render the English-only form, also used to enforce the English word budget."""
    references, ordered = _ordered_references(digest, items)
    english_text, english_html = _narrative(digest, items, run_date, "en", references)
    shared_text, shared_html = _shared_sections(ordered, references, notes, bilingual=False)
    text_body = "\n".join(english_text + shared_text).strip() + "\n"
    headline = clean_text(digest["headline"])
    subject = f"[China Business Digest] {run_date}: {headline[:90]}"
    return RenderedDigest(subject, text_body, _document([english_html, shared_html]), visible_word_count(text_body))


def render_bilingual_digest(english, chinese, items, run_date, notes=()):
    references, ordered = _ordered_references(english, items)
    english_text, english_html = _narrative(english, items, run_date, "en", references)
    chinese_text, chinese_html = _narrative(chinese, items, run_date, "zh-Hans", references)
    shared_text, shared_html = _shared_sections(ordered, references, notes, bilingual=True)
    text_body = "\n".join(
        english_text + ["简体中文 / SIMPLIFIED CHINESE", ""] + chinese_text + shared_text
    ).strip() + "\n"
    english_budget_text = "\n".join(english_text + shared_text)
    headline = clean_text(english["headline"])
    subject = f"[China Business Digest 中国商业简报] {run_date}: {headline[:70]}"
    divider = '<hr style="border:0;border-top:3px solid #b42318;margin:32px 0">'
    return RenderedDigest(
        subject,
        text_body,
        _document([english_html, divider, chinese_html, shared_html]),
        visible_word_count(english_budget_text),
    )
