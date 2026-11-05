"""SQLite persistence for Shiye."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Iterable

from .sanitize import html_to_text
from .util import clamp_int, iso_before, like_pattern, utc_now

SCHEMA_VERSION = 1

DEFAULT_SETTINGS: dict[str, Any] = {
    "refresh_interval": 30,
    "retention_days": 90,
    "max_per_feed": 500,
    "auto_refresh": True,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    site_url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    icon_url TEXT NOT NULL DEFAULT '',
    etag TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT '',
    last_fetch_at TEXT,
    last_success_at TEXT,
    next_fetch_at TEXT,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feeds_category ON feeds(category_id);
CREATE INDEX IF NOT EXISTS idx_feeds_next_fetch ON feeds(next_fetch_at);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    guid TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    is_starred INTEGER NOT NULL DEFAULT 0,
    search_text TEXT NOT NULL DEFAULT '',
    UNIQUE(feed_id, guid)
);

CREATE INDEX IF NOT EXISTS idx_articles_feed_published
    ON articles(feed_id, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_articles_published
    ON articles(published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_articles_unread
    ON articles(is_read, published_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_articles_starred
    ON articles(is_starred, published_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class StoreError(Exception):
    def __init__(self, message: str, code: str = "store_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            path, timeout=10, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=10000")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._connection.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                self._connection.executescript(_SCHEMA)
                self._connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self._connection.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.close()
            except sqlite3.Error:
                pass

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._connection.execute(sql, tuple(params))
            return [dict(row) for row in cursor.fetchall()]

    def _query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._connection.execute(sql, tuple(params))
            self._connection.commit()
            return cursor

    # ------------------------------------------------------------------ categories

    def list_categories(self) -> list[dict[str, Any]]:
        return self._query(
            """
            SELECT c.id, c.name, c.position,
                   (SELECT COUNT(*) FROM feeds f WHERE f.category_id = c.id)
                       AS feed_count,
                   (SELECT COUNT(*) FROM articles a
                      JOIN feeds f ON f.id = a.feed_id
                     WHERE f.category_id = c.id AND a.is_read = 0)
                       AS unread_count
              FROM categories c
             ORDER BY c.position ASC, c.name COLLATE NOCASE ASC
            """
        )

    def get_category(self, category_id: int) -> dict[str, Any] | None:
        return self._query_one(
            "SELECT id, name, position FROM categories WHERE id = ?", (category_id,)
        )

    def find_category(self, name: str) -> dict[str, Any] | None:
        return self._query_one(
            "SELECT id, name, position FROM categories WHERE name = ? COLLATE NOCASE",
            (name,),
        )

    def create_category(self, name: str) -> dict[str, Any]:
        now = utc_now()
        position = self._query_one(
            "SELECT COALESCE(MAX(position), 0) + 1 AS next FROM categories"
        )["next"]
        try:
            cursor = self._execute(
                "INSERT INTO categories (name, position, created_at) VALUES (?, ?, ?)",
                (name, position, now),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreError("分类名称已存在", "duplicate_category") from exc
        return self.get_category(int(cursor.lastrowid)) or {}

    def rename_category(self, category_id: int, name: str) -> dict[str, Any] | None:
        try:
            cursor = self._execute(
                "UPDATE categories SET name = ? WHERE id = ?", (name, category_id)
            )
        except sqlite3.IntegrityError as exc:
            raise StoreError("分类名称已存在", "duplicate_category") from exc
        if cursor.rowcount == 0:
            return None
        return self.get_category(category_id)

    def delete_category(self, category_id: int) -> bool:
        cursor = self._execute("DELETE FROM categories WHERE id = ?", (category_id,))
        return cursor.rowcount > 0

    # ----------------------------------------------------------------------- feeds

    _FEED_SELECT = """
        SELECT f.id, f.category_id, f.title, f.url, f.site_url, f.description,
               f.icon_url, f.etag, f.last_modified, f.last_fetch_at,
               f.last_success_at, f.next_fetch_at, f.error_count, f.last_error,
               f.disabled, f.created_at, f.updated_at,
               c.name AS category_name,
               (SELECT COUNT(*) FROM articles a WHERE a.feed_id = f.id)
                   AS article_count,
               (SELECT COUNT(*) FROM articles a
                 WHERE a.feed_id = f.id AND a.is_read = 0) AS unread_count
          FROM feeds f
          LEFT JOIN categories c ON c.id = f.category_id
    """

    def list_feeds(self) -> list[dict[str, Any]]:
        return self._query(
            self._FEED_SELECT
            + """
             ORDER BY c.position ASC, c.name COLLATE NOCASE ASC,
                      f.title COLLATE NOCASE ASC, f.id ASC
            """
        )

    def get_feed(self, feed_id: int) -> dict[str, Any] | None:
        return self._query_one(self._FEED_SELECT + " WHERE f.id = ?", (feed_id,))

    def get_feed_by_url(self, url: str) -> dict[str, Any] | None:
        return self._query_one(self._FEED_SELECT + " WHERE f.url = ?", (url,))

    def create_feed(
        self,
        *,
        title: str,
        url: str,
        site_url: str = "",
        description: str = "",
        icon_url: str = "",
        category_id: int | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        try:
            cursor = self._execute(
                """
                INSERT INTO feeds (
                    category_id, title, url, site_url, description, icon_url,
                    next_fetch_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    category_id,
                    title,
                    url,
                    site_url,
                    description,
                    icon_url,
                    now,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreError("该订阅地址已存在", "duplicate_feed") from exc
        return self.get_feed(int(cursor.lastrowid)) or {}

    def update_feed_meta(
        self,
        feed_id: int,
        *,
        title: str | None = None,
        site_url: str | None = None,
        description: str | None = None,
        icon_url: str | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        for name, value in (
            ("title", title),
            ("site_url", site_url),
            ("description", description),
            ("icon_url", icon_url),
        ):
            if value is not None:
                fields.append(f"{name} = ?")
                values.append(value)
        if not fields:
            return
        fields.append("updated_at = ?")
        values.append(utc_now())
        values.append(feed_id)
        self._execute(
            f"UPDATE feeds SET {', '.join(fields)} WHERE id = ?", values
        )

    def set_feed_category(self, feed_id: int, category_id: int | None) -> None:
        self._execute(
            "UPDATE feeds SET category_id = ?, updated_at = ? WHERE id = ?",
            (category_id, utc_now(), feed_id),
        )

    def set_feed_disabled(self, feed_id: int, disabled: bool) -> None:
        self._execute(
            "UPDATE feeds SET disabled = ?, updated_at = ? WHERE id = ?",
            (1 if disabled else 0, utc_now(), feed_id),
        )

    def record_fetch_success(
        self,
        feed_id: int,
        *,
        etag: str,
        last_modified: str,
        next_fetch_at: str,
    ) -> None:
        now = utc_now()
        self._execute(
            """
            UPDATE feeds
               SET etag = ?, last_modified = ?, last_fetch_at = ?,
                   last_success_at = ?, next_fetch_at = ?, error_count = 0,
                   last_error = '', updated_at = ?
             WHERE id = ?
            """,
            (etag, last_modified, now, now, next_fetch_at, now, feed_id),
        )

    def record_fetch_not_modified(
        self, feed_id: int, *, etag: str, last_modified: str, next_fetch_at: str
    ) -> None:
        now = utc_now()
        self._execute(
            """
            UPDATE feeds
               SET etag = ?, last_modified = ?, last_fetch_at = ?,
                   last_success_at = ?, next_fetch_at = ?, error_count = 0,
                   last_error = '', updated_at = ?
             WHERE id = ?
            """,
            (etag, last_modified, now, now, next_fetch_at, now, feed_id),
        )

    def record_fetch_failure(
        self, feed_id: int, *, error: str, next_fetch_at: str
    ) -> None:
        now = utc_now()
        self._execute(
            """
            UPDATE feeds
               SET last_fetch_at = ?, next_fetch_at = ?, error_count = error_count + 1,
                   last_error = ?, updated_at = ?
             WHERE id = ?
            """,
            (now, next_fetch_at, error[:1000], now, feed_id),
        )

    def delete_feed(self, feed_id: int) -> bool:
        cursor = self._execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
        return cursor.rowcount > 0

    def due_feeds(self, now: str) -> list[dict[str, Any]]:
        return self._query(
            self._FEED_SELECT
            + """
             WHERE f.disabled = 0
               AND (f.next_fetch_at IS NULL OR f.next_fetch_at <= ?)
             ORDER BY COALESCE(f.next_fetch_at, '') ASC, f.id ASC
            """,
            (now,),
        )

    def count_feeds(self) -> int:
        row = self._query_one("SELECT COUNT(*) AS total FROM feeds")
        return int(row["total"]) if row else 0

    # -------------------------------------------------------------------- articles

    @staticmethod
    def _search_text(title: str, author: str, summary: str, content: str) -> str:
        text = " ".join(
            [
                title or "",
                author or "",
                html_to_text(summary or "", limit=1500),
                html_to_text(content or "", limit=6000),
            ]
        )
        normalized = " ".join(text.split()).lower()
        return normalized[:8000]

    def upsert_articles(
        self, feed_id: int, entries: Iterable[dict[str, Any]]
    ) -> int:
        fetched_at = utc_now()
        inserted = 0
        with self._lock:
            for entry in entries:
                guid = str(entry.get("guid") or "").strip()
                if not guid:
                    continue
                title = str(entry.get("title") or "(无标题)")[:500]
                summary = str(entry.get("summary") or "")
                content = str(entry.get("content") or "")
                author = str(entry.get("author") or "")[:200]
                published = str(entry.get("published_at") or fetched_at)
                url = str(entry.get("url") or "")[:2048]
                search_text = self._search_text(title, author, summary, content)
                cursor = self._connection.execute(
                    """
                    INSERT INTO articles (
                        feed_id, guid, url, title, author, summary, content,
                        published_at, fetched_at, search_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(feed_id, guid) DO NOTHING
                    """,
                    (
                        feed_id,
                        guid,
                        url,
                        title,
                        author,
                        summary,
                        content,
                        published,
                        fetched_at,
                        search_text,
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                    continue
                self._connection.execute(
                    """
                    UPDATE articles
                       SET url = ?, title = ?, author = ?, summary = ?,
                           content = ?, published_at = ?, search_text = ?
                     WHERE feed_id = ? AND guid = ?
                    """,
                    (
                        url,
                        title,
                        author,
                        summary,
                        content,
                        published,
                        search_text,
                        feed_id,
                        guid,
                    ),
                )
            self._connection.commit()
        return inserted

    def _article_filters(
        self,
        *,
        feed_id: int | None,
        category_id: int | None,
        unread_only: bool,
        starred_only: bool,
        search: str,
        since: str | None,
    ) -> tuple[str, list[Any]]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if feed_id is not None:
            clauses.append("a.feed_id = ?")
            params.append(feed_id)
        elif category_id is not None:
            clauses.append("a.feed_id IN (SELECT id FROM feeds WHERE category_id = ?)")
            params.append(category_id)
        if unread_only:
            clauses.append("a.is_read = 0")
        if starred_only:
            clauses.append("a.is_starred = 1")
        if search:
            clauses.append("a.search_text LIKE ? ESCAPE '\\'")
            params.append(like_pattern(search.strip().lower()))
        if since:
            clauses.append("a.published_at >= ?")
            params.append(since)
        return " AND ".join(clauses), params

    def list_articles(
        self,
        *,
        feed_id: int | None = None,
        category_id: int | None = None,
        unread_only: bool = False,
        starred_only: bool = False,
        search: str = "",
        since: str | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        where, params = self._article_filters(
            feed_id=feed_id,
            category_id=category_id,
            unread_only=unread_only,
            starred_only=starred_only,
            search=search,
            since=since,
        )
        total_row = self._query_one(
            f"SELECT COUNT(*) AS total FROM articles a WHERE {where}", params
        )
        rows = self._query(
            f"""
            SELECT a.id, a.feed_id, f.title AS feed_title, f.icon_url AS feed_icon,
                   a.title, a.author, a.summary, a.url, a.published_at,
                   a.fetched_at, a.is_read, a.is_starred
              FROM articles a
              JOIN feeds f ON f.id = a.feed_id
             WHERE {where}
             ORDER BY a.published_at DESC, a.id DESC
             LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        for row in rows:
            row["is_read"] = bool(row["is_read"])
            row["is_starred"] = bool(row["is_starred"])
        return {
            "items": rows,
            "total": int(total_row["total"]) if total_row else 0,
            "limit": limit,
            "offset": offset,
        }

    def get_article(self, article_id: int) -> dict[str, Any] | None:
        row = self._query_one(
            """
            SELECT a.id, a.feed_id, f.title AS feed_title, f.icon_url AS feed_icon,
                   f.site_url AS feed_site_url, a.title, a.author, a.summary,
                   a.content, a.url, a.published_at, a.fetched_at,
                   a.is_read, a.is_starred
              FROM articles a
              JOIN feeds f ON f.id = a.feed_id
             WHERE a.id = ?
            """,
            (article_id,),
        )
        if row:
            row["is_read"] = bool(row["is_read"])
            row["is_starred"] = bool(row["is_starred"])
        return row

    def mark_read(self, article_ids: list[int], read: bool = True) -> int:
        if not article_ids:
            return 0
        placeholders = ",".join("?" for _ in article_ids)
        cursor = self._execute(
            f"UPDATE articles SET is_read = ? WHERE id IN ({placeholders})",
            [1 if read else 0] + list(article_ids),
        )
        return cursor.rowcount

    def toggle_star(self, article_id: int, starred: bool | None = None) -> dict[str, Any] | None:
        article = self.get_article(article_id)
        if article is None:
            return None
        value = (not article["is_starred"]) if starred is None else bool(starred)
        self._execute(
            "UPDATE articles SET is_starred = ? WHERE id = ?",
            (1 if value else 0, article_id),
        )
        article["is_starred"] = value
        return article

    def mark_all_read(
        self, *, feed_id: int | None = None, category_id: int | None = None
    ) -> int:
        clauses = ["is_read = 0"]
        params: list[Any] = []
        if feed_id is not None:
            clauses.append("feed_id = ?")
            params.append(feed_id)
        elif category_id is not None:
            clauses.append("feed_id IN (SELECT id FROM feeds WHERE category_id = ?)")
            params.append(category_id)
        cursor = self._execute(
            f"UPDATE articles SET is_read = 1 WHERE {' AND '.join(clauses)}", params
        )
        return cursor.rowcount

    def prune(self, retention_days: int, max_per_feed: int) -> int:
        deleted = 0
        if retention_days > 0:
            cutoff = iso_before(retention_days)
            cursor = self._execute(
                """
                DELETE FROM articles
                 WHERE is_read = 1 AND is_starred = 0 AND published_at < ?
                """,
                (cutoff,),
            )
            deleted += max(0, cursor.rowcount)
        if max_per_feed > 0:
            feed_ids = [
                row["id"] for row in self._query("SELECT id FROM feeds")
            ]
            for feed_id in feed_ids:
                cursor = self._execute(
                    """
                    DELETE FROM articles
                     WHERE id IN (
                         SELECT id FROM articles
                          WHERE feed_id = ? AND is_read = 1 AND is_starred = 0
                          ORDER BY published_at DESC, id DESC
                          LIMIT -1 OFFSET ?
                     )
                    """,
                    (feed_id, max_per_feed),
                )
                deleted += max(0, cursor.rowcount)
        return deleted

    def count_articles(self) -> int:
        row = self._query_one("SELECT COUNT(*) AS total FROM articles")
        return int(row["total"]) if row else 0

    # -------------------------------------------------------------------- settings

    def _coerce_settings(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "refresh_interval": clamp_int(
                raw.get("refresh_interval"),
                DEFAULT_SETTINGS["refresh_interval"],
                5,
                1440,
            ),
            "retention_days": clamp_int(
                raw.get("retention_days"), DEFAULT_SETTINGS["retention_days"], 0, 3650
            ),
            "max_per_feed": clamp_int(
                raw.get("max_per_feed"), DEFAULT_SETTINGS["max_per_feed"], 50, 5000
            ),
            "auto_refresh": bool(
                raw.get("auto_refresh", DEFAULT_SETTINGS["auto_refresh"])
            ),
        }

    def get_settings(self) -> dict[str, Any]:
        rows = self._query("SELECT key, value FROM settings")
        raw: dict[str, Any] = {}
        for row in rows:
            try:
                raw[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                continue
        return self._coerce_settings(raw)

    def set_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings()
        for key in DEFAULT_SETTINGS:
            if key in patch:
                current[key] = patch[key]
        current = self._coerce_settings(current)
        with self._lock:
            for key, value in current.items():
                self._connection.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )
            self._connection.commit()
        return current

    # ------------------------------------------------------------------------ stats

    def stats(self) -> dict[str, Any]:
        feeds = self._query_one("SELECT COUNT(*) AS total FROM feeds")["total"]
        categories = self._query_one(
            "SELECT COUNT(*) AS total FROM categories"
        )["total"]
        articles = self._query_one("SELECT COUNT(*) AS total FROM articles")["total"]
        unread = self._query_one(
            "SELECT COUNT(*) AS total FROM articles WHERE is_read = 0"
        )["total"]
        starred = self._query_one(
            "SELECT COUNT(*) AS total FROM articles WHERE is_starred = 1"
        )["total"]
        last_refresh = self._query_one(
            "SELECT MAX(last_fetch_at) AS last FROM feeds"
        )["last"]
        try:
            size = os.path.getsize(self.path)
            size += os.path.getsize(self.path + "-wal") if os.path.exists(
                self.path + "-wal"
            ) else 0
        except OSError:
            size = 0
        return {
            "feeds": int(feeds),
            "categories": int(categories),
            "articles": int(articles),
            "unread": int(unread),
            "starred": int(starred),
            "last_refresh_at": last_refresh,
            "database_bytes": size,
        }
