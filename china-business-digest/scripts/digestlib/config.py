import json
import re
from pathlib import Path

from .errors import ConfigurationError
from .models import Source
from .util import canonicalize_url, host_allowed

VALID_CATEGORIES = {"official", "professional_media", "practitioner_industry", "community"}


def load_configuration(path):
    with Path(path).open(encoding="utf-8") as source:
        data = json.load(source)
    if data.get("version") != 1:
        raise ConfigurationError("Unsupported sources.json version")
    keywords = data.get("keywords", {})
    if not keywords or not all(isinstance(values, list) for values in keywords.values()):
        raise ConfigurationError("sources.json must define keyword lists")

    sources = []
    identifiers = set()
    for raw in data.get("sources", []):
        if raw.get("enabled", True) is False:
            continue
        required = {"id", "name", "type", "url", "allowed_hosts", "category", "language"}
        missing = required.difference(raw)
        if missing:
            raise ConfigurationError(f"Source is missing fields: {', '.join(sorted(missing))}")
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,50}", raw["id"]):
            raise ConfigurationError(f"Invalid source id: {raw['id']!r}")
        if raw["id"] in identifiers:
            raise ConfigurationError(f"Duplicate source id: {raw['id']}")
        if raw["type"] not in ("rss", "html_listing"):
            raise ConfigurationError(f"Unsupported source type for {raw['id']}")
        if raw["category"] not in VALID_CATEGORIES:
            raise ConfigurationError(f"Invalid category for {raw['id']}")
        try:
            listing_url = canonicalize_url(raw["url"])
        except ValueError as error:
            raise ConfigurationError(f"Invalid URL for {raw['id']}: {error}") from error
        if not raw["allowed_hosts"] or not host_allowed(listing_url, raw["allowed_hosts"]):
            raise ConfigurationError(f"Listing host is not allowlisted for {raw['id']}")
        if raw["type"] == "html_listing" and not raw.get("item_selector"):
            raise ConfigurationError(f"HTML source {raw['id']} needs item_selector")
        if raw.get("url_pattern"):
            try:
                re.compile(raw["url_pattern"])
            except re.error as error:
                raise ConfigurationError(f"Invalid url_pattern for {raw['id']}: {error}") from error
        try:
            maximum = int(raw.get("max_candidates", 3))
        except (TypeError, ValueError) as error:
            raise ConfigurationError(f"max_candidates must be an integer for {raw['id']}") from error
        if not 1 <= maximum <= 10:
            raise ConfigurationError(f"max_candidates must be 1–10 for {raw['id']}")
        identifiers.add(raw["id"])
        sources.append(Source(
            id=raw["id"], name=raw["name"], type=raw["type"], url=listing_url,
            allowed_hosts=tuple(raw["allowed_hosts"]), category=raw["category"],
            language=raw["language"], item_selector=raw.get("item_selector", ""),
            link_selector=raw.get("link_selector", "a[href]"),
            url_pattern=raw.get("url_pattern", ""), url_include=raw.get("url_include", ""),
            detail_selectors=tuple(raw.get("detail_selectors", [])),
            title_excludes=tuple(raw.get("title_excludes", [])),
            fetch_detail=bool(raw.get("fetch_detail", False)), max_candidates=maximum,
        ))
    if not sources:
        raise ConfigurationError("No enabled sources configured")
    return sources, keywords
