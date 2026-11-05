"""Application service tests with network access mocked out."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from shiye.feeds import FetchResult, FeedError, ParsedEntry, ParsedFeed
from shiye.service import AppService, ServiceError


def make_result(
    *,
    url: str = "https://example.com/feed",
    final_url: str | None = None,
    status: int = 200,
    etag: str = 'W/"v1"',
    last_modified: str = "Wed, 01 Jan 2025 00:00:00 GMT",
    not_modified: bool = False,
) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=final_url or url,
        status=304 if not_modified else status,
        body=b"<rss/>",
        content_type="application/rss+xml",
        charset="utf-8",
        etag=etag,
        last_modified=last_modified,
        not_modified=not_modified,
    )


def make_parsed(
    *,
    title: str = "Example Feed",
    description: str = "Channel description",
    entries: list[ParsedEntry] | None = None,
) -> ParsedFeed:
    return ParsedFeed(
        title=title,
        site_url="https://example.com/",
        description=description,
        icon_url="https://example.com/icon.png",
        entries=entries
        if entries is not None
        else [
            ParsedEntry(
                guid="entry-1",
                url="https://example.com/post-1",
                title="Post one",
                author="Alice",
                published_at="2025-01-02T03:04:05Z",
                summary_html="<p>Short <b>summary</b>.</p>",
                content_html=(
                    '<p>Hello<script>alert(1)</script> world</p>'
                    '<img src="/pixel.png" width="1" height="1">'
                    '<a href="https://example.com/read?utm_source=rss">Read</a>'
                ),
            )
        ],
        kind="rss",
    )


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.service = AppService(self.temp.name, frontend_dir=self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.service.close)

    def subscribe(self, parsed: ParsedFeed | None = None, **kwargs):
        with mock.patch(
            "shiye.service.fetch_feed",
            return_value=(make_result(), parsed or make_parsed()),
        ):
            return self.service.subscribe(
                "https://example.com/feed", **kwargs
            )


class SubscribeTests(ServiceTestCase):
    def test_subscribe_creates_feed_and_sanitized_articles(self) -> None:
        result = self.subscribe()
        feed = result["feed"]
        self.assertEqual(feed["title"], "Example Feed")
        self.assertEqual(feed["description"], "Channel description")
        self.assertEqual(feed["site_url"], "https://example.com/")
        self.assertEqual(feed["icon_url"], "https://example.com/icon.png")
        self.assertEqual(feed["etag"], 'W/"v1"')
        self.assertEqual(result["new_articles"], 1)
        article = self.service.list_articles()["items"][0]
        self.assertEqual(article["title"], "Post one")
        self.assertEqual(article["author"], "Alice")
        self.assertEqual(article["feed_title"], "Example Feed")
        detail = self.service.get_article(int(article["id"]))
        self.assertNotIn("<script", detail["content"])
        self.assertNotIn("pixel.png", detail["content"])
        self.assertNotIn("utm_source", detail["content"])
        self.assertIn("https://example.com/read", detail["content"])
        self.assertEqual(detail["summary"], "Short summary.")

    def test_description_regression_for_feed_with_items(self) -> None:
        result = self.subscribe(make_parsed(description="Real description"))
        self.assertEqual(result["feed"]["description"], "Real description")

    def test_category_name_is_created(self) -> None:
        result = self.subscribe(category_name="Tech")
        self.assertEqual(result["feed"]["category_name"], "Tech")
        self.assertEqual(self.service.store.list_categories()[0]["feed_count"], 1)

    def test_category_id_must_exist(self) -> None:
        with self.assertRaises(ServiceError) as caught:
            self.subscribe(category_id=999)
        self.assertEqual(caught.exception.status, 404)

    def test_duplicate_feed_is_rejected(self) -> None:
        self.subscribe()
        with mock.patch(
            "shiye.service.fetch_feed",
            return_value=(make_result(), make_parsed()),
        ):
            with self.assertRaises(ServiceError) as caught:
                self.service.subscribe("https://example.com/feed")
        self.assertEqual(caught.exception.code, "duplicate_feed")
        self.assertEqual(caught.exception.status, 409)

    def test_invalid_url_is_rejected(self) -> None:
        with self.assertRaises(ServiceError) as caught:
            self.service.subscribe("ftp://example.com/feed")
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.code, "invalid_url")

    def test_fetch_error_is_422(self) -> None:
        with mock.patch(
            "shiye.service.fetch_feed",
            side_effect=FeedError("无法连接订阅源", "network_error"),
        ):
            with self.assertRaises(ServiceError) as caught:
                self.service.subscribe("https://example.com/feed")
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.code, "network_error")

    def test_future_dates_are_clamped(self) -> None:
        entry = ParsedEntry(
            guid="future",
            url="https://example.com/future",
            title="Future",
            author="",
            published_at="2999-01-01T00:00:00Z",
            summary_html="summary",
            content_html="<p>content</p>",
        )
        self.subscribe(make_parsed(entries=[entry]))
        article = self.service.list_articles()["items"][0]
        self.assertLess(article["published_at"], "2100-01-01")

    def test_missing_title_falls_back(self) -> None:
        result = self.subscribe(make_parsed(title=""))
        self.assertEqual(result["feed"]["title"], "example.com")


class RefreshTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.feed = self.subscribe()["feed"]
        self.feed_id = int(self.feed["id"])

    def test_refresh_adds_new_articles_and_updates_meta(self) -> None:
        entry = ParsedEntry(
            guid="entry-2",
            url="https://example.com/post-2",
            title="Post two",
            author="Bob",
            published_at="2025-01-03T00:00:00Z",
            summary_html="new",
            content_html="<p>new</p>",
        )
        parsed = make_parsed(title="Renamed Feed", entries=[entry])
        with mock.patch(
            "shiye.service.fetch_feed",
            return_value=(make_result(etag='W/"v2"'), parsed),
        ):
            result = self.service.refresh_feed(self.feed_id)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["new_articles"], 1)
        self.assertEqual(result["feed"]["title"], "Renamed Feed")
        self.assertEqual(result["feed"]["etag"], 'W/"v2"')
        self.assertEqual(result["feed"]["error_count"], 0)

    def test_not_modified_updates_fetch_time(self) -> None:
        with mock.patch(
            "shiye.service.fetch_feed",
            return_value=(make_result(not_modified=True), None),
        ):
            result = self.service.refresh_feed(self.feed_id)
        self.assertEqual(result["status"], "not_modified")
        self.assertEqual(result["new_articles"], 0)
        self.assertIsNotNone(result["feed"]["last_fetch_at"])

    def test_failure_records_backoff(self) -> None:
        with mock.patch(
            "shiye.service.fetch_feed",
            side_effect=FeedError("boom", "http_error"),
        ):
            with self.assertRaises(ServiceError):
                self.service.refresh_feed(self.feed_id)
        feed = self.service.store.get_feed(self.feed_id)
        self.assertEqual(feed["error_count"], 1)
        self.assertEqual(feed["last_error"], "boom")
        self.assertIsNotNone(feed["next_fetch_at"])

    def test_disabled_feed_cannot_refresh(self) -> None:
        self.service.update_feed(self.feed_id, {"disabled": True})
        with self.assertRaises(ServiceError) as caught:
            self.service.refresh_feed(self.feed_id)
        self.assertEqual(caught.exception.code, "feed_disabled")

    def test_missing_feed_is_404(self) -> None:
        with self.assertRaises(ServiceError) as caught:
            self.service.refresh_feed(99999)
        self.assertEqual(caught.exception.status, 404)


class ReadingTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        entries = [
            ParsedEntry(
                guid=f"entry-{index}",
                url=f"https://example.com/{index}",
                title=f"Article {index}",
                author="",
                published_at=f"2025-01-0{index}T00:00:00Z",
                summary_html=f"summary {index}",
                content_html=f"<p>content {index}</p>",
            )
            for index in (1, 2, 3)
        ]
        self.result = self.subscribe(make_parsed(entries=entries))
        self.feed_id = int(self.result["feed"]["id"])
        self.ids = [
            int(item["id"]) for item in self.service.list_articles()["items"]
        ]

    def test_list_and_search(self) -> None:
        self.assertEqual(self.service.list_articles()["total"], 3)
        self.assertEqual(self.service.list_articles(search="Article 2")["total"], 1)
        self.assertEqual(
            self.service.list_articles(feed_id=self.feed_id)["total"], 3
        )

    def test_mark_read_and_star(self) -> None:
        result = self.service.mark_read([self.ids[0]], read=True)
        self.assertEqual(result["changed"], 1)
        self.assertEqual(result["unread"], 2)
        self.assertEqual(
            self.service.list_articles(unread_only=True)["total"], 2
        )
        starred = self.service.toggle_star(self.ids[1], True)
        self.assertTrue(starred["article"]["is_starred"])
        self.assertEqual(starred["starred"], 1)
        self.assertEqual(
            self.service.list_articles(starred_only=True)["total"], 1
        )

    def test_mark_read_ignores_bad_ids(self) -> None:
        result = self.service.mark_read([self.ids[0], "bad", -1, self.ids[0]])
        self.assertEqual(result["changed"], 1)
        with self.assertRaises(ServiceError):
            self.service.mark_read("not-a-list")

    def test_mark_all_read(self) -> None:
        result = self.service.mark_all_read(feed_id=self.feed_id)
        self.assertEqual(result["changed"], 3)
        self.assertEqual(result["unread"], 0)

    def test_missing_article_is_404(self) -> None:
        with self.assertRaises(ServiceError):
            self.service.get_article(99999)
        with self.assertRaises(ServiceError):
            self.service.toggle_star(99999, True)


class ManagementTests(ServiceTestCase):
    def test_categories(self) -> None:
        category = self.service.create_category("Tech")
        self.assertEqual(category["name"], "Tech")
        renamed = self.service.rename_category(int(category["id"]), "News")
        self.assertEqual(renamed["name"], "News")
        with self.assertRaises(ServiceError):
            self.service.create_category("   ")
        self.service.delete_category(int(category["id"]))
        with self.assertRaises(ServiceError):
            self.service.delete_category(int(category["id"]))

    def test_update_feed_category_and_delete(self) -> None:
        feed = self.subscribe()["feed"]
        category = self.service.create_category("Tech")
        updated = self.service.update_feed(
            int(feed["id"]), {"category_id": category["id"], "disabled": True}
        )
        self.assertEqual(updated["category_name"], "Tech")
        self.assertTrue(updated["disabled"])
        updated = self.service.update_feed(int(feed["id"]), {"category_id": 0})
        self.assertIsNone(updated["category_id"])
        self.service.delete_feed(int(feed["id"]))
        self.assertEqual(self.service.list_articles()["total"], 0)

    def test_settings_are_clamped_and_pruning_runs(self) -> None:
        settings = self.service.set_settings({"refresh_interval": 1, "max_per_feed": 10})
        self.assertEqual(settings["refresh_interval"], 5)
        self.assertEqual(settings["max_per_feed"], 50)

    def test_health_and_stats(self) -> None:
        health = self.service.health()
        self.assertEqual(health["app"], "shiye")
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["feeds"], 0)
        stats = self.service.stats()
        self.assertEqual(stats["version"], health["version"])
        self.assertFalse(stats["refresh_running"])

    def test_opml_import_and_export(self) -> None:
        opml = """<?xml version="1.0" encoding="UTF-8"?>
        <opml version="2.0"><body>
          <outline text="Tech">
            <outline text="One" type="rss" xmlUrl="https://one.example/feed"/>
            <outline text="Two" type="rss" xmlUrl="https://two.example/feed"/>
          </outline>
        </body></opml>
        """
        with mock.patch.object(
            self.service, "request_refresh_all", return_value=False
        ):
            result = self.service.import_opml(opml)
        self.assertEqual(result, {"imported": 2, "skipped": 0, "total": 2})
        self.assertEqual(len(self.service.list_feeds()), 2)
        self.assertEqual(self.service.store.list_categories()[0]["name"], "Tech")
        exported = self.service.export_opml()
        self.assertIn("https://one.example/feed", exported)
        self.assertIn('text="Tech"', exported)
        with mock.patch.object(
            self.service, "request_refresh_all", return_value=False
        ):
            duplicate = self.service.import_opml(opml)
        self.assertEqual(duplicate["imported"], 0)
        self.assertEqual(duplicate["skipped"], 2)

    def test_import_rejects_empty(self) -> None:
        with self.assertRaises(ServiceError):
            self.service.import_opml("")
        with self.assertRaises(ServiceError):
            self.service.import_opml("<not-opml/>")

    def test_media_errors_are_mapped(self) -> None:
        with self.assertRaises(ServiceError) as caught:
            self.service.media("")
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.code, "invalid_url")


class SavedArticleTests(ServiceTestCase):
    PAGE = """
    <html><head>
      <title>稍后读测试文章 - 示例站</title>
      <meta property="og:site_name" content="示例站">
      <meta name="author" content="作者甲">
      <meta property="og:description" content="稍后读摘要">
    </head><body>
      <article>
        <p>这是一段足够长的正文，用于验证保存链接时能够提取出干净的阅读内容，并且去掉导航和广告。</p>
        <p>第二段继续补充内容，确保阅读时间与字数统计都有实际意义，而不是空值。</p>
        <p>第三段用来让正文长度稳稳超过阈值，减少测试对具体字数的敏感程度。</p>
      </article>
    </body></html>
    """

    def page_result(self, url: str = "https://example.com/story") -> FetchResult:
        return FetchResult(
            url=url,
            final_url=url,
            status=200,
            body=self.PAGE.encode("utf-8"),
            content_type="text/html",
            charset="utf-8",
        )

    def test_save_extracts_content_and_hides_internal_feed(self) -> None:
        with mock.patch(
            "shiye.service.fetch_page", return_value=(self.page_result(), self.PAGE)
        ):
            result = self.service.save_article("example.com/story")
        article = result["article"]
        self.assertTrue(result["created"])
        self.assertTrue(result["extracted"])
        self.assertEqual(article["title"], "稍后读测试文章")
        self.assertEqual(article["site_name"], "示例站")
        self.assertEqual(article["author"], "作者甲")
        self.assertIn("一段足够长的正文", article["content"])
        self.assertGreater(article["word_count"], 20)
        self.assertGreaterEqual(article["reading_minutes"], 1)
        self.assertEqual(self.service.list_feeds(), [])
        saved = self.service.list_articles(saved_only=True)
        self.assertEqual(saved["total"], 1)
        self.assertEqual(saved["items"][0]["feed_kind"], "saved")
        self.assertEqual(self.service.stats()["saved"], 1)
        self.assertEqual(self.service.health()["saved"], 1)

    def test_save_twice_reuses_existing_article(self) -> None:
        with mock.patch(
            "shiye.service.fetch_page", return_value=(self.page_result(), self.PAGE)
        ):
            first = self.service.save_article("https://example.com/story")
            second = self.service.save_article("https://example.com/story")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["article"]["id"], second["article"]["id"])
        self.assertEqual(self.service.stats()["saved"], 1)

    def test_fallback_saves_link_without_fetching(self) -> None:
        with mock.patch("shiye.service.fetch_page") as fetch:
            result = self.service.save_article(
                "https://example.com/later", fallback=True, title="稍后看的文章"
            )
            fetch.assert_not_called()
        article = result["article"]
        self.assertFalse(result["extracted"])
        self.assertEqual(article["title"], "稍后看的文章")
        self.assertEqual(article["content"], "")
        self.assertEqual(article["site_name"], "example.com")

    def test_reload_extracts_content_for_fallback_article(self) -> None:
        fallback = self.service.save_article(
            "https://example.com/story", fallback=True
        )
        article_id = int(fallback["article"]["id"])
        with mock.patch(
            "shiye.service.fetch_page", return_value=(self.page_result(), self.PAGE)
        ):
            article = self.service.reload_saved_content(article_id)
        self.assertIn("一段足够长的正文", article["content"])
        self.assertGreater(article["word_count"], 0)

    def test_extract_failure_maps_to_422(self) -> None:
        with mock.patch(
            "shiye.service.fetch_page",
            side_effect=FeedError("页面打不开", "network_error"),
        ):
            with self.assertRaises(ServiceError) as caught:
                self.service.save_article("https://example.com/broken")
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.code, "network_error")

    def test_only_saved_articles_can_be_deleted(self) -> None:
        self.subscribe()
        regular_id = int(self.service.list_articles()["items"][0]["id"])
        with self.assertRaises(ServiceError) as caught:
            self.service.delete_article(regular_id)
        self.assertEqual(caught.exception.code, "not_saved")
        saved = self.service.save_article(
            "https://example.com/later", fallback=True
        )
        result = self.service.delete_article(int(saved["article"]["id"]))
        self.assertTrue(result["deleted"])
        self.assertEqual(self.service.stats()["saved"], 0)

    def test_saved_feed_cannot_be_refreshed_or_deleted(self) -> None:
        feed_id = self.service.store.saved_feed_id()
        self.assertIsNotNone(feed_id)
        with self.assertRaises(ServiceError):
            self.service.refresh_feed(int(feed_id))
        with self.assertRaises(ServiceError):
            self.service.delete_feed(int(feed_id))


if __name__ == "__main__":
    unittest.main()
