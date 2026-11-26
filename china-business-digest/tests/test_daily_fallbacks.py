import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from digestlib import cli
from digestlib.collect import collect_sources
from digestlib.errors import FetchError, SummaryError
from digestlib.http import FetchResponse
from digestlib.models import CollectionResult, Item, Source
from digestlib.render import render_bilingual_digest
from digestlib.state import State
from digestlib.summarize import create_digest, validate_digest, validate_translation
from digestlib.util import CHINA_TZ

UTC = timezone.utc
NOW = datetime(2026, 11, 14, 1, 0, tzinfo=UTC)
FRESH_CUTOFF = NOW - timedelta(days=1)
FALLBACK_CUTOFF = NOW - timedelta(days=7)
SOURCE_URL = "https://example.cn/list/"
KEYWORDS = {
    "bookkeeping": ["代理记账"],
    "small_business": ["小规模纳税人"],
    "tax": ["纳税申报"],
}
RELEVANT_DETAIL = "代理记账机构应帮助小规模纳税人完成纳税申报并保存完整账簿。" * 12
IRRELEVANT_DETAIL = "城市天气晴朗，体育比赛吸引了许多观众，周末交通运行平稳。" * 12


def article_url(slug):
    return f"https://example.cn/2026/t{slug}.html"


def make_source(source_id="source", **changes):
    values = {
        "id": source_id,
        "name": f"Source {source_id}",
        "type": "html_listing",
        "url": SOURCE_URL,
        "allowed_hosts": ("example.cn", "www.example.cn"),
        "category": "official",
        "language": "zh",
        "item_selector": "li",
        "link_selector": "a[href]",
        "url_pattern": r"/t[^/]+\.html$",
        "detail_selectors": (".article",),
        "fetch_detail": True,
        "max_candidates": 10,
        "detail_relevance_probe": False,
        "max_relevance_probes": 3,
    }
    values.update(changes)
    return Source(**values)


def make_item(source_id="source", url=None, **changes):
    values = {
        "source_id": source_id,
        "source_name": f"Source {source_id}",
        "category": "official",
        "language": "zh",
        "title": "代理记账和纳税申报学习材料",
        "url": url or article_url("learning"),
        "published_at": NOW - timedelta(days=10),
        "summary": "代理记账机构为小规模纳税人提供纳税申报学习材料。",
        "content": RELEVANT_DETAIL,
        "content_basis": "full_text",
        "topics": ["bookkeeping", "small_business", "tax"],
        "relevance": 12,
        "retrieved_at": NOW,
    }
    values.update(changes)
    return Item(**values)


def listing_html(entries):
    """Build entries of (URL, title, publication datetime, optional summary)."""
    parts = ["<html><body><ul>"]
    for url, title, published, summary in entries:
        parts.append(
            f"<li><a href='{url}'>{title}</a>"
            f"<time datetime='{published.isoformat()}'></time>"
        )
        if summary is not None:
            parts.append(f"<p>{summary}</p>")
        parts.append("</li>")
    parts.append("</ul></body></html>")
    return "".join(parts).encode("utf-8")


def detail_html(text, published=None):
    date = f"<time datetime='{published.isoformat()}'></time>" if published else ""
    return (
        f"<html><body>{date}<div class='article'><p>{text}</p></div></body></html>"
    ).encode("utf-8")


class FakeFetcher:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def fetch(self, url, hosts):
        self.requests.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        if isinstance(response, str):
            response = response.encode("utf-8")
        if isinstance(response, bytes):
            response = FetchResponse(url, 200, response)
        return response


def learning_english(item):
    return {
        "headline": "Learning brief: Understanding official bookkeeping guidance",
        "developments": [],
        "pulse": [],
        "learning_resource": {
            "title": "Official bookkeeping guidance",
            "description": (
                "Learn how official guidance explains practical records and filing "
                "responsibilities for a small business."
            ),
            "source_id": item.id,
        },
    }


def learning_chinese(item):
    return {
        "headline": "学习简报：理解官方代理记账指引",
        "developments": [],
        "pulse": [],
        "learning_resource": {
            "title": "官方代理记账学习指引",
            "description": "这份官方材料帮助初学者理解账簿记录、纳税申报和小微企业合规责任。",
            "source_id": item.id,
        },
    }


class FakeLearningClient:
    def __init__(self, item):
        self.item = item
        self.calls = []
        self.commits = []

    def generate(self, system, user, purpose):
        request = json.loads(user)
        self.calls.append((purpose, request))
        if purpose == "initial-digest":
            return "learning-en", json.dumps(learning_english(self.item), ensure_ascii=False)
        if purpose == "simplified-chinese-translation":
            return "learning-zh", json.dumps(learning_chinese(self.item), ensure_ascii=False)
        raise AssertionError(f"Unexpected model purpose: {purpose}")

    def commit(self, cache_key, content):
        self.commits.append(cache_key)


class TemporaryStateTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = State(self.root / "state.sqlite3")
        self.addCleanup(self.state.close)

    def collect(self, sources, fetcher, **changes):
        options = {
            "cutoff": FRESH_CUTOFF,
            "max_items": 12,
            "now": NOW,
        }
        options.update(changes)
        return collect_sources(
            sources, KEYWORDS, self.state, fetcher, options.pop("cutoff"),
            **options,
        )


class DetailRelevanceProbeTests(TemporaryStateTestCase):
    def test_generic_fresh_title_is_recovered_by_relevant_detail_body(self):
        source = make_source(detail_relevance_probe=True)
        url = article_url("generic")
        fetcher = FakeFetcher({
            source.url: listing_html([
                (url, "Weekly administrative update", NOW - timedelta(hours=2),
                 "General information for readers."),
            ]),
            url: detail_html(RELEVANT_DETAIL),
        })

        result = self.collect([source], fetcher)

        self.assertEqual("fresh", result.mode)
        self.assertEqual([url], [item.url for item in result.items])
        self.assertEqual("full_text", result.items[0].content_basis)
        self.assertEqual(
            {"bookkeeping", "small_business", "tax"}, set(result.items[0].topics)
        )
        self.assertEqual([source.url, url], fetcher.requests)
        self.assertIsNotNone(self.state.get_item(result.items[0].key))
        stats = result.diagnostics[source.id]
        self.assertEqual(1, stats["parsed"])
        self.assertEqual(1, stats["relevance_probes"])
        self.assertEqual(1, stats["body_relevant"])
        self.assertEqual(0, stats["body_irrelevant"])

    def test_irrelevant_detail_body_is_excluded_after_probe(self):
        source = make_source(detail_relevance_probe=True)
        url = article_url("irrelevant")
        fetcher = FakeFetcher({
            source.url: listing_html([
                (url, "Weekly administrative update", NOW - timedelta(hours=2),
                 "General information for readers."),
            ]),
            url: detail_html(IRRELEVANT_DETAIL),
        })

        result = self.collect([source], fetcher)

        self.assertEqual([], result.items)
        self.assertEqual("empty", result.mode)
        self.assertIsNone(self.state.get_item(make_item(url=url).key))
        self.assertEqual(1, result.diagnostics[source.id]["relevance_probes"])
        self.assertEqual(1, result.diagnostics[source.id]["body_irrelevant"])
        self.assertEqual(0, result.diagnostics[source.id]["body_relevant"])
        self.assertTrue(any(
            "after full-text relevance verification" in warning
            for warning in result.warnings
        ))

    def test_failed_probe_is_excluded_instead_of_using_listing_metadata(self):
        source = make_source(detail_relevance_probe=True)
        url = article_url("failed")
        fetcher = FakeFetcher({
            source.url: listing_html([
                (url, "Weekly administrative update", NOW - timedelta(hours=2),
                 "General information for readers without a topic keyword."),
            ]),
            url: FetchError("simulated detail failure"),
        })

        result = self.collect([source], fetcher)

        self.assertEqual([], result.items)
        self.assertEqual("empty", result.mode)
        self.assertEqual([source.url, url], fetcher.requests)
        self.assertIsNone(self.state.get_item(make_item(url=url).key))
        self.assertEqual(1, result.diagnostics[source.id]["relevance_probes"])
        self.assertEqual(0, result.diagnostics[source.id]["body_relevant"])
        self.assertTrue(any(
            "detail unavailable" in warning and "simulated detail failure" in warning
            for warning in result.warnings
        ))

    def test_per_source_probe_budget_skips_later_ambiguous_titles(self):
        source = make_source(detail_relevance_probe=True, max_relevance_probes=1)
        newest = article_url("newest")
        older = article_url("older")
        fetcher = FakeFetcher({
            source.url: listing_html([
                (older, "Older administrative update", NOW - timedelta(hours=4),
                 "General information for readers."),
                (newest, "Newest administrative update", NOW - timedelta(hours=2),
                 "General information for readers."),
            ]),
            newest: detail_html(RELEVANT_DETAIL),
            older: detail_html(RELEVANT_DETAIL),
        })

        result = self.collect([source], fetcher, max_relevance_probes=3)

        self.assertEqual([newest], [item.url for item in result.items])
        self.assertNotIn(older, fetcher.requests)
        self.assertEqual(1, result.diagnostics[source.id]["relevance_probes"])
        self.assertTrue(any(
            "relevance probe budget exhausted" in warning and "Older" in warning
            for warning in result.warnings
        ))

    def test_global_probe_budget_is_shared_across_sources(self):
        first = make_source(
            "first", url="https://example.cn/first-list/", max_relevance_probes=1,
            detail_relevance_probe=True,
        )
        second = make_source(
            "second", url="https://example.cn/second-list/", max_relevance_probes=1,
            detail_relevance_probe=True,
        )
        first_url = article_url("first")
        second_url = article_url("second")
        fetcher = FakeFetcher({
            first.url: listing_html([
                (first_url, "First administrative update", NOW - timedelta(hours=2),
                 "General information for readers."),
            ]),
            second.url: listing_html([
                (second_url, "Second administrative update", NOW - timedelta(hours=3),
                 "General information for readers."),
            ]),
            first_url: detail_html(RELEVANT_DETAIL),
            second_url: detail_html(RELEVANT_DETAIL),
        })

        result = self.collect([first, second], fetcher, max_relevance_probes=1)

        self.assertEqual([first_url], [item.url for item in result.items])
        self.assertIn(first_url, fetcher.requests)
        self.assertNotIn(second_url, fetcher.requests)
        self.assertEqual(1, result.diagnostics[first.id]["relevance_probes"])
        self.assertEqual(0, result.diagnostics[second.id]["relevance_probes"])
        self.assertTrue(any(
            "Source second: relevance probe budget exhausted" in warning
            for warning in result.warnings
        ))

    def test_probes_and_fallback_details_share_the_detail_request_budget(self):
        source = make_source(detail_relevance_probe=True)
        fresh_url = article_url("fresh-probe")
        fallback_url = article_url("fallback-direct")
        fetcher = FakeFetcher({
            source.url: listing_html([
                (fresh_url, "Generic administrative update", NOW - timedelta(hours=2),
                 "General information for readers."),
                (fallback_url, "代理记账纳税申报旧指引", NOW - timedelta(days=3),
                 "General information for readers."),
            ]),
            fresh_url: detail_html(IRRELEVANT_DETAIL),
            fallback_url: detail_html(RELEVANT_DETAIL),
        })

        result = self.collect(
            [source], fetcher, fallback_cutoff=FALLBACK_CUTOFF, max_items=1,
        )

        self.assertEqual("recent_unread", result.mode)
        self.assertEqual([fallback_url], [item.url for item in result.items])
        self.assertIn(fresh_url, fetcher.requests)
        self.assertNotIn(fallback_url, fetcher.requests)
        self.assertEqual("listing_summary", result.items[0].content_basis)
        self.assertEqual(1, result.diagnostics[source.id]["body_irrelevant"])
        self.assertEqual(1, result.diagnostics[source.id]["fallback_selected"])
        self.assertTrue(any(
            "detail-request budget exhausted" in warning
            and "using listing metadata" in warning
            for warning in result.warnings
        ))


class RecentUnreadFallbackTests(TemporaryStateTestCase):
    def test_fallback_runs_when_fresh_candidate_fails_final_date_check(self):
        source = make_source()
        fresh_url = article_url("fresh-listing-old-detail")
        fallback_url = article_url("eligible-fallback")
        fetcher = FakeFetcher({
            source.url: listing_html([
                (fresh_url, "代理记账新指引", NOW - timedelta(hours=2),
                 "General information for readers."),
                (fallback_url, "代理记账旧指引", NOW - timedelta(days=3),
                 "General information for readers."),
            ]),
            fresh_url: detail_html(RELEVANT_DETAIL, NOW - timedelta(days=4)),
            fallback_url: detail_html(RELEVANT_DETAIL),
        })

        result = self.collect([source], fetcher, fallback_cutoff=FALLBACK_CUTOFF, max_items=1)

        self.assertEqual("recent_unread", result.mode)
        self.assertEqual([fallback_url], [item.url for item in result.items])
        self.assertEqual(2, result.diagnostics[source.id]["deferred_candidates"])
        self.assertEqual(1, result.diagnostics[source.id]["fallback_selected"])
        self.assertTrue(any(
            "after detail-date verification" in warning for warning in result.warnings
        ))

    def test_detail_date_can_demote_fresh_listing_into_recent_fallback(self):
        source = make_source()
        url = article_url("demoted-by-detail-date")
        fetcher = FakeFetcher({
            source.url: listing_html([
                (url, "代理记账指引", NOW - timedelta(hours=2), None),
            ]),
            url: detail_html(RELEVANT_DETAIL, NOW - timedelta(days=4)),
        })

        result = self.collect([source], fetcher, fallback_cutoff=FALLBACK_CUTOFF)

        self.assertEqual("recent_unread", result.mode)
        self.assertEqual([url], [item.url for item in result.items])
        self.assertEqual(1, result.diagnostics[source.id]["fallback_selected"])
        self.assertEqual(1, result.diagnostics[source.id]["deferred_candidates"])
        self.assertEqual(2, len(fetcher.requests))
        self.assertTrue(any("held" in warning for warning in result.warnings))

    def test_any_fresh_result_prevents_recent_unread_fallback(self):
        source = make_source(fetch_detail=False)
        fresh_url = article_url("fresh")
        fallback_url = article_url("deferred")
        fetcher = FakeFetcher({
            source.url: listing_html([
                (fallback_url, "代理记账旧指引", NOW - timedelta(days=3), None),
                (fresh_url, "代理记账新指引", NOW - timedelta(hours=2), None),
            ]),
        })

        result = self.collect([source], fetcher, fallback_cutoff=FALLBACK_CUTOFF)

        self.assertEqual("fresh", result.mode)
        self.assertEqual([fresh_url], [item.url for item in result.items])
        self.assertEqual([source.url], fetcher.requests)
        self.assertEqual(1, result.diagnostics[source.id]["deferred_candidates"])
        self.assertEqual(0, result.diagnostics[source.id]["fallback_selected"])
        self.assertIsNone(self.state.get_item(make_item(url=fallback_url).key))

    def test_processed_items_are_excluded_from_both_news_tiers(self):
        source = make_source(fetch_detail=False)
        processed_fresh = article_url("processed-fresh")
        processed_old = article_url("processed-old")
        eligible_old = article_url("eligible-old")
        fresh_item = make_item(url=processed_fresh, published_at=NOW - timedelta(hours=2))
        old_item = make_item(url=processed_old, published_at=NOW - timedelta(days=2))
        self.state.upsert_item(fresh_item)
        self.state.upsert_item(old_item)
        self.state.record_sent(
            "2026-11-13", "Earlier delivery", {}, "text", "html",
            [fresh_item.key, old_item.key],
        )
        fetcher = FakeFetcher({
            source.url: listing_html([
                (processed_fresh, "代理记账已发送新指引", NOW - timedelta(hours=2), None),
                (processed_old, "代理记账已发送旧指引", NOW - timedelta(days=2), None),
                (eligible_old, "代理记账未发送旧指引", NOW - timedelta(days=3), None),
            ]),
        })

        result = self.collect([source], fetcher, fallback_cutoff=FALLBACK_CUTOFF)

        self.assertEqual("recent_unread", result.mode)
        self.assertEqual([eligible_old], [item.url for item in result.items])
        self.assertEqual(2, result.diagnostics[source.id]["processed_excluded"])
        self.assertEqual(1, result.diagnostics[source.id]["deferred_candidates"])

    def test_fallback_cutoff_is_inclusive_and_older_items_are_stale(self):
        source = make_source(fetch_detail=False)
        boundary = article_url("boundary")
        too_old = article_url("too-old")
        fetcher = FakeFetcher({
            source.url: listing_html([
                (boundary, "代理记账窗口边界指引", FALLBACK_CUTOFF, None),
                (too_old, "纳税申报过期指引", FALLBACK_CUTOFF - timedelta(seconds=1), None),
            ]),
        })

        result = self.collect([source], fetcher, fallback_cutoff=FALLBACK_CUTOFF)

        self.assertEqual("recent_unread", result.mode)
        self.assertEqual([boundary], [item.url for item in result.items])
        self.assertEqual(1, result.diagnostics[source.id]["stale_excluded"])
        self.assertEqual(1, result.diagnostics[source.id]["deferred_candidates"])

    def test_fallback_is_newest_first_even_when_older_item_has_more_keywords(self):
        source = make_source(fetch_detail=False, max_candidates=3)
        newest = article_url("newest-low-relevance")
        middle = article_url("middle-relevance")
        oldest = article_url("oldest-high-relevance")
        fetcher = FakeFetcher({
            source.url: listing_html([
                (oldest, "代理记账小规模纳税人纳税申报旧指引", NOW - timedelta(days=6), None),
                (middle, "代理记账纳税申报中期指引", NOW - timedelta(days=4), None),
                (newest, "代理记账最新指引", NOW - timedelta(days=2), None),
            ]),
        })

        result = self.collect(
            [source], fetcher, fallback_cutoff=FALLBACK_CUTOFF, max_items=2,
        )

        self.assertEqual([newest, middle], [item.url for item in result.items])
        self.assertNotIn(oldest, [item.url for item in result.items])
        self.assertEqual(3, result.diagnostics[source.id]["deferred_candidates"])
        self.assertEqual(2, result.diagnostics[source.id]["fallback_selected"])

    def test_collection_result_serializes_selection_mode_and_diagnostics(self):
        item = make_item()
        result = CollectionResult(
            [item], [item.source_id], {}, ["note"], "recent_unread",
            {item.source_id: {"fallback_selected": 1}},
        )

        payload = result.to_dict()

        self.assertEqual("recent_unread", payload["mode"])
        self.assertEqual({item.source_id: {"fallback_selected": 1}}, payload["diagnostics"])
        self.assertEqual([item.id], [entry["id"] for entry in payload["items"]])
        default = CollectionResult([], [], {}, {})
        self.assertEqual("empty", default.mode)
        self.assertEqual({}, default.diagnostics)


class ChinaCalendarCutoffTests(TemporaryStateTestCase):
    def test_cutoff_uses_china_calendar_dates_and_local_midnight(self):
        now = datetime(2026, 11, 14, 16, 30, tzinfo=UTC)
        self.assertEqual("2026-11-15T00:30:00+08:00", now.astimezone(CHINA_TZ).isoformat())

        one_day = cli.china_calendar_cutoff(now, 1)
        three_days = cli.china_calendar_cutoff(now, 3)
        seven_days = cli.china_calendar_cutoff(now, 7)

        self.assertEqual("2026-11-15T00:00:00+08:00", one_day.isoformat())
        self.assertEqual("2026-11-13T00:00:00+08:00", three_days.isoformat())
        self.assertEqual("2026-11-09T00:00:00+08:00", seven_days.isoformat())
        self.assertEqual(datetime(2026, 11, 12, 16, 0, tzinfo=UTC), three_days)
        for invalid in (0, -1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                cli.china_calendar_cutoff(now, invalid)

    def test_scheduled_collection_uses_three_and_seven_day_calendar_cutoffs(self):
        self.state.mark_collection()
        args = SimpleNamespace(
            lookback_hours=None, initial_lookback_hours=168, max_items=12,
        )
        collected = CollectionResult([], ["source"], {}, {})
        now = datetime(2026, 11, 14, 16, 30, tzinfo=UTC)
        with mock.patch.object(cli, "utc_now", return_value=now), \
                mock.patch.object(cli, "Fetcher", return_value=object()), \
                mock.patch.object(cli, "collect_sources", return_value=collected) as collection:
            result, path = cli.collect(
                self.state, args, [], KEYWORDS, self.root / "build",
            )

        self.assertIs(collected, result)
        self.assertTrue(path.exists())
        kwargs = collection.call_args.kwargs
        self.assertEqual(cli.china_calendar_cutoff(now, 3), collection.call_args.args[4])
        self.assertEqual(cli.china_calendar_cutoff(now, 7), kwargs["fallback_cutoff"])
        self.assertEqual(now, kwargs["now"])


class UnusedOfficialLearningItemTests(TemporaryStateTestCase):
    def test_filters_category_text_processing_and_returns_newest_eligible(self):
        older = make_item(url=article_url("eligible-older"), published_at=NOW - timedelta(days=8))
        newest = make_item(url=article_url("eligible-newest"), published_at=NOW - timedelta(days=6))
        ineligible = [
            make_item(
                url=article_url("nonofficial"), category="practitioner_industry",
                published_at=NOW - timedelta(days=1),
            ),
            make_item(
                url=article_url("listing-only"), content_basis="listing_summary",
                published_at=NOW - timedelta(days=1),
            ),
            make_item(
                url=article_url("empty-content"), content="",
                published_at=NOW - timedelta(days=1),
            ),
            make_item(
                url=article_url("no-topics"), topics=[],
                published_at=NOW - timedelta(days=1),
            ),
            make_item(url=article_url("undated"), published_at=None),
        ]
        processed = make_item(
            url=article_url("processed"), published_at=NOW - timedelta(days=1),
        )
        for item in [older, newest, processed, *ineligible]:
            self.state.upsert_item(item)
        self.state.mark_items_processed([processed.key])

        self.assertEqual(newest.key, self.state.get_unused_official_item().key)
        self.state.mark_items_processed([newest.key])
        self.assertEqual(older.key, self.state.get_unused_official_item().key)
        self.state.mark_items_processed([older.key])
        self.assertIsNone(self.state.get_unused_official_item())

    def test_newest_eligible_item_is_ordered_by_instant_across_timezone_offsets(self):
        older = make_item(
            url=article_url("offset-older"),
            published_at=datetime(2026, 11, 14, 0, 30, tzinfo=CHINA_TZ),
        )
        newer = make_item(
            url=article_url("offset-newer"),
            published_at=datetime(2026, 11, 13, 17, 0, tzinfo=UTC),
        )
        self.assertLess(older.published_at.astimezone(UTC), newer.published_at.astimezone(UTC))
        self.state.upsert_item(older)
        self.state.upsert_item(newer)

        selected = self.state.get_unused_official_item()

        self.assertEqual(newer.key, selected.key)


class LearningOnlyDigestTests(unittest.TestCase):
    def setUp(self):
        self.official = make_item()
        self.nonofficial = make_item(
            source_id="community", url="https://example.cn/2026/tcommunity.html",
            category="community",
        )

    def test_validation_requires_empty_news_sections_and_one_official_resource(self):
        valid = learning_english(self.official)
        self.assertEqual(valid, validate_digest(valid, [self.official], learning_only=True))

        development = {
            "title": "Unsupported development",
            "summary": "This news section is not permitted in a learning-only digest.",
            "connection": "It would incorrectly turn the learning brief into a news update.",
            "source_ids": [self.official.id],
        }
        pulse = {
            "text": "This pulse entry is not permitted in a learning-only digest.",
            "source_ids": [self.official.id],
        }
        variants = []
        with_development = copy.deepcopy(valid)
        with_development["developments"] = [development]
        variants.append(("developments", with_development, "must not contain developments"))
        with_pulse = copy.deepcopy(valid)
        with_pulse["pulse"] = [pulse]
        variants.append(("pulse", with_pulse, "must not contain pulse"))
        without_resource = copy.deepcopy(valid)
        without_resource["learning_resource"] = None
        variants.append(("missing", without_resource, "requires one official learning resource"))
        nonofficial = learning_english(self.nonofficial)
        variants.append(("nonofficial", nonofficial, "must cite an official source"))

        for name, value, message in variants:
            with self.subTest(name=name), self.assertRaisesRegex(SummaryError, message):
                items = [self.nonofficial] if name == "nonofficial" else [self.official]
                validate_digest(value, items, learning_only=True)

        with self.assertRaisesRegex(SummaryError, "requires an official source item"):
            create_digest([], "2026-11-14", [], None, learning_only=True)

    def test_learning_translation_preserves_mode_and_rejects_news_or_nonofficial_sources(self):
        english = learning_english(self.official)
        chinese = learning_chinese(self.official)
        self.assertEqual(
            chinese, validate_translation(english, chinese, [self.official])
        )

        with_development = copy.deepcopy(chinese)
        with_development["developments"] = [{
            "title": "不应出现的动态",
            "summary": "学习简报不应包含任何新闻动态内容。",
            "connection": "这会错误地把学习材料变成新闻更新。",
            "source_ids": [self.official.id],
        }]
        with self.assertRaisesRegex(SummaryError, "must not contain developments"):
            validate_translation(english, with_development, [self.official])

        with_pulse = copy.deepcopy(chinese)
        with_pulse["pulse"] = [{
            "text": "学习简报不应包含行业观察内容。",
            "source_ids": [self.official.id],
        }]
        with self.assertRaisesRegex(SummaryError, "must not contain pulse"):
            validate_translation(english, with_pulse, [self.official])

        with self.assertRaisesRegex(SummaryError, "must cite an official source"):
            validate_translation(
                learning_english(self.nonofficial),
                learning_chinese(self.nonofficial),
                [self.nonofficial],
            )

    def test_create_digest_translates_and_renders_prominent_bilingual_non_news_label(self):
        client = FakeLearningClient(self.official)

        digest, rendered = create_digest(
            [self.official], "2026-11-14", ["Learning fallback selected"],
            client, learning_only=True,
        )

        self.assertEqual(
            ["initial-digest", "simplified-chinese-translation"],
            [purpose for purpose, request in client.calls],
        )
        initial_request = client.calls[0][1]
        self.assertEqual("learning_only", initial_request["response_mode"])
        self.assertIn("not a news update", initial_request["task"])
        self.assertEqual([self.official.id], [item["id"] for item in initial_request["items"]])
        self.assertEqual(digest["en"], client.calls[1][1]["english_digest"])
        self.assertEqual(["learning-en", "learning-zh"], client.commits)

        for language in ("en", "zh-Hans"):
            self.assertEqual([], digest[language]["developments"])
            self.assertEqual([], digest[language]["pulse"])
            self.assertIsInstance(digest[language]["learning_resource"], dict)
            self.assertEqual(
                self.official.id, digest[language]["learning_resource"]["source_id"]
            )

        english_label = "Learning brief — not a news update"
        chinese_label = "学习简报 — 非新闻更新"
        self.assertIn(english_label, rendered.text)
        self.assertIn(chinese_label, rendered.text)
        self.assertIn(f"<strong>{english_label}</strong>", rendered.html)
        self.assertIn(f"<strong>{chinese_label}</strong>", rendered.html)
        self.assertLess(rendered.text.index(english_label), rendered.text.index(digest["en"]["headline"]))
        self.assertLess(
            rendered.text.index(chinese_label),
            rendered.text.index(digest["zh-Hans"]["headline"]),
        )
        for empty_news_text in (
            "No new material met the relevance and freshness checks",
            "今天没有符合相关性和时效性要求的新材料",
            "未用旧闻填充篇幅",
        ):
            self.assertNotIn(empty_news_text, rendered.text)
            self.assertNotIn(empty_news_text, rendered.html)
        self.assertNotIn("KEY DEVELOPMENTS", rendered.text)
        self.assertNotIn("主要动态", rendered.text)
        self.assertEqual(1, rendered.text.count(self.official.display_url))
        self.assertLessEqual(rendered.word_count, 600)


class RecentUnreadRenderingTests(unittest.TestCase):
    def test_extended_window_label_is_visible_in_both_languages(self):
        item = make_item()
        english = {
            "headline": "Earlier bookkeeping guidance",
            "developments": [{
                "title": "Earlier filing guidance",
                "summary": "An earlier official article explains practical filing records.",
                "connection": "The article gives bookkeepers a useful workflow lesson.",
                "source_ids": [item.id],
            }],
            "pulse": [],
            "learning_resource": None,
        }
        chinese = {
            "headline": "此前代理记账指引",
            "developments": [{
                "title": "此前申报指引",
                "summary": "此前一篇官方文章介绍了实务申报记录。",
                "connection": "该文章为记账人员提供了有用的流程学习内容。",
                "source_ids": [item.id],
            }],
            "pulse": [],
            "learning_resource": None,
        }
        rendered = render_bilingual_digest(
            english, chinese, [item], "2026-11-14", recent_unread=True,
        )
        self.assertIn("Earlier, not previously sent", rendered.text)
        self.assertIn("此前未发送", rendered.text)
        self.assertIn(item.published_at.astimezone(CHINA_TZ).date().isoformat(), rendered.text)


class CLIFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "data").mkdir()
        config = {
            "version": 1,
            "keywords": {"bookkeeping": ["代理记账"]},
            "sources": [{
                "id": "source",
                "name": "Test official",
                "type": "html_listing",
                "url": SOURCE_URL,
                "allowed_hosts": ["example.cn"],
                "category": "official",
                "language": "zh",
                "item_selector": "li",
                "url_pattern": r"/t[^/]+\.html$",
                "fetch_detail": False,
            }],
        }
        (self.root / "data" / "sources.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8",
        )

    def test_preview_fails_without_news_or_learning_material_and_calls_no_model(self):
        fetcher = FakeFetcher({
            SOURCE_URL: listing_html([
                (article_url("generic"), "Weekly administrative update",
                 NOW - timedelta(hours=2), "General information for readers."),
            ]),
        })
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(cli, "Fetcher", return_value=fetcher), \
                mock.patch.object(cli, "OpenAICompatibleClient") as client_class, \
                mock.patch.object(cli, "utc_now", return_value=NOW), \
                redirect_stdout(stdout), redirect_stderr(stderr), \
                self.assertRaises(SystemExit) as raised:
            cli.main(["preview", "--date", "2026-11-14"], root=self.root)

        self.assertEqual(1, raised.exception.code)
        self.assertIn("No fresh, recent unread, or unused official learning material", stderr.getvalue())
        self.assertIn("Collected 0 candidate(s)", stdout.getvalue())
        client_class.assert_not_called()
        self.assertFalse((self.root / "build" / "digests").exists())
        with State(self.root / "build" / "state.sqlite3") as state:
            self.assertIsNone(state.get_run("2026-11-14"))

    def test_preview_uses_saved_official_learning_item_when_collection_fails(self):
        learning_item = make_item(published_at=NOW - timedelta(days=30))
        with State(self.root / "build" / "state.sqlite3") as state:
            state.upsert_item(learning_item)
        fetcher = FakeFetcher({SOURCE_URL: FetchError("simulated source outage")})
        client = FakeLearningClient(learning_item)
        stdout = io.StringIO()
        with mock.patch.object(cli, "Fetcher", return_value=fetcher), \
                mock.patch.object(cli, "OpenAICompatibleClient", return_value=client), \
                mock.patch.object(cli, "send_email", side_effect=AssertionError("SMTP forbidden")), \
                mock.patch.object(cli, "utc_now", return_value=NOW), \
                redirect_stdout(stdout):
            cli.main(["preview", "--date", "2026-11-14"], root=self.root)

        candidates = json.loads(
            (self.root / "build" / "latest_candidates.json").read_text(encoding="utf-8")
        )
        self.assertEqual("learning_only", candidates["mode"])
        self.assertEqual([], candidates["succeeded"])
        self.assertIn("source", candidates["errors"])
        self.assertEqual([learning_item.id], [item["id"] for item in candidates["items"]])

        saved = json.loads(
            (self.root / "build" / "digests" / "2026-11-14.json").read_text(encoding="utf-8")
        )
        self.assertEqual([], saved["en"]["developments"])
        self.assertEqual([], saved["en"]["pulse"])
        self.assertEqual(learning_item.id, saved["en"]["learning_resource"]["source_id"])
        self.assertEqual([], saved["zh-Hans"]["developments"])
        self.assertEqual([], saved["zh-Hans"]["pulse"])
        text = (self.root / "build" / "digests" / "2026-11-14.txt").read_text(encoding="utf-8")
        self.assertIn("Learning brief — not a news update", text)
        self.assertIn("学习简报 — 非新闻更新", text)
        self.assertIn("official-guidance learning brief, not a news update", text)
        self.assertIn("Collected 1 candidate(s) from 0 source(s)", stdout.getvalue())
        with State(self.root / "build" / "state.sqlite3") as state:
            self.assertFalse(state.is_processed(learning_item.key))
            self.assertIsNone(state.get_run("2026-11-14"))


if __name__ == "__main__":
    unittest.main()
