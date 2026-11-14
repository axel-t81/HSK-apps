import json
import os
import re
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

UTC = timezone.utc
CHINA_TZ = ZoneInfo("Asia/Shanghai")
TRACKING_PARAMETERS = {
    "from",
    "spm",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


def utc_now():
    return datetime.now(UTC)


def clean_text(value):
    if not value:
        return ""
    if "<" in value and ">" in value:
        value = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", value).strip()


def ensure_aware(value, default_timezone=CHINA_TZ):
    if value.tzinfo is None:
        return value.replace(tzinfo=default_timezone)
    return value


def parse_date(value, default_timezone=CHINA_TZ):
    """Parse dates found in feeds/listings, returning an aware datetime or None."""
    if isinstance(value, datetime):
        return ensure_aware(value, default_timezone)
    text = clean_text(value)
    if not text:
        return None

    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return ensure_aware(parsed, default_timezone)
    except (TypeError, ValueError, OverflowError):
        pass

    iso_candidate = text.replace("Z", "+00:00")
    try:
        return ensure_aware(datetime.fromisoformat(iso_candidate), default_timezone)
    except ValueError:
        pass

    patterns = [
        (r"(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?",
         "%Y-%m-%d"),
        (r"(?<!\d)(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日?(?:\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
         "%Y-%m-%d"),
    ]
    for pattern, _ in patterns:
        match = re.search(pattern, text)
        if match:
            year, month, day, hour, minute, second = match.groups()
            try:
                return datetime(
                    int(year), int(month), int(day), int(hour or 0), int(minute or 0),
                    int(second or 0), tzinfo=default_timezone,
                )
            except ValueError:
                continue

    english = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+(\d{1,2}),\s*(20\d{2})\b",
        text,
        flags=re.IGNORECASE,
    )
    if english:
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(english.group(0), fmt).replace(tzinfo=default_timezone)
            except ValueError:
                continue
    return None


def canonicalize_url(url):
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not hostname:
        raise ValueError("Only absolute HTTP(S) URLs are supported")
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        hostname = f"{hostname}:{port}"
    query = urlencode([
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMETERS and not key.lower().startswith("utm_")
    ])
    return urlunsplit((scheme, hostname, parts.path or "/", query, ""))


def host_allowed(url, allowed_hosts):
    try:
        parts = urlsplit(url)
        hostname = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not hostname:
        return False
    try:
        if port is not None and not (1 <= port <= 65535):
            return False
    except ValueError:
        return False
    return any(hostname == host.lower() or hostname.endswith("." + host.lower())
               for host in allowed_hosts)


def json_hash(value):
    import hashlib
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def atomic_write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
