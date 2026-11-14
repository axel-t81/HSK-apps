import calendar
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import feedparser
from bs4 import BeautifulSoup

from .errors import FetchError, SourceError
from .models import CollectionResult, Item
from .util import CHINA_TZ, UTC, canonicalize_url, clean_text, host_allowed, parse_date, utc_now

MIN_DETAIL_CHARACTERS = 200
MAX_DETAIL_CHARACTERS = 40_000
REMOVE_SELECTORS = (
    "script", "style", "noscript", "svg", "form", "nav", "header", "footer", "aside",
    ".sharedaddy", ".jp-relatedposts", ".related-posts", ".newsletter", ".comments",
)


def _rss_date(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime.fromtimestamp(calendar.timegm(parsed), UTC)
        except (TypeError, ValueError, OverflowError):
            pass
    return parse_date(entry.get("published") or entry.get("updated"))


def parse_rss(source, body, retrieved_at=None):
    feed = feedparser.parse(body)
    items = []
    seen = set()
    for entry in feed.entries:
        link = entry.get("link", "")
        try:
            url = canonicalize_url(link)
        except ValueError:
            continue
        if not host_allowed(url, source.allowed_hosts):
            continue
        title = clean_text(entry.get("title", ""))
        if len(title) < 4:
            continue
        item = Item(
            source_id=source.id, source_name=source.name, category=source.category,
            language=source.language, title=title, url=url,
            guid=str(entry.get("id") or url), published_at=_rss_date(entry),
            summary=clean_text(entry.get("summary") or entry.get("description") or ""),
            content_basis="feed_summary", retrieved_at=retrieved_at or utc_now(),
        )
        if item.key not in seen:
            seen.add(item.key)
            items.append(item)
    if not items:
        detail = f": {feed.bozo_exception}" if feed.bozo else ""
        raise SourceError(f"No usable RSS items from {source.name}{detail}")
    return items


def _listing_link(node, source, base_url):
    anchor = node.select_one(source.link_selector)
    if not anchor or not anchor.get("href"):
        return None
    title = clean_text(anchor.get_text(" ", strip=True) or anchor.get("title", ""))
    if len(title) < 4:
        return None
    try:
        url = canonicalize_url(urljoin(base_url, anchor["href"]))
    except (ValueError, TypeError):
        return None
    if not host_allowed(url, source.allowed_hosts):
        return None
    path = urlsplit(url).path
    if source.url_pattern and not re.search(source.url_pattern, path):
        return None
    if source.url_include and source.url_include not in path:
        return None
    if any(part in path for part in ("/category/", "/tag/", "/author/", "/page/")):
        return None
    return title, url


def parse_html_listing(source, body, retrieved_at=None, base_url=None):
    soup = BeautifulSoup(body, "html.parser")
    base_url = base_url or source.url
    items = []
    seen = set()
    for node in soup.select(source.item_selector):
        found = _listing_link(node, source, base_url)
        if not found:
            continue
        title, url = found
        time_element = node.select_one("time[datetime]")
        published = None
        if time_element:
            published = parse_date(time_element.get("datetime")) or parse_date(time_element.get_text(" "))
        if not published:
            published = parse_date(node.get_text(" ", strip=True))
        paragraph = node.select_one("p")
        summary = clean_text(paragraph.get_text(" ", strip=True) if paragraph else node.get_text(" ", strip=True))
        item = Item(
            source_id=source.id, source_name=source.name, category=source.category,
            language=source.language, title=title, url=url, published_at=published,
            summary=summary, content_basis="listing_summary" if summary else "listing_only",
            retrieved_at=retrieved_at or utc_now(),
        )
        if item.key not in seen:
            seen.add(item.key)
            items.append(item)
    if not items:
        raise SourceError(
            f"No dated article links matched {source.name}; the listing layout may have changed"
        )
    return items


def classify(item, keywords):
    text = f"{item.title}\n{item.summary}\n{item.content}".casefold()
    topics = []
    relevance = 0
    for topic, terms in keywords.items():
        matches = sum(1 for term in terms if term.casefold() in text)
        if matches:
            topics.append(topic)
            relevance += 3 + min(matches, 4)
    item.topics = topics
    item.relevance = relevance
    return item


def _detail_date(soup):
    for element in soup.select("time[datetime], time, .post-date, .published"):
        parsed = parse_date(element.get("datetime") if element.has_attr("datetime") else element.get_text(" "))
        if parsed:
            return parsed
    return None


def extract_detail(source, item, body, final_url):
    soup = BeautifulSoup(body, "html.parser")
    canonical = soup.select_one('link[rel="canonical"][href]')
    if canonical:
        candidate = urljoin(final_url, canonical.get("href", ""))
        try:
            if host_allowed(candidate, source.allowed_hosts):
                item.canonical_url = canonicalize_url(candidate)
        except ValueError:
            pass
    published = _detail_date(soup)
    if published:
        item.published_at = published
    description = soup.select_one('meta[name="description"][content]')
    if description and not item.summary:
        item.summary = clean_text(description.get("content"))

    for element in soup.select(", ".join(REMOVE_SELECTORS)):
        element.decompose()
    selectors = source.detail_selectors or ("article", "main")
    for selector in selectors:
        candidates = []
        for element in soup.select(selector):
            text = clean_text(element.get_text(" ", strip=True))
            if len(text) >= MIN_DETAIL_CHARACTERS:
                candidates.append(text)
        if candidates:
            item.content = max(candidates, key=len)[:MAX_DETAIL_CHARACTERS]
            item.content_basis = "full_text"
            return item
    if not item.content:
        raise SourceError(f"No article body matched the configured selectors for {source.name}")
    return item


def _sort_key(item):
    timestamp = item.published_at or datetime.min.replace(tzinfo=CHINA_TZ)
    return item.relevance, timestamp.timestamp()


def collect_sources(sources, keywords, state, fetcher, cutoff, max_items=12, now=None):
    now = now or utc_now()
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    shortlisted = []
    succeeded = []
    errors = {}
    warnings = []

    for source in sources:
        try:
            response = fetcher.fetch(source.url, source.allowed_hosts)
            if source.type == "rss":
                parsed = parse_rss(source, response.body, now)
            else:
                parsed = parse_html_listing(source, response.body, now, response.url)
            succeeded.append(source.id)
        except (FetchError, SourceError, ValueError) as error:
            errors[source.id] = str(error)
            continue

        fresh = []
        undated = irrelevant = 0
        for item in parsed:
            if state.is_processed(item.key):
                continue
            if any(term.casefold() in item.title.casefold() for term in source.title_excludes):
                irrelevant += 1
                continue
            if not item.published_at:
                undated += 1
                continue
            if item.published_at.astimezone(timezone.utc) < cutoff.astimezone(timezone.utc):
                continue
            cached = state.get_item(item.key)
            if cached:
                previous = cached["item"]
                if previous.content_basis == "full_text":
                    item.content = previous.content
                    item.content_basis = previous.content_basis
                    item.canonical_url = previous.canonical_url
                    item.summary = item.summary or previous.summary
            classify(item, keywords)
            if not item.topics:
                irrelevant += 1
                continue
            fresh.append(item)
        if undated:
            warnings.append(f"{source.name}: skipped {undated} item(s) without a reliable publication date.")
        if irrelevant:
            warnings.append(f"{source.name}: skipped {irrelevant} item(s) without a relevant bookkeeping, small-business, or tax keyword.")
        fresh.sort(key=_sort_key, reverse=True)
        shortlisted.extend((source, item) for item in fresh[:source.max_candidates])

    shortlisted.sort(key=lambda pair: _sort_key(pair[1]), reverse=True)
    selected = shortlisted[:max_items]
    result_items = []
    for source, item in selected:
        if source.fetch_detail and item.content_basis != "full_text":
            try:
                response = fetcher.fetch(item.url, source.allowed_hosts)
                item = extract_detail(source, item, response.body, response.url)
            except (FetchError, SourceError, ValueError) as error:
                warnings.append(f"{source.name}: detail unavailable for {item.title}: {error}")
                item.content = item.summary
                item.content_basis = "listing_summary" if item.summary else "listing_only"
        if not item.published_at or item.published_at.astimezone(timezone.utc) < cutoff.astimezone(timezone.utc):
            warnings.append(f"{source.name}: excluded {item.title} after detail-date verification.")
            continue
        classify(item, keywords)
        if not item.topics:
            warnings.append(f"{source.name}: excluded {item.title} after full-text relevance verification.")
            continue
        state.upsert_item(item)
        result_items.append(item)
    return CollectionResult(result_items, succeeded, errors, warnings)
