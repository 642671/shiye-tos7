"""SQLite store tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from shiye.store import DEFAULT_SETTINGS, Store, StoreError
from shiye.util import iso_after, iso_before


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "test.db"))

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def add_feed(self, title: str = "Feed", url: str = "https://example.com/feed"):
        return self.store.create_feed(
            title=title,
            url=url,
            site_url="https://example.com/",
            description="desc",
            icon_url="https://example.com/icon.png",
        )

    def add_entry(
        self,
        feed_id: int,
        guid: str,
        *,
        title: str = "Article",
        published: str | None = None,
        summary: str = "Summary",
        content: str = "<p>Body</p>",
        author: str = "Author",
    ) -> int:
        self.store.upsert_articles(
            feed_id,
            [
                {
                    "guid": guid,
                    "url": f"https://example.com/{guid}",
                    "title": title,
                    "author": author,
                    "summary": summary,
                    "content": content,
                    "published_at": published or iso_after(),
                }
            ],
        )
        article = self.store.list_articles(search=title)["items"][0]
        return int(article["id"])


class CategoryTests(StoreTestCase):
    def test_create_find_rename_delete(self) -> None:
        category = self.store.create_category("Tech")
        self.assertEqual(category["name"], "Tech")
        self.assertEqual(self.store.find_category("tech")["id"], category["id"])
        renamed = self.store.rename_category(category["id"], "News")
        self.assertEqual(renamed["name"], "News")
        self.assertTrue(self.store.delete_category(category["id"]))
        self.assertFalse(self.store.delete_category(category["id"]))
        self.assertIsNone(self.store.get_category(category["id"]))

    def test_duplicate_names_are_rejected_case_insensitively(self) -> None:
        self.store.create_category("Tech")
        with self.assertRaises(StoreError) as caught:
            self.store.create_category("tech")
        self.assertEqual(caught.exception.code, "duplicate_category")

    def test_category_counts_and_delete_sets_feed_null(self) -> None:
        category = self.store.create_category("Tech")
        feed = self.store.create_feed(
            title="Feed", url="https://example.com/feed", category_id=category["id"]
        )
        self.add_entry(int(feed["id"]), "a")
        listed = self.store.list_categories()[0]
        self.assertEqual(listed["feed_count"], 1)
        self.assertEqual(listed["unread_count"], 1)
        self.store.delete_category(category["id"])
        self.assertIsNone(self.store.get_feed(int(feed["id"]))["category_id"])


class FeedTests(StoreTestCase):
    def test_create_and_duplicate(self) -> None:
        feed = self.add_feed()
        self.assertEqual(feed["title"], "Feed")
        self.assertEqual(feed["article_count"], 0)
        with self.assertRaises(StoreError) as caught:
            self.store.create_feed(title="Again", url="https://example.com/feed")
        self.assertEqual(caught.exception.code, "duplicate_feed")

    def test_meta_and_category_updates(self) -> None:
        feed = self.add_feed()
        category = self.store.create_category("Tech")
        self.store.update_feed_meta(
            int(feed["id"]), title="New", site_url="https://new.example/"
        )
        self.store.set_feed_category(int(feed["id"]), category["id"])
        self.store.set_feed_disabled(int(feed["id"]), True)
        updated = self.store.get_feed(int(feed["id"]))
        self.assertEqual(updated["title"], "New")
        self.assertEqual(updated["site_url"], "https://new.example/")
        self.assertEqual(updated["category_id"], category["id"])
        self.assertEqual(updated["category_name"], "Tech")
        self.assertTrue(updated["disabled"])

    def test_due_feeds_skip_disabled_and_future(self) -> None:
        first = self.add_feed("A", "https://example.com/a")
        self.add_feed("B", "https://example.com/b")
        future = self.add_feed("C", "https://example.com/c")
        self.store.record_fetch_success(
            int(future["id"]),
            etag="",
            last_modified="",
            next_fetch_at=iso_after(days=1),
        )
        self.store.set_feed_disabled(int(first["id"]), True)
        due = self.store.due_feeds(iso_after())
        self.assertEqual([feed["title"] for feed in due], ["B"])

    def test_fetch_records(self) -> None:
        feed = self.add_feed()
        self.store.record_fetch_failure(
            int(feed["id"]), error="boom", next_fetch_at=iso_after(minutes=5)
        )
        failed = self.store.get_feed(int(feed["id"]))
        self.assertEqual(failed["error_count"], 1)
        self.assertEqual(failed["last_error"], "boom")
        self.assertIsNotNone(failed["last_fetch_at"])
        self.store.record_fetch_success(
            int(feed["id"]),
            etag='W/"1"',
            last_modified="date",
            next_fetch_at=iso_after(minutes=30),
        )
        success = self.store.get_feed(int(feed["id"]))
        self.assertEqual(success["error_count"], 0)
        self.assertEqual(success["etag"], 'W/"1"')
        self.assertEqual(success["last_error"], "")

    def test_delete_cascades_articles(self) -> None:
        feed = self.add_feed()
        self.add_entry(int(feed["id"]), "a")
        self.assertTrue(self.store.delete_feed(int(feed["id"])))
        self.assertEqual(self.store.count_articles(), 0)
        self.assertFalse(self.store.delete_feed(int(feed["id"])))

    def test_count_feeds(self) -> None:
        self.add_feed("A", "https://example.com/a")
        self.add_feed("B", "https://example.com/b")
        self.assertEqual(self.store.count_feeds(), 2)


class ArticleTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.feed = self.add_feed()
        self.feed_id = int(self.feed["id"])
        self.first = self.add_entry(
            self.feed_id,
            "a",
            title="Alpha",
            summary="One two three",
            content="<p>Alpha body</p>",
            published="2024-01-01T00:00:00Z",
        )
        self.second = self.add_entry(
            self.feed_id,
            "b",
            title="Beta",
            summary="Four five",
            content="<p>Beta body</p>",
            published="2024-02-01T00:00:00Z",
        )

    def test_upsert_is_idempotent_and_updates_content(self) -> None:
        inserted = self.store.upsert_articles(
            self.feed_id,
            [
                {
                    "guid": "a",
                    "url": "https://example.com/updated",
                    "title": "Alpha updated",
                    "summary": "New summary",
                    "content": "<p>New body</p>",
                    "published_at": "2024-01-02T00:00:00Z",
                }
            ],
        )
        self.assertEqual(inserted, 0)
        article = self.store.list_articles(search="Alpha updated")["items"][0]
        self.assertEqual(article["url"], "https://example.com/updated")
        self.assertEqual(self.store.count_articles(), 2)

    def test_list_orders_newest_first(self) -> None:
        result = self.store.list_articles()
        self.assertEqual([item["title"] for item in result["items"]], ["Beta", "Alpha"])
        self.assertEqual(result["total"], 2)

    def test_filters(self) -> None:
        self.store.mark_read([self.second], read=True)
        self.assertEqual(
            [item["title"] for item in self.store.list_articles(unread_only=True)["items"]],
            ["Alpha"],
        )
        self.store.toggle_star(self.second, True)
        self.assertEqual(
            [item["title"] for item in self.store.list_articles(starred_only=True)["items"]],
            ["Beta"],
        )
        self.assertEqual(self.store.list_articles(search="alpha")["total"], 1)
        self.assertEqual(self.store.list_articles(search="BODY")["total"], 2)
        self.assertEqual(
            self.store.list_articles(since="2024-01-15T00:00:00Z")["total"], 1
        )
        self.assertEqual(self.store.list_articles(limit=1, offset=1)["items"][0]["title"], "Alpha")

    def test_search_handles_like_wildcards(self) -> None:
        self.add_entry(self.feed_id, "percent", title="100% pure")
        self.assertEqual(self.store.list_articles(search="%")["total"], 1)
        self.assertEqual(self.store.list_articles(search="_")["total"], 0)

    def test_category_and_feed_filters(self) -> None:
        other = self.store.create_category("Other")
        other_feed = self.store.create_feed(
            title="Other", url="https://other.example/feed", category_id=other["id"]
        )
        self.add_entry(int(other_feed["id"]), "x", title="Other article")
        self.assertEqual(self.store.list_articles(feed_id=self.feed_id)["total"], 2)
        self.assertEqual(
            self.store.list_articles(category_id=other["id"])["total"], 1
        )

    def test_detail_and_missing(self) -> None:
        detail = self.store.get_article(self.first)
        self.assertEqual(detail["feed_title"], "Feed")
        self.assertEqual(detail["feed_site_url"], "https://example.com/")
        self.assertIn("Alpha body", detail["content"])
        self.assertIsNone(self.store.get_article(99999))

    def test_mark_read_and_toggle_star(self) -> None:
        changed = self.store.mark_read([self.first, self.second], read=True)
        self.assertEqual(changed, 2)
        self.assertEqual(self.store.stats()["unread"], 0)
        self.assertEqual(self.store.mark_read([self.first], read=False), 1)
        self.assertEqual(self.store.stats()["unread"], 1)
        starred = self.store.toggle_star(self.first, True)
        self.assertTrue(starred["is_starred"])
        toggled = self.store.toggle_star(self.first)
        self.assertFalse(toggled["is_starred"])
        self.assertIsNone(self.store.toggle_star(99999))

    def test_mark_all_read_scopes(self) -> None:
        changed = self.store.mark_all_read(feed_id=self.feed_id)
        self.assertEqual(changed, 2)
        self.assertEqual(self.store.stats()["unread"], 0)
        self.assertEqual(self.store.mark_all_read(feed_id=self.feed_id), 0)

    def test_prune_retention_keeps_starred(self) -> None:
        old_read = self.add_entry(
            self.feed_id,
            "old",
            title="Old read",
            published=iso_before(400),
        )
        old_starred = self.add_entry(
            self.feed_id,
            "old-star",
            title="Old starred",
            published=iso_before(400),
        )
        self.store.mark_read([old_read, old_starred], read=True)
        self.store.toggle_star(old_starred, True)
        deleted = self.store.prune(90, 0)
        self.assertGreaterEqual(deleted, 1)
        titles = {item["title"] for item in self.store.list_articles()["items"]}
        self.assertNotIn("Old read", titles)
        self.assertIn("Old starred", titles)

    def test_prune_max_per_feed(self) -> None:
        self.store.mark_read([self.first, self.second], read=True)
        deleted = self.store.prune(0, 1)
        self.assertEqual(deleted, 1)
        self.assertEqual(self.store.count_articles(), 1)


class SettingsTests(StoreTestCase):
    def test_defaults(self) -> None:
        self.assertEqual(self.store.get_settings(), DEFAULT_SETTINGS)

    def test_set_and_clamp(self) -> None:
        settings = self.store.set_settings(
            {
                "refresh_interval": 1,
                "retention_days": -5,
                "max_per_feed": 999999,
                "auto_refresh": False,
                "unknown": "ignored",
            }
        )
        self.assertEqual(settings["refresh_interval"], 5)
        self.assertEqual(settings["retention_days"], 0)
        self.assertEqual(settings["max_per_feed"], 5000)
        self.assertFalse(settings["auto_refresh"])
        self.assertEqual(self.store.get_settings(), settings)

    def test_partial_update_preserves_other_keys(self) -> None:
        self.store.set_settings({"refresh_interval": 60})
        updated = self.store.set_settings({"auto_refresh": False})
        self.assertEqual(updated["refresh_interval"], 60)
        self.assertFalse(updated["auto_refresh"])

    def test_invalid_value_uses_defaults(self) -> None:
        settings = self.store.set_settings({"refresh_interval": "abc"})
        self.assertEqual(settings["refresh_interval"], 30)


class StatsTests(StoreTestCase):
    def test_stats(self) -> None:
        category = self.store.create_category("Tech")
        feed = self.store.create_feed(
            title="Feed", url="https://example.com/feed", category_id=category["id"]
        )
        self.add_entry(int(feed["id"]), "a")
        self.add_entry(int(feed["id"]), "b")
        stats = self.store.stats()
        self.assertEqual(stats["feeds"], 1)
        self.assertEqual(stats["categories"], 1)
        self.assertEqual(stats["articles"], 2)
        self.assertEqual(stats["unread"], 2)
        self.assertEqual(stats["starred"], 0)
        self.assertGreater(stats["database_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
