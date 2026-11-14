import hashlib
from dataclasses import dataclass, field

from .util import canonicalize_url, clean_text, parse_date, utc_now


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    type: str
    url: str
    allowed_hosts: tuple
    category: str
    language: str
    item_selector: str = ""
    link_selector: str = "a[href]"
    url_pattern: str = ""
    url_include: str = ""
    detail_selectors: tuple = ()
    title_excludes: tuple = ()
    fetch_detail: bool = False
    max_candidates: int = 3


@dataclass
class Item:
    source_id: str
    source_name: str
    category: str
    language: str
    title: str
    url: str
    guid: str = ""
    published_at: object = None
    summary: str = ""
    content: str = ""
    content_basis: str = "listing_only"
    canonical_url: str = ""
    topics: list = field(default_factory=list)
    relevance: int = 0
    retrieved_at: object = field(default_factory=utc_now)

    def __post_init__(self):
        self.url = canonicalize_url(self.url)
        self.canonical_url = canonicalize_url(self.canonical_url or self.url)
        self.title = clean_text(self.title)
        self.summary = clean_text(self.summary)
        self.content = clean_text(self.content)
        self.published_at = parse_date(self.published_at) if self.published_at else None
        self.retrieved_at = parse_date(self.retrieved_at) if self.retrieved_at else utc_now()
        identity = self.guid or self.url
        self.key = hashlib.sha256(f"{self.source_id}\0{identity}".encode("utf-8")).hexdigest()
        self.id = "src_" + self.key[:14]

    @property
    def display_url(self):
        return self.canonical_url or self.url

    def to_dict(self, include_content=True):
        result = {
            "id": self.id,
            "key": self.key,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "category": self.category,
            "language": self.language,
            "title": self.title,
            "url": self.url,
            "display_url": self.display_url,
            "guid": self.guid,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "summary": self.summary,
            "content_basis": self.content_basis,
            "topics": list(self.topics),
            "relevance": self.relevance,
            "retrieved_at": self.retrieved_at.isoformat(),
        }
        if include_content:
            result["content"] = self.content
        return result

    @classmethod
    def from_dict(cls, value):
        return cls(
            source_id=value["source_id"],
            source_name=value["source_name"],
            category=value["category"],
            language=value["language"],
            title=value["title"],
            url=value["url"],
            canonical_url=value.get("canonical_url") or value.get("display_url") or value["url"],
            guid=value.get("guid", ""),
            published_at=value.get("published_at"),
            summary=value.get("summary", ""),
            content=value.get("content", ""),
            content_basis=value.get("content_basis", "listing_only"),
            topics=list(value.get("topics", [])),
            relevance=int(value.get("relevance", 0)),
            retrieved_at=value.get("retrieved_at"),
        )


@dataclass
class CollectionResult:
    items: list
    succeeded: list
    errors: dict
    warnings: list

    def to_dict(self):
        return {
            "items": [item.to_dict() for item in self.items],
            "succeeded": list(self.succeeded),
            "errors": dict(self.errors),
            "warnings": list(self.warnings),
        }
