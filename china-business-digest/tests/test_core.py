import io
import json
import os
import smtplib
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from digestlib import cli
from digestlib.collect import classify, collect_sources, extract_detail, parse_html_listing, parse_rss
from digestlib.config import load_configuration
from digestlib.emailer import SMTPConfig, build_message, send_email
from digestlib.errors import ConfigurationError, FetchError, SourceError, StateError, SummaryError
from digestlib.http import FetchResponse, Fetcher
from digestlib.models import CollectionResult, Item, Source
from digestlib.render import empty_digest, render_bilingual_digest, render_digest, visible_word_count
from digestlib.state import State
from digestlib.summarize import (
    _keyword_excerpt, create_digest, decode_json_object, validate_digest,
    validate_english_language, validate_translation,
)
from digestlib.summarize_client import OpenAICompatibleClient
from digestlib.util import parse_date

UTC = timezone.utc
NOW = datetime(2026, 11, 14, 1, 0, tzinfo=UTC)


def make_source(**changes):
    values = {
        "id": "test_source",
        "name": "测试来源",
        "type": "html_listing",
        "url": "https://example.cn/list/",
        "allowed_hosts": ("example.cn", "www.example.cn"),
        "category": "official",
        "language": "zh",
        "item_selector": "li",
        "link_selector": "a[href]",
        "url_pattern": r"/t[^/]+\.html$",
        "detail_selectors": (".article",),
        "fetch_detail": True,
        "max_candidates": 3,
    }
    values.update(changes)
    return Source(**values)


def make_item(source_id="test_source", category="official", title="代理记账新规", url=None):
    return Item(
        source_id=source_id,
        source_name="测试来源",
        category=category,
        language="zh",
        title=title,
        url=url or "https://example.cn/2026/t1.html",
        published_at=datetime(2026, 11, 13, 2, 0, tzinfo=UTC),
        summary="小规模纳税人需要了解纳税申报和数电发票。",
        content="会计和代理记账服务帮助小微企业保存账簿并完成增值税申报。" * 8,
        content_basis="full_text",
        topics=["bookkeeping", "small_business", "tax"],
        relevance=12,
    )


def valid_digest(item, summary="The official notice explains a filing requirement for small businesses."):
    return {
        "headline": "A practical filing update",
        "developments": [{
            "title": "New filing guidance",
            "summary": summary,
            "connection": "Accurate bookkeeping gives a small business the records needed for this tax filing.",
            "source_ids": [item.id],
        }],
        "pulse": [],
        "learning_resource": None,
    }


def chinese_digest(item):
    return {
        "headline": "实用纳税申报更新",
        "developments": [{
            "title": "新的纳税申报指引",
            "summary": "官方通知说明了小规模纳税人的纳税申报要求。",
            "connection": "准确记账为小企业完成税务申报提供所需记录。",
            "source_ids": [item.id],
        }],
        "pulse": [],
        "learning_resource": None,
    }


class ParsingTests(unittest.TestCase):
    def test_rss_cdata_dates_guid_and_host_filtering(self):
        source = make_source(type="rss", url="https://example.cn/feed", url_pattern="")
        body = """<?xml version='1.0' encoding='utf-8'?>
        <rss version='2.0'><channel><title>Tax</title>
        <item><guid>https://www.example.cn/2026/t1.html</guid>
        <title>小规模纳税人申报指南</title><link>https://www.example.cn/2026/t1.html?utm_source=rss</link>
        <pubDate>Fri, 13 Nov 2026 15:51:50 +0800</pubDate>
        <description><![CDATA[<p>代理记账 &amp; 纳税申报</p>]]></description></item>
        <item><title>External item</title><link>https://outside.example/t2.html</link></item>
        </channel></rss>""".encode()
        items = parse_rss(source, body, NOW)
        self.assertEqual(1, len(items))
        self.assertEqual("代理记账 & 纳税申报", items[0].summary)
        self.assertEqual("https://www.example.cn/2026/t1.html", items[0].url)
        expected = datetime(2026, 11, 13, 15, 51, 50, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(expected, items[0].published_at)

    def test_html_listing_resolves_relative_url_and_uses_displayed_date(self):
        source = make_source()
        body = """
        <html><body><ul>
        <li><a href='../2026/t101.html'>代理记账监督管理办法</a><span>2026-11-11</span></li>
        <li><a href='/2026/t102.html'>无日期但可用的政策解读</a></li>
        <li><a href='https://outside.example/t3.html'>站外链接不应采集</a><span>2026-11-10</span></li>
        </ul></body></html>""".encode()
        items = parse_html_listing(source, body, NOW)
        self.assertEqual(2, len(items))
        self.assertEqual("https://example.cn/2026/t101.html", items[0].url)
        self.assertEqual("2026-11-11T00:00:00+08:00", items[0].published_at.isoformat())
        self.assertIsNone(items[1].published_at)

    def test_listing_links_resolve_against_final_redirect_url(self):
        source = make_source()
        body = "<ul><li><a href='t9.html'>代理记账纳税申报指引</a>2026-11-13</li></ul>".encode()
        items = parse_html_listing(
            source, body, NOW, base_url="https://www.example.cn/redirected/list/"
        )
        self.assertEqual("https://www.example.cn/redirected/list/t9.html", items[0].url)

    def test_detail_extraction_canonical_date_and_boilerplate_removal(self):
        source = make_source()
        item = make_item()
        item.content = ""
        item.summary = ""
        body = f"""<html><head><link rel='canonical' href='/2026/t1.html'>
        <meta name='description' content='Policy summary'></head><body>
        <time datetime='2026-11-12T09:30:00+08:00'></time>
        <nav>Navigation that must not be included</nav>
        <div class='article'><h1>Policy title</h1><p>{'会计和申报具体规定。' * 30}</p></div>
        <script>alert('bad')</script></body></html>""".encode()
        extract_detail(source, item, body, item.url)
        self.assertEqual("full_text", item.content_basis)
        self.assertNotIn("Navigation", item.content)
        self.assertNotIn("alert", item.content)
        self.assertEqual("2026-11-12T09:30:00+08:00", item.published_at.isoformat())

    def test_parse_date_returns_none_for_impossible_dates(self):
        for text in ("2026-13-05", "2026-02-30", "2026-11-0", "2026-11-10 24:00"):
            with self.subTest(text=text):
                self.assertIsNone(parse_date(text))

    def test_listing_survives_impossible_dates(self):
        source = make_source()
        body = """<ul>
        <li><a href='/2026/t1.html'>代理记账新指引</a>2026-13-05</li>
        <li><a href='/2026/t2.html'>小规模纳税人申报指引</a>2026-11-12</li>
        </ul>""".encode()
        items = parse_html_listing(source, body, NOW)
        self.assertEqual(2, len(items))
        self.assertIsNone(items[0].published_at)
        self.assertEqual("2026-11-12", items[1].published_at.date().isoformat())

    def test_keyword_classification_covers_all_three_learning_topics(self):
        item = make_item()
        classify(item, {
            "bookkeeping": ["代理记账"], "small_business": ["小微企业"], "tax": ["增值税"],
        })
        self.assertEqual(["bookkeeping", "small_business", "tax"], item.topics)
        self.assertGreaterEqual(item.relevance, 9)


class StateAndCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = State(self.root / "state.sqlite3")

    def tearDown(self):
        self.state.close()
        self.temporary.cleanup()

    def test_sent_state_is_idempotent_and_marks_candidates_processed(self):
        item = make_item()
        self.state.upsert_item(item)
        self.state.record_sent("2026-11-14", "Subject", valid_digest(item), "text", "<p>html</p>", [item.key])
        self.assertTrue(self.state.is_processed(item.key))
        self.assertTrue(self.state.has_sent_run())
        self.assertEqual("text", self.state.get_run("2026-11-14")["text_body"])
        with self.assertRaises(StateError):
            self.state.record_sent("2026-11-14", "Again", {}, "x", "y", [item.key])

    def test_model_and_http_caches_round_trip(self):
        self.state.set_model("key", '{"ok": true}')
        self.state.set_http("https://example.cn/", b"abc", "etag", "modified", "text/html")
        self.assertEqual('{"ok": true}', self.state.get_model("key"))
        cached = self.state.get_http("https://example.cn/")
        self.assertEqual(b"abc", bytes(cached["body"]))
        self.assertEqual("etag", cached["etag"])

    def test_pending_delivery_fails_closed_until_marked_sent(self):
        item = make_item()
        self.state.upsert_item(item)
        self.state.record_pending("2026-11-14", "Subject", {}, "text", "html", [item.key])
        run = self.state.get_run("2026-11-14")
        self.assertEqual("sending", run["status"])
        self.assertIsNone(run["sent_at"])
        self.assertTrue(self.state.is_processed(item.key))
        self.assertFalse(self.state.has_sent_run())
        self.state.mark_sent("2026-11-14")
        self.assertEqual("sent", self.state.get_run("2026-11-14")["status"])
        self.assertTrue(self.state.has_sent_run())

    def test_pending_runs_are_listed_across_dates(self):
        self.state.record_pending("2026-11-12", "S1", {}, "t", "h", [])
        self.state.record_pending("2026-11-13", "S2", {}, "t", "h", [])
        self.state.mark_sent("2026-11-13")
        self.assertEqual(
            ["2026-11-12"], [row["run_date"] for row in self.state.get_pending_runs()]
        )

    def test_legacy_http_cache_is_migrated_to_final_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.sqlite3"
            legacy = sqlite3.connect(str(path))
            legacy.executescript(
                """CREATE TABLE http_cache (
                       url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT,
                       content_type TEXT, body BLOB NOT NULL, fetched_at TEXT NOT NULL);"""
            )
            legacy.execute(
                "INSERT INTO http_cache VALUES "
                "('https://example.cn/', NULL, NULL, 'text/html', x'616263', '2026-01-01T00:00:00+00:00')"
            )
            legacy.commit()
            legacy.close()
            with State(path) as state:
                cached = state.get_http("https://example.cn/")
                self.assertEqual(b"abc", bytes(cached["body"]))
                state.set_http("https://example.cn/", b"xyz", final_url="https://example.cn/final/")
                self.assertEqual(
                    "https://example.cn/final/", state.get_http("https://example.cn/")["final_url"]
                )

    def test_collection_history_is_separate_from_send_history(self):
        self.assertFalse(self.state.has_collection_run())
        self.state.mark_collection()
        self.assertTrue(self.state.has_collection_run())
        self.assertFalse(self.state.has_sent_run())

    def test_source_failure_isolated_and_global_cap_limits_detail_fetches(self):
        good = make_source(max_candidates=3)
        bad = make_source(id="bad_source", name="Broken", url="https://broken.example/list/")
        listing = b"""<ul>
        <li><a href='/2026/t1.html'>\xe4\xbb\xa3\xe7\x90\x86\xe8\xae\xb0\xe8\xb4\xa6\xe6\x8c\x87\xe5\x8d\x97</a>2026-11-13</li>
        <li><a href='/2026/t2.html'>\xe5\xb0\x8f\xe8\xa7\x84\xe6\xa8\xa1\xe7\xba\xb3\xe7\xa8\x8e\xe4\xba\xba\xe7\x94\xb3\xe6\x8a\xa5</a>2026-11-12</li>
        </ul>"""
        detail = ("<div class='article'>" + "会计代理记账纳税申报内容。" * 30 + "</div>").encode()

        class FakeFetcher:
            def __init__(self):
                self.detail_urls = []

            def fetch(self, url, hosts):
                if url == bad.url:
                    raise FetchError("unavailable")
                if url == good.url:
                    return FetchResponse(url, 200, listing)
                self.detail_urls.append(url)
                return FetchResponse(url, 200, detail)

        fetcher = FakeFetcher()
        result = collect_sources(
            [good, bad], {"bookkeeping": ["代理记账"], "tax": ["纳税申报"]},
            self.state, fetcher, NOW - timedelta(days=7), max_items=1, now=NOW,
        )
        self.assertEqual(1, len(result.items))
        self.assertEqual(["test_source"], result.succeeded)
        self.assertIn("bad_source", result.errors)
        self.assertEqual(1, len(fetcher.detail_urls))
        self.assertIsNotNone(self.state.get_item(result.items[0].key))

    def test_undated_and_zero_relevance_items_are_not_shortlisted(self):
        source = make_source(fetch_detail=False)
        body = """<ul>
        <li><a href='/2026/t1.html'>代理记账新指引</a></li>
        <li><a href='/2026/t2.html'>Local weather and sports</a>2026-11-13</li>
        </ul>""".encode()

        class FakeFetcher:
            def fetch(self, url, hosts):
                return FetchResponse(url, 200, body)

        result = collect_sources(
            [source], {"bookkeeping": ["代理记账"]}, self.state, FakeFetcher(),
            NOW - timedelta(days=7), now=NOW,
        )
        self.assertEqual([], result.items)
        self.assertEqual(2, len(result.warnings))

    def test_detail_date_older_than_cutoff_is_rechecked(self):
        source = make_source()
        listing = b"<ul><li><a href='/2026/t1.html'>\xe4\xbb\xa3\xe7\x90\x86\xe8\xae\xb0\xe8\xb4\xa6\xe6\x96\xb0\xe6\x8c\x87\xe5\xbc\x95</a>2026-11-13</li></ul>"
        detail = ("<time datetime='2020-01-01'></time><div class='article'>" + "代理记账纳税申报内容。" * 30 + "</div>").encode()

        class FakeFetcher:
            def fetch(self, url, hosts):
                return FetchResponse(url, 200, listing if url == source.url else detail)

        result = collect_sources(
            [source], {"bookkeeping": ["代理记账"]}, self.state, FakeFetcher(),
            NOW - timedelta(days=7), now=NOW,
        )
        self.assertEqual([], result.items)
        self.assertIn("detail-date verification", result.warnings[0])

    def test_processed_items_are_not_collected_again(self):
        item = make_item()
        self.state.upsert_item(item)
        self.state.record_sent("2026-11-13", "Subject", {}, "text", "html", [item.key])
        source = make_source()
        body = b"<ul><li><a href='/2026/t1.html'>\xe4\xbb\xa3\xe7\x90\x86\xe8\xae\xb0\xe8\xb4\xa6\xe6\x96\xb0\xe8\xa7\x84</a>2026-11-13</li></ul>"

        class FakeFetcher:
            def fetch(self, url, hosts):
                return FetchResponse(url, 200, body)

        result = collect_sources(
            [source], {"bookkeeping": ["代理记账"]}, self.state, FakeFetcher(),
            NOW - timedelta(days=7), now=NOW,
        )
        self.assertEqual([], result.items)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = State(Path(self.temporary.name) / "state.sqlite3")

    def tearDown(self):
        self.state.close()
        self.temporary.cleanup()

    def test_conditional_304_uses_cached_bytes_and_final_url(self):
        url = "https://example.cn/feed"
        self.state.set_http(url, b"cached", etag='"v1"', final_url="https://example.cn/feed/final/")
        fetcher = Fetcher(self.state, sleep=lambda _: None)
        fetcher._robots_allowed = lambda *_: True
        error = urllib.error.HTTPError(url, 304, "Not Modified", {}, io.BytesIO())

        class Opener:
            def open(self, request, timeout):
                if request.get_header("If-none-match") != '"v1"':
                    raise AssertionError("Missing conditional ETag")
                raise error

        fetcher._opener = lambda *_: Opener()
        response = fetcher.fetch(url, ("example.cn",))
        self.assertEqual(b"cached", response.body)
        self.assertEqual("https://example.cn/feed/final/", response.url)
        self.assertTrue(response.from_cache)

    def test_robots_rules_are_applied_to_each_path(self):
        fetcher = Fetcher(self.state)
        requested = []

        def fake_request(url, hosts, use_cache=True, check_robots=True):
            requested.append(url)
            if url.endswith("/robots.txt"):
                return FetchResponse(url, 200, b"User-agent: *\nDisallow: /private\n")
            return FetchResponse(url, 200, b"public")

        fetcher._request = fake_request
        self.assertEqual(b"public", fetcher.fetch("https://example.cn/public", ("example.cn",)).body)
        with self.assertRaises(FetchError):
            fetcher.fetch("https://example.cn/private/article", ("example.cn",))
        self.assertNotIn("https://example.cn/private/article", requested)

    def test_allowlist_rejects_arbitrary_url_before_network(self):
        fetcher = Fetcher(self.state)
        with self.assertRaises(FetchError):
            fetcher.fetch("http://127.0.0.1/private", ("example.cn",))


class SummaryAndRenderingTests(unittest.TestCase):
    def test_word_count_ignores_urls_and_chinese_translation(self):
        self.assertEqual(2, visible_word_count("Tax 申报 https://example.cn/a-word-here guide"))

    def test_unknown_citation_and_official_pulse_are_rejected(self):
        item = make_item()
        digest = valid_digest(item)
        digest["developments"][0]["source_ids"] = ["invented"]
        with self.assertRaises(SummaryError):
            validate_digest(digest, [item])
        digest = valid_digest(item)
        digest["pulse"] = [{"text": "Unsupported official sentiment claim", "source_ids": [item.id]}]
        with self.assertRaises(SummaryError):
            validate_digest(digest, [item])

    def test_model_urls_are_rejected(self):
        item = make_item()
        digest = valid_digest(item, summary="See https://invented.example for more information")
        with self.assertRaises(SummaryError):
            validate_digest(digest, [item])

    def test_english_language_check_allows_retained_chinese_terms(self):
        item = make_item()
        digest = valid_digest(item)
        digest["headline"] = "数电发票 rollout guide"
        validate_english_language(digest)
        digest["headline"] = "完全中文的标题没有任何英文单词"
        with self.assertRaises(SummaryError):
            validate_english_language(digest)

    def test_html_escapes_model_and_source_text(self):
        item = make_item(title="Tax & bookkeeping <update>")
        digest = valid_digest(item, summary="A <script>alert(1)</script> string is escaped in email HTML.")
        rendered = render_digest(digest, [item], "2026-11-14")
        self.assertNotIn("<script>", rendered.html)
        self.assertIn("&lt;script&gt;", rendered.html)
        self.assertIn("Tax &amp; bookkeeping", rendered.html)
        self.assertLessEqual(rendered.word_count, 600)

    def test_overlong_first_answer_gets_one_shorter_correction(self):
        item = make_item()
        long_digest = valid_digest(item, summary="word " * 120)
        long_digest["developments"] *= 5
        short_digest = valid_digest(item)

        class FakeClient:
            def __init__(self):
                self.calls = []

            def generate(self, system, user, purpose):
                self.calls.append(purpose)
                if purpose == "initial-digest":
                    return "k-initial", json.dumps(long_digest)
                if purpose == "digest-repair":
                    return "k-repair", json.dumps(short_digest)
                return "k-translation", json.dumps(chinese_digest(item))

            def commit(self, cache_key, content):
                pass

        client = FakeClient()
        digest, rendered = create_digest([item], "2026-11-14", [], client)
        self.assertEqual(
            ["initial-digest", "digest-repair", "simplified-chinese-translation"], client.calls
        )
        self.assertEqual(short_digest, digest["en"])
        self.assertEqual(chinese_digest(item), digest["zh-Hans"])
        self.assertLessEqual(rendered.word_count, 600)

    def test_bilingual_email_places_chinese_below_english_without_using_word_budget(self):
        item = make_item()
        english = valid_digest(item)
        chinese = chinese_digest(item)
        chinese["headline"] += "更新" * 500
        rendered = render_bilingual_digest(english, chinese, [item], "2026-11-14")
        english_only = render_digest(english, [item], "2026-11-14")
        self.assertLess(rendered.text.index("A practical filing update"), rendered.text.index("实用纳税申报更新"))
        self.assertIn('section lang="zh-Hans"', rendered.html)
        self.assertEqual(1, rendered.text.count("SOURCES / 来源"))
        self.assertEqual(english_only.word_count, rendered.word_count)
        self.assertLessEqual(rendered.word_count, 600)

    def test_translation_must_preserve_citations_and_contain_chinese(self):
        item = make_item()
        english = valid_digest(item)
        chinese = chinese_digest(item)
        self.assertEqual(chinese, validate_translation(english, chinese, [item]))
        chinese["developments"][0]["source_ids"] = ["invented"]
        with self.assertRaises(SummaryError):
            validate_translation(english, chinese, [item])
        chinese = chinese_digest(item)
        chinese["headline"] = "Untranslated English headline"
        with self.assertRaises(SummaryError):
            validate_translation(english, chinese, [item])

    def test_translation_rejects_mostly_english_changed_numbers_and_dropped_caveats(self):
        item = make_item()
        english = valid_digest(
            item, summary="The 6 percent rule may not apply to every small business in 2026."
        )
        chinese = chinese_digest(item)
        chinese["headline"] = "This headline is mostly English 中文"
        with self.assertRaises(SummaryError):
            validate_translation(english, chinese, [item])

        chinese = chinese_digest(item)
        chinese["developments"][0]["summary"] = "2026年7%的规定可能不适用于所有小企业。"
        with self.assertRaises(SummaryError):
            validate_translation(english, chinese, [item])

        chinese["developments"][0]["summary"] = "2026年6%的规定适用于所有小企业。"
        with self.assertRaisesRegex(SummaryError, "negation"):
            validate_translation(english, chinese, [item])

        chinese["developments"][0]["summary"] = "2026年，6%的规定可能并不适用于所有小企业。"
        self.assertEqual(chinese, validate_translation(english, chinese, [item]))

    def test_long_documents_keep_late_market_specific_sections(self):
        text = "政策介绍。" + ("一般性内容。" * 2500) + "代理记账机构应提升执业质量并服务小微企业。"
        excerpt = _keyword_excerpt(text)
        self.assertLessEqual(len(excerpt), 6000)
        self.assertIn("代理记账机构", excerpt)
        self.assertIn("小微企业", excerpt)
        self.assertIn("政策介绍", excerpt)

    def test_no_news_does_not_call_model(self):
        digest, rendered = create_digest([], "2026-11-14", ["One unavailable source"], None)
        self.assertEqual({
            "en": empty_digest("en"), "zh-Hans": empty_digest("zh-Hans"),
        }, digest)
        self.assertIn("No new material", rendered.text)
        self.assertIn("未用旧闻填充篇幅", rendered.text)
        self.assertLessEqual(rendered.word_count, 600)

    def test_json_decoder_rejects_trailing_content(self):
        with self.assertRaises(SummaryError):
            decode_json_object('{"headline": "ok"} trailing')


class EmailTests(unittest.TestCase):
    def config(self):
        return SMTPConfig("smtp.example.com", 587, "starttls", "user", "password", "from@example.com", "to@example.com")

    def test_message_contains_utf8_plain_and_html_parts(self):
        message = build_message(self.config(), "申报 update", "中文 text", "<p>中文</p>")
        self.assertEqual("multipart/alternative", message.get_content_type())
        self.assertEqual("text/plain", message.get_payload(0).get_content_type())
        self.assertEqual("text/html", message.get_payload(1).get_content_type())
        self.assertEqual("申报 update", str(message["Subject"]))

    def test_starttls_login_and_send_are_used(self):
        events = []

        class FakeSMTP:
            def __init__(self, host, port, timeout):
                events.append((host, port, timeout))

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def ehlo(self):
                events.append("ehlo")

            def starttls(self, context):
                events.append("starttls")

            def login(self, username, password):
                events.append((username, password))

            def send_message(self, message):
                events.append(message)

        send_email(self.config(), "Subject", "text", "<p>html</p>", smtp_factory=FakeSMTP)
        self.assertEqual("starttls", events[2])
        self.assertIn(("user", "password"), events)
        self.assertEqual(2, events.count("ehlo"))
        self.assertIsInstance(events[-1], EmailMessage)


class LLMClientTests(unittest.TestCase):
    def test_response_and_authorization_are_cached(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = State(Path(temporary) / "state.sqlite3")
            client = OpenAICompatibleClient(
                state, base_url="https://llm.example/v1", model="test-model", api_key="secret"
            )
            calls = []

            class Response:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

                def read(self, limit):
                    return json.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode()

            class Opener:
                def open(self, request, timeout):
                    calls.append(request)
                    return Response()

            client.opener = Opener()
            first_key, first = client.generate("system", "user", "test")
            self.assertEqual(1, len(calls))
            self.assertEqual("Bearer secret", calls[0].get_header("Authorization"))
            second_key, second = client.generate("system", "user", "test")
            self.assertEqual(2, len(calls))
            client.commit(first_key, first)
            third_key, third = client.generate("system", "user", "test")
            self.assertEqual(2, len(calls))
            self.assertEqual((first_key, first), (third_key, third))
            state.close()


    def test_redirected_llm_endpoint_fails_without_forwarding_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = State(Path(temporary) / "state.sqlite3")
            client = OpenAICompatibleClient(
                state, base_url="https://llm.example/v1", model="test-model", api_key="secret"
            )

            class RedirectingOpener:
                def open(self, request, timeout):
                    raise urllib.error.HTTPError(
                        request.full_url, 302, "Found", {"Location": "http://other.example/"},
                        io.BytesIO(b"redirect"),
                    )

            client.opener = RedirectingOpener()
            with self.assertRaisesRegex(SummaryError, "redirects are disabled"):
                client.generate("system", "user", "redirect-test")
            state.close()


class CLIIntegrationTests(unittest.TestCase):
    def test_offline_preview_from_html_to_rendered_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            config = {
                "version": 1,
                "keywords": {"bookkeeping": ["代理记账"], "tax": ["纳税申报"]},
                "sources": [{
                    "id": "test_source", "name": "Test official", "type": "html_listing",
                    "url": "https://example.cn/list/", "allowed_hosts": ["example.cn"],
                    "category": "official", "language": "zh", "item_selector": "li",
                    "url_pattern": "/t[^/]+\\.html$", "detail_selectors": [".article"],
                    "fetch_detail": False,
                }],
            }
            (root / "data" / "sources.json").write_text(json.dumps(config), encoding="utf-8")
            body = "<ul><li><a href='/2026/t1.html'>代理记账纳税申报指南</a>2026-11-13<p>小规模纳税人申报说明。</p></li></ul>".encode()

            class FakeFetcher:
                def __init__(self, state):
                    pass

                def fetch(self, url, hosts):
                    return FetchResponse(url, 200, body)

            class FakeClient:
                def __init__(self, state):
                    pass

                def generate(self, system, user, purpose):
                    payload = json.loads(user)
                    if purpose == "simplified-chinese-translation":
                        english = payload["english_digest"]
                        return "k-zh", json.dumps({
                            "headline": "记账与纳税申报更新",
                            "developments": [{
                                "title": "新的纳税人指引",
                                "summary": "该列表介绍了记账和纳税申报指引。",
                                "connection": "小企业依靠准确账簿完成纳税申报。",
                                "source_ids": english["developments"][0]["source_ids"],
                            }],
                            "pulse": [], "learning_resource": None,
                        })
                    source_id = payload["items"][0]["id"]
                    return "k-en", json.dumps({
                        "headline": "Bookkeeping and filing update",
                        "developments": [{
                            "title": "New taxpayer guidance",
                            "summary": "The listing introduces bookkeeping and tax filing guidance.",
                            "connection": "A small business relies on accurate books to prepare the filing.",
                            "source_ids": [source_id],
                        }],
                        "pulse": [], "learning_resource": None,
                    })

                def commit(self, cache_key, content):
                    pass

            with mock.patch.object(cli, "Fetcher", FakeFetcher), mock.patch.object(cli, "OpenAICompatibleClient", FakeClient), mock.patch.object(cli, "utc_now", return_value=NOW):
                with redirect_stdout(io.StringIO()):
                    cli.main(["preview", "--date", "2026-1-1"], root=root)
            text = (root / "build" / "digests" / "2026-01-01.txt").read_text(encoding="utf-8")
            html = (root / "build" / "digests" / "2026-01-01.html").read_text(encoding="utf-8")
            self.assertIn("Bookkeeping and filing update", text)
            self.assertIn("记账与纳税申报更新", text)
            self.assertLess(text.index("Bookkeeping and filing update"), text.index("记账与纳税申报更新"))
            self.assertIn("https://example.cn/2026/t1.html", text)
            self.assertIn("<!doctype html>", html)
            self.assertIn('section lang="zh-Hans"', html)

            smtp_environment = {
                "DIGEST_SMTP_HOST": "smtp.example.com",
                "DIGEST_EMAIL_FROM": "from@example.com",
                "DIGEST_EMAIL_TO": "to@example.com",
            }
            with mock.patch.dict(os.environ, smtp_environment), \
                    mock.patch.object(cli, "Fetcher", FakeFetcher), \
                    mock.patch.object(cli, "OpenAICompatibleClient", FakeClient), \
                    mock.patch.object(cli, "utc_now", return_value=NOW), \
                    mock.patch.object(cli, "send_email", side_effect=smtplib.SMTPException("timeout")) as delivery:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    cli.main(["send", "--date", "2026-1-1"], root=root)
                self.assertEqual(1, delivery.call_count)
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    cli.main(["send", "--date", "2026-01-01"], root=root)
                self.assertEqual(1, delivery.call_count)

            with State(root / "build" / "state.sqlite3") as state:
                self.assertEqual("sending", state.get_run("2026-01-01")["status"])

            # A pending delivery from an earlier date blocks later scheduled sends.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main(["send", "--date", "2026-01-02"], root=root)

            with redirect_stdout(io.StringIO()):
                cli.main(["send", "--date", "2026-01-01", "--mark-sent"], root=root)
            with State(root / "build" / "state.sqlite3") as state:
                self.assertEqual("sent", state.get_run("2026-01-01")["status"])

            with mock.patch.dict(os.environ, smtp_environment), \
                    mock.patch.object(cli, "send_email", return_value=None) as delivery:
                with redirect_stdout(io.StringIO()):
                    cli.main(["send", "--date", "2026-01-01", "--force"], root=root)
                self.assertEqual(1, delivery.call_count)
            with State(root / "build" / "state.sqlite3") as state:
                self.assertEqual("sent", state.get_run("2026-01-01")["status"])


if __name__ == "__main__":
    unittest.main()
