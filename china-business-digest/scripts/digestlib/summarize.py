import json
import re
from collections import Counter

from .errors import SummaryError
from .render import empty_digest, render_bilingual_digest, render_digest
from .summarize_client import SYSTEM_PROMPT, TRANSLATION_PROMPT

MAX_BODY_WORDS = 600
MAX_ITEM_CHARACTERS = 6_000


def _plain_text(value, field, minimum, maximum):
    if not isinstance(value, str):
        raise SummaryError(f"{field} must be text")
    value = " ".join(value.split())
    if not minimum <= len(value) <= maximum:
        raise SummaryError(f"{field} has an invalid length")
    if "http://" in value.lower() or "https://" in value.lower():
        raise SummaryError(f"{field} contains an unapproved URL")
    return value


def _source_ids(value, valid_ids, field):
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise SummaryError(f"{field} must contain 1–3 source IDs")
    if any(not isinstance(source_id, str) for source_id in value):
        raise SummaryError(f"{field} contains a non-text source ID")
    if len(set(value)) != len(value) or any(source_id not in valid_ids for source_id in value):
        raise SummaryError(f"{field} contains an unknown or duplicate source ID")
    return list(value)


def validate_digest(value, items):
    if not isinstance(value, dict):
        raise SummaryError("Digest JSON is not an object")
    expected = {"headline", "developments", "pulse", "learning_resource"}
    if set(value) != expected:
        raise SummaryError("Digest JSON does not match the required shape")
    by_id = {item.id: item for item in items}
    digest = {"headline": _plain_text(value["headline"], "headline", 4, 140)}

    raw_developments = value["developments"]
    if not isinstance(raw_developments, list) or not 1 <= len(raw_developments) <= 5:
        raise SummaryError("developments must contain 1–5 entries")
    developments = []
    for index, raw in enumerate(raw_developments):
        field = f"developments[{index}]"
        if not isinstance(raw, dict) or set(raw) != {"title", "summary", "connection", "source_ids"}:
            raise SummaryError(f"{field} has an invalid shape")
        developments.append({
            "title": _plain_text(raw["title"], field + ".title", 3, 140),
            "summary": _plain_text(raw["summary"], field + ".summary", 10, 800),
            "connection": _plain_text(raw["connection"], field + ".connection", 10, 600),
            "source_ids": _source_ids(raw["source_ids"], by_id, field + ".source_ids"),
        })
    digest["developments"] = developments

    raw_pulse = value["pulse"]
    if not isinstance(raw_pulse, list) or len(raw_pulse) > 2:
        raise SummaryError("pulse must contain at most 2 entries")
    pulse = []
    for index, raw in enumerate(raw_pulse):
        field = f"pulse[{index}]"
        if not isinstance(raw, dict) or set(raw) != {"text", "source_ids"}:
            raise SummaryError(f"{field} has an invalid shape")
        ids = _source_ids(raw["source_ids"], by_id, field + ".source_ids")
        if any(by_id[source_id].category not in ("practitioner_industry", "community") for source_id in ids):
            raise SummaryError(f"{field} cites a non-practitioner source")
        pulse.append({
            "text": _plain_text(raw["text"], field + ".text", 10, 700),
            "source_ids": ids,
        })
    digest["pulse"] = pulse

    learning = value["learning_resource"]
    if learning is not None:
        if not isinstance(learning, dict) or set(learning) != {"title", "description", "source_id"}:
            raise SummaryError("learning_resource has an invalid shape")
        if not isinstance(learning["source_id"], str) or learning["source_id"] not in by_id:
            raise SummaryError("learning_resource contains an unknown source ID")
        learning = {
            "title": _plain_text(learning["title"], "learning_resource.title", 3, 160),
            "description": _plain_text(
                learning["description"], "learning_resource.description", 10, 500
            ),
            "source_id": learning["source_id"],
        }
    digest["learning_resource"] = learning
    return digest


def decode_json_object(raw):
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    if start < 0:
        raise SummaryError("Model response did not contain a JSON object")
    try:
        value, end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as error:
        raise SummaryError("Model response was not valid JSON") from error
    if text[start + end:].strip():
        raise SummaryError("Model response contained content after the JSON object")
    return value


FOCUS_TERMS = (
    "代理记账", "中小微", "小微企业", "中小企业", "小规模纳税人", "个体工商户",
    "会计服务业", "财税服务", "数电发票", "电子税务局", "纳税申报", "汇算清缴",
    "人工智能", "内卷式竞争", "bookkeeping", "small business", "small-scale taxpayer",
    "fapiao", "VAT",
)


def _keyword_excerpt(text, limit=MAX_ITEM_CHARACTERS):
    """Retain the introduction and merged windows around market-specific terms."""
    if len(text) <= limit:
        return text
    intervals = []
    for term in FOCUS_TERMS:
        for count, match in enumerate(re.finditer(re.escape(term), text, re.IGNORECASE)):
            if count >= 4:
                break
            index = match.start()
            intervals.append((max(0, index - 450), min(len(text), index + len(term) + 750)))
    if not intervals:
        return text[:limit]

    intervals.sort()
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))

    head_length = min(800, limit // 4)
    chunks = [text[:head_length]]
    used = head_length
    for start, end in merged:
        if end <= head_length:
            continue
        start = max(start, head_length)
        marker = "\n[…keyword-focused excerpt…]\n"
        available = limit - used - len(marker)
        if available <= 0:
            break
        chunk = text[start:min(end, start + available)]
        chunks.extend([marker, chunk])
        used += len(marker) + len(chunk)
    return "".join(chunks)[:limit]


def _model_items(items):
    return [{
        "id": item.id,
        "source": item.source_name,
        "source_category": item.category,
        "language": item.language,
        "title": item.title,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "content_basis": item.content_basis,
        "topics": item.topics,
        "listing_summary": item.summary[:1_500],
        "source_text": _keyword_excerpt(item.content or item.summary),
        "source_text_note": (
            "Long documents are shortened with keyword-focused excerpts; do not infer omitted content."
            if len(item.content or item.summary) > MAX_ITEM_CHARACTERS
            else "Complete extracted source text or listing metadata is supplied."
        ),
    } for item in items]


def _digest_values(digest):
    values = [digest["headline"]]
    for development in digest["developments"]:
        values.extend([development["title"], development["summary"], development["connection"]])
    values.extend(entry["text"] for entry in digest["pulse"])
    if digest["learning_resource"]:
        values.extend([
            digest["learning_resource"]["title"], digest["learning_resource"]["description"],
        ])
    return values


def _aligned_text_pairs(english, chinese):
    pairs = [("headline", english["headline"], chinese["headline"])]
    for index, (original, translated) in enumerate(zip(english["developments"], chinese["developments"])):
        for field in ("title", "summary", "connection"):
            pairs.append((f"developments[{index}].{field}", original[field], translated[field]))
    for index, (original, translated) in enumerate(zip(english["pulse"], chinese["pulse"])):
        pairs.append((f"pulse[{index}].text", original["text"], translated["text"]))
    if english["learning_resource"] and chinese["learning_resource"]:
        for field in ("title", "description"):
            pairs.append((
                f"learning_resource.{field}",
                english["learning_resource"][field], chinese["learning_resource"][field],
            ))
    return pairs


def _latin_count(text):
    return len(re.findall(r"[A-Za-z]+(?:[’'_-][A-Za-z]+)*", text))


def _han_count(text):
    return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))


def validate_english_language(digest):
    for text in _digest_values(digest):
        latin = _latin_count(text)
        if latin < 2 or _han_count(text) > max(4, latin):
            raise SummaryError("English digest contains a non-English or mostly Chinese text value")


def _numbers(text):
    return re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))


def _validate_caveat(field, english, chinese):
    checks = [
        (r"\b(?:not|no|never)\b", r"[不无非未]", "negation"),
        (r"\bonly\b", r"[仅只]", "only/just limitation"),
        (r"\b(?:may|might|could|possibly)\b", r"(?:可能|或许|或可|也许|未必)", "uncertainty"),
        (r"\bconsensus\b", r"共识", "consensus caveat"),
    ]
    for pattern, chinese_pattern, label in checks:
        if re.search(pattern, english, re.IGNORECASE) and not re.search(chinese_pattern, chinese):
            raise SummaryError(f"Chinese translation may have dropped {label} in {field}")


def validate_translation(english, value, items):
    chinese = validate_digest(value, items)
    if len(chinese["developments"]) != len(english["developments"]):
        raise SummaryError("Chinese translation changed the number of developments")
    for translated, original in zip(chinese["developments"], english["developments"]):
        if translated["source_ids"] != original["source_ids"]:
            raise SummaryError("Chinese translation changed development citations")
    if len(chinese["pulse"]) != len(english["pulse"]):
        raise SummaryError("Chinese translation changed the pulse section")
    for translated, original in zip(chinese["pulse"], english["pulse"]):
        if translated["source_ids"] != original["source_ids"]:
            raise SummaryError("Chinese translation changed pulse citations")
    translated_learning = chinese["learning_resource"]
    original_learning = english["learning_resource"]
    if bool(translated_learning) != bool(original_learning):
        raise SummaryError("Chinese translation changed the learning-resource selection")
    if translated_learning and translated_learning["source_id"] != original_learning["source_id"]:
        raise SummaryError("Chinese translation changed the learning-resource citation")

    for field, original, translated in _aligned_text_pairs(english, chinese):
        if Counter(_numbers(original)) != Counter(_numbers(translated)):
            raise SummaryError(f"Chinese translation changed numbers in {field}")
        if _han_count(translated) < max(2, _latin_count(translated)):
            raise SummaryError(f"Chinese translation is mostly untranslated English in {field}")
        _validate_caveat(field, original, translated)
    return chinese


def _english_digest(items, run_date, notes, client):
    request = json.dumps({
        "run_date": run_date,
        "task": "Select and summarise the most useful new material for the daily learning digest.",
        "items": _model_items(items),
    }, ensure_ascii=False)
    cache_key, raw = client.generate(SYSTEM_PROMPT, request, "initial-digest")
    last_problem = None
    for attempt in range(2):
        try:
            digest = validate_digest(decode_json_object(raw), items)
            validate_english_language(digest)
            rendered = render_digest(digest, items, run_date, notes)
            if rendered.word_count > MAX_BODY_WORDS:
                raise SummaryError(
                    f"English email was {rendered.word_count} words; shorten it to at most 520 English words"
                )
            client.commit(cache_key, raw)
            return digest
        except SummaryError as error:
            last_problem = error
            if attempt == 1:
                break
            correction = json.dumps({
                "task": "Repair the previous English digest. Return only a complete corrected JSON object.",
                "problem": str(error),
                "requirements": "Keep only grounded facts and valid supplied source IDs; no URLs in values.",
                "original_request": request,
                "previous_model_output": raw,
            }, ensure_ascii=False)
            cache_key, raw = client.generate(SYSTEM_PROMPT, correction, "digest-repair")
    raise SummaryError(f"Unable to produce valid English after one correction: {last_problem}")


def _chinese_translation(english, items, client):
    request = json.dumps({
        "task": "Translate this validated English digest into Simplified Chinese without changing citations.",
        "english_digest": english,
    }, ensure_ascii=False)
    cache_key, raw = client.generate(TRANSLATION_PROMPT, request, "simplified-chinese-translation")
    last_problem = None
    for attempt in range(2):
        try:
            chinese = validate_translation(english, decode_json_object(raw), items)
            client.commit(cache_key, raw)
            return chinese
        except SummaryError as error:
            last_problem = error
            if attempt == 1:
                break
            correction = json.dumps({
                "task": "Repair the Simplified Chinese translation. Return only complete corrected JSON.",
                "problem": str(error),
                "original_validated_english": english,
                "previous_translation_output": raw,
            }, ensure_ascii=False)
            cache_key, raw = client.generate(TRANSLATION_PROMPT, correction, "simplified-chinese-translation-repair")
    raise SummaryError(f"Unable to produce aligned Simplified Chinese after one correction: {last_problem}")


def create_digest(items, run_date, notes, client):
    if not items:
        english = empty_digest("en")
        chinese = empty_digest("zh-Hans")
    else:
        english = _english_digest(items, run_date, notes, client)
        chinese = _chinese_translation(english, items, client)
    rendered = render_bilingual_digest(english, chinese, items, run_date, notes)
    if rendered.word_count > MAX_BODY_WORDS:
        raise SummaryError(f"Bilingual email exceeded the {MAX_BODY_WORDS}-word English limit")
    return {"en": english, "zh-Hans": chinese}, rendered
