"""Application logic for Yuii Reader."""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from . import APP_ID, __version__
from .feeds import (
    FETCH_TIMEOUT,
    FeedError,
    FetchedImage,
    OpmlEntry,
    ParsedFeed,
    build_opml,
    fetch_feed,
    fetch_image,
    parse_opml,
    normalize_feed_url,
)
from .sanitize import html_to_text, sanitize_fragment, summarize_html
from .store import Store, StoreError
from .util import clamp_int, clean_text, iso_after, parse_iso, utc_now

LOGGER = logging.getLogger("yuii_reader.service")
MAX_MARK_IDS = 500
MAX_SEARCH_CHARS = 100

_BACKOFF_MINUTES = (5, 15, 60, 180, 360)


class ServiceError(Exception):
    def __init__(
        self, message: str, code: str = "bad_request", status: int = 400
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


class AppService:
    def __init__(self, data_dir: str, frontend_dir: str = "") -> None:
        self.data_dir = data_dir
        self.frontend_dir = frontend_dir
        os.makedirs(data_dir, exist_ok=True)
        self.store = Store(os.path.join(data_dir, "yuii-reader.db"))
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._executor_lock = threading.Lock()
        self._active_feeds: set[int] = set()
        self._active_lock = threading.Lock()
        self._refresh_all_lock = threading.Lock()
        self._refresh_all_running = False
        self._prune_lock = threading.Lock()
        self._last_prune = 0.0
        self._started = False

    # -------------------------------------------------------------------- lifecycle

    def start_background(self) -> None:
        with self._executor_lock:
            if self._started:
                return
            self._started = True
            self._executor = ThreadPoolExecutor(
                max_workers=3, thread_name_prefix="yuii-fetch"
            )
        self._scheduler = threading.Thread(
            target=self._scheduler_loop, name="yuii-scheduler", daemon=True
        )
        self._scheduler.start()

    def close(self) -> None:
        self._stop.set()
        scheduler = self._scheduler
        if scheduler and scheduler.is_alive():
            scheduler.join(timeout=3)
        with self._executor_lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)
        self.store.close()

    def _ensure_executor(self) -> ThreadPoolExecutor:
        with self._executor_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=3, thread_name_prefix="yuii-fetch"
                )
            return self._executor

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(20):
            try:
                settings = self.store.get_settings()
                if settings["auto_refresh"]:
                    due = self.store.due_feeds(utc_now())[:30]
                    for feed in due:
                        if self._stop.is_set():
                            break
                        self._ensure_executor().submit(
                            self._refresh_feed_safe, feed["id"]
                        )
                self.maybe_prune()
            except Exception:  # pragma: no cover - defensive
                LOGGER.exception("scheduler loop failed")

    # ----------------------------------------------------------------------- helpers

    @staticmethod
    def _http_url(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            parts = urllib.parse.urlsplit(text)
        except ValueError:
            return ""
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return ""
        if parts.username or parts.password:
            return ""
        return text[:2048]

    @staticmethod
    def _hostname(url: str) -> str:
        try:
            host = urllib.parse.urlsplit(url).hostname or ""
        except ValueError:
            host = ""
        return host or "未命名订阅"

    def _next_fetch_at(self, minutes: int | None = None) -> str:
        if minutes is None:
            minutes = self.store.get_settings()["refresh_interval"]
        return iso_after(minutes=minutes)

    def _backoff(self, error_count: int) -> str:
        index = min(max(error_count, 0), len(_BACKOFF_MINUTES) - 1)
        return iso_after(minutes=_BACKOFF_MINUTES[index])

    def _resolve_category(
        self, category_id: Any = None, category_name: Any = None
    ) -> int | None:
        if category_id not in (None, "", 0, "0"):
            try:
                parsed = int(category_id)
            except (TypeError, ValueError) as exc:
                raise ServiceError("分类参数无效", "invalid_category") from exc
            if self.store.get_category(parsed) is None:
                raise ServiceError("分类不存在", "category_not_found", 404)
            return parsed
        name = clean_text(category_name, 60)
        if not name:
            return None
        existing = self.store.find_category(name)
        if existing:
            return int(existing["id"])
        try:
            created = self.store.create_category(name)
        except StoreError as exc:
            raise ServiceError(exc.message, exc.code, 409) from exc
        return int(created["id"])

    # ------------------------------------------------------------------ subscription

    def subscribe(
        self,
        raw_url: Any,
        *,
        category_id: Any = None,
        category_name: Any = None,
        title: Any = "",
    ) -> dict[str, Any]:
        try:
            requested = normalize_feed_url(raw_url)
        except FeedError as exc:
            raise ServiceError(exc.message, exc.code, 400) from exc
        if self.store.get_feed_by_url(requested):
            raise ServiceError("该订阅地址已存在", "duplicate_feed", 409)
        try:
            result, parsed = fetch_feed(requested)
        except FeedError as exc:
            raise ServiceError(exc.message, exc.code, 422) from exc
        if parsed is None:
            raise ServiceError("订阅源没有返回内容", "empty_feed", 422)

        final_url = requested
        try:
            final_url = normalize_feed_url(result.final_url)
        except FeedError:
            final_url = requested
        if self.store.get_feed_by_url(final_url):
            raise ServiceError("该订阅地址已存在", "duplicate_feed", 409)

        feed_title = clean_text(parsed.title, 500) or clean_text(title, 500)
        if not feed_title:
            feed_title = self._hostname(final_url)
        resolved_category = self._resolve_category(category_id, category_name)
        site_url = self._http_url(parsed.site_url)
        icon_url = self._http_url(parsed.icon_url)
        try:
            feed = self.store.create_feed(
                title=feed_title,
                url=final_url,
                site_url=site_url,
                description=clean_text(parsed.description, 1000),
                icon_url=icon_url,
                category_id=resolved_category,
            )
        except StoreError as exc:
            raise ServiceError(exc.message, exc.code, 409) from exc

        entries = self._prepare_entries(parsed, feed, final_url)
        inserted = self.store.upsert_articles(int(feed["id"]), entries)
        self.store.record_fetch_success(
            int(feed["id"]),
            etag=result.etag,
            last_modified=result.last_modified,
            next_fetch_at=self._next_fetch_at(),
        )
        LOGGER.info(
            "subscribed feed id=%s url=%s new=%s", feed["id"], final_url, inserted
        )
        return {
            "feed": self.store.get_feed(int(feed["id"])),
            "new_articles": inserted,
        }

    def _prepare_entries(
        self, parsed: ParsedFeed, feed: dict[str, Any], feed_url: str
    ) -> list[dict[str, Any]]:
        now = utc_now()
        now_dt = parse_iso(now)
        limit_dt = now_dt + timedelta(days=1) if now_dt else None
        entries: list[dict[str, Any]] = []
        for entry in parsed.entries:
            base = entry.url or feed.get("site_url") or feed_url
            content = sanitize_fragment(entry.content_html, base_url=base)
            summary_source = entry.summary_html or entry.content_html
            summary = summarize_html(summary_source, base_url=base, limit=600)
            if not summary:
                summary = html_to_text(content, limit=500)
            published = entry.published_at or now
            published_dt = parse_iso(published)
            if published_dt is not None and limit_dt is not None:
                if published_dt > limit_dt:
                    published = now
            title = clean_text(entry.title, 500) or "(无标题)"
            entries.append(
                {
                    "guid": entry.guid,
                    "url": self._http_url(entry.url),
                    "title": title,
                    "author": clean_text(entry.author, 200),
                    "summary": summary,
                    "content": content,
                    "published_at": published,
                }
            )
        return entries

    def _feed_updates(
        self, parsed: ParsedFeed, feed: dict[str, Any]
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        title = clean_text(parsed.title, 500)
        if (
            title
            and title != feed.get("title")
            and title not in {feed.get("url"), feed.get("site_url")}
        ):
            updates["title"] = title
        site = self._http_url(parsed.site_url)
        if site and site != feed.get("site_url"):
            updates["site_url"] = site
        description = clean_text(parsed.description, 1000)
        if description and description != feed.get("description"):
            updates["description"] = description
        icon = self._http_url(parsed.icon_url)
        if icon and icon != feed.get("icon_url"):
            updates["icon_url"] = icon
        return updates

    # ----------------------------------------------------------------------- refresh

    def refresh_feed(self, feed_id: int) -> dict[str, Any]:
        feed = self.store.get_feed(feed_id)
        if feed is None:
            raise ServiceError("订阅不存在", "feed_not_found", 404)
        if feed["disabled"]:
            raise ServiceError("该订阅已暂停", "feed_disabled", 409)
        with self._active_lock:
            if feed_id in self._active_feeds:
                raise ServiceError("该订阅正在刷新", "refresh_busy", 409)
            self._active_feeds.add(feed_id)
        try:
            try:
                result, parsed = fetch_feed(
                    feed["url"],
                    etag=feed.get("etag") or "",
                    last_modified=feed.get("last_modified") or "",
                    timeout=FETCH_TIMEOUT,
                )
            except FeedError as exc:
                self.store.record_fetch_failure(
                    feed_id,
                    error=exc.message,
                    next_fetch_at=self._backoff(int(feed["error_count"])),
                )
                raise ServiceError(exc.message, exc.code, 422) from exc
            if parsed is None:
                self.store.record_fetch_not_modified(
                    feed_id,
                    etag=result.etag or feed.get("etag") or "",
                    last_modified=result.last_modified or feed.get("last_modified") or "",
                    next_fetch_at=self._next_fetch_at(),
                )
                return {
                    "status": "not_modified",
                    "new_articles": 0,
                    "feed": self.store.get_feed(feed_id),
                }
            updates = self._feed_updates(parsed, feed)
            if updates:
                self.store.update_feed_meta(feed_id, **updates)
            merged = {**feed, **updates}
            entries = self._prepare_entries(parsed, merged, feed["url"])
            inserted = self.store.upsert_articles(feed_id, entries)
            self.store.record_fetch_success(
                feed_id,
                etag=result.etag,
                last_modified=result.last_modified,
                next_fetch_at=self._next_fetch_at(),
            )
            self.maybe_prune()
            return {
                "status": "ok",
                "new_articles": inserted,
                "feed": self.store.get_feed(feed_id),
            }
        finally:
            with self._active_lock:
                self._active_feeds.discard(feed_id)

    def _refresh_feed_safe(self, feed_id: int) -> None:
        try:
            result = self.refresh_feed(feed_id)
            if result.get("status") == "ok":
                LOGGER.info("refreshed feed id=%s new=%s", feed_id, result.get("new_articles"))
        except ServiceError as exc:
            LOGGER.warning("refresh feed id=%s failed: %s", feed_id, exc.message)
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("refresh feed id=%s crashed", feed_id)

    def request_refresh_all(self) -> bool:
        with self._refresh_all_lock:
            if self._refresh_all_running:
                return False
            self._refresh_all_running = True
        thread = threading.Thread(
            target=self._refresh_all_worker, name="yuii-refresh-all", daemon=True
        )
        thread.start()
        return True

    def _refresh_all_worker(self) -> None:
        try:
            feeds = [feed for feed in self.store.list_feeds() if not feed["disabled"]]
            executor = self._ensure_executor()
            futures = [
                executor.submit(self._refresh_feed_safe, int(feed["id"]))
                for feed in feeds
            ]
            for future in futures:
                try:
                    future.result(timeout=FETCH_TIMEOUT + 15)
                except Exception:
                    LOGGER.exception("refresh-all task failed")
            self.maybe_prune(force=True)
        finally:
            with self._refresh_all_lock:
                self._refresh_all_running = False

    def refresh_all_running(self) -> bool:
        with self._refresh_all_lock:
            return self._refresh_all_running

    def maybe_prune(self, force: bool = False) -> int:
        with self._prune_lock:
            now = time.monotonic()
            if not force and now - self._last_prune < 3600:
                return 0
            self._last_prune = now
        settings = self.store.get_settings()
        deleted = self.store.prune(
            settings["retention_days"], settings["max_per_feed"]
        )
        if deleted:
            LOGGER.info("pruned %s old articles", deleted)
        return deleted

    # ----------------------------------------------------------------------- reading

    def list_articles(
        self,
        *,
        feed_id: Any = None,
        category_id: Any = None,
        unread_only: bool = False,
        starred_only: bool = False,
        search: Any = "",
        limit: Any = 30,
        offset: Any = 0,
    ) -> dict[str, Any]:
        parsed_feed_id = self._optional_id(feed_id, "订阅")
        parsed_category_id = self._optional_id(category_id, "分类")
        text = clean_text(search, MAX_SEARCH_CHARS)
        if parsed_feed_id is not None:
            parsed_category_id = None
        return self.store.list_articles(
            feed_id=parsed_feed_id,
            category_id=parsed_category_id,
            unread_only=bool(unread_only),
            starred_only=bool(starred_only),
            search=text,
            limit=clamp_int(limit, 30, 1, 100),
            offset=clamp_int(offset, 0, 0, 1_000_000),
        )

    @staticmethod
    def _optional_id(value: Any, label: str) -> int | None:
        if value in (None, "", 0, "0"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ServiceError(f"{label}参数无效", "invalid_parameter") from exc

    def get_article(self, article_id: int) -> dict[str, Any]:
        article = self.store.get_article(article_id)
        if article is None:
            raise ServiceError("文章不存在", "article_not_found", 404)
        return article

    def mark_read(self, ids: Any, read: bool = True) -> dict[str, Any]:
        article_ids = self._id_list(ids)
        changed = self.store.mark_read(article_ids, read=read)
        stats = self.store.stats()
        return {"changed": changed, "unread": stats["unread"]}

    def toggle_star(self, article_id: int, starred: Any = None) -> dict[str, Any]:
        value = None if starred is None else bool(starred)
        article = self.store.toggle_star(article_id, value)
        if article is None:
            raise ServiceError("文章不存在", "article_not_found", 404)
        stats = self.store.stats()
        return {"article": article, "starred": stats["starred"]}

    def mark_all_read(
        self, *, feed_id: Any = None, category_id: Any = None
    ) -> dict[str, Any]:
        parsed_feed_id = self._optional_id(feed_id, "订阅")
        parsed_category_id = self._optional_id(category_id, "分类")
        if parsed_feed_id is not None:
            parsed_category_id = None
        changed = self.store.mark_all_read(
            feed_id=parsed_feed_id, category_id=parsed_category_id
        )
        return {"changed": changed, "unread": self.store.stats()["unread"]}

    @staticmethod
    def _id_list(value: Any) -> list[int]:
        if not isinstance(value, list):
            raise ServiceError("文章 ID 列表无效", "invalid_parameter")
        result: list[int] = []
        seen: set[int] = set()
        for item in value:
            try:
                number = int(item)
            except (TypeError, ValueError):
                continue
            if number <= 0 or number in seen:
                continue
            seen.add(number)
            result.append(number)
            if len(result) >= MAX_MARK_IDS:
                break
        return result

    # ------------------------------------------------------------------------ feeds

    def list_feeds(self) -> list[dict[str, Any]]:
        return self.store.list_feeds()

    def delete_feed(self, feed_id: int) -> None:
        if not self.store.delete_feed(feed_id):
            raise ServiceError("订阅不存在", "feed_not_found", 404)

    def update_feed(self, feed_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        feed = self.store.get_feed(feed_id)
        if feed is None:
            raise ServiceError("订阅不存在", "feed_not_found", 404)
        if "category_id" in fields:
            category_id = fields.get("category_id")
            if category_id in (None, "", 0, "0"):
                self.store.set_feed_category(feed_id, None)
            else:
                try:
                    parsed = int(category_id)
                except (TypeError, ValueError) as exc:
                    raise ServiceError("分类参数无效", "invalid_category") from exc
                if self.store.get_category(parsed) is None:
                    raise ServiceError("分类不存在", "category_not_found", 404)
                self.store.set_feed_category(feed_id, parsed)
        if "disabled" in fields:
            self.store.set_feed_disabled(feed_id, bool(fields.get("disabled")))
        return self.store.get_feed(feed_id) or {}

    # ------------------------------------------------------------------- categories

    def create_category(self, name: Any) -> dict[str, Any]:
        cleaned = clean_text(name, 60)
        if not cleaned:
            raise ServiceError("分类名称不能为空", "invalid_category")
        try:
            return self.store.create_category(cleaned)
        except StoreError as exc:
            raise ServiceError(exc.message, exc.code, 409) from exc

    def rename_category(self, category_id: int, name: Any) -> dict[str, Any]:
        cleaned = clean_text(name, 60)
        if not cleaned:
            raise ServiceError("分类名称不能为空", "invalid_category")
        try:
            category = self.store.rename_category(category_id, cleaned)
        except StoreError as exc:
            raise ServiceError(exc.message, exc.code, 409) from exc
        if category is None:
            raise ServiceError("分类不存在", "category_not_found", 404)
        return category

    def delete_category(self, category_id: int) -> None:
        if not self.store.delete_category(category_id):
            raise ServiceError("分类不存在", "category_not_found", 404)

    # ---------------------------------------------------------------------- OPML

    def import_opml(self, text: Any) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise ServiceError("OPML 内容为空", "invalid_opml")
        try:
            entries: list[OpmlEntry] = parse_opml(text)
        except FeedError as exc:
            raise ServiceError(exc.message, exc.code) from exc
        imported = 0
        skipped = 0
        categories: dict[str, int] = {}
        for entry in entries:
            if self.store.get_feed_by_url(entry.xml_url):
                skipped += 1
                continue
            category_id = None
            if entry.category:
                key = entry.category.lower()
                if key not in categories:
                    existing = self.store.find_category(entry.category)
                    if existing:
                        categories[key] = int(existing["id"])
                    else:
                        try:
                            categories[key] = int(
                                self.store.create_category(entry.category)["id"]
                            )
                        except StoreError:
                            categories[key] = 0
                category_id = categories.get(key) or None
            title = clean_text(entry.title, 500) or self._hostname(entry.xml_url)
            site_url = self._http_url(entry.html_url)
            try:
                self.store.create_feed(
                    title=title,
                    url=entry.xml_url,
                    site_url=site_url,
                    category_id=category_id,
                )
                imported += 1
            except StoreError:
                skipped += 1
        if imported:
            self.request_refresh_all()
        return {
            "imported": imported,
            "skipped": skipped,
            "total": len(entries),
        }

    def export_opml(self) -> str:
        categories = self.store.list_categories()
        feeds = self.store.list_feeds()
        grouped: dict[str, list[dict[str, Any]]] = {
            category["name"]: [] for category in categories
        }
        uncategorized: list[dict[str, Any]] = []
        for feed in feeds:
            item = {
                "title": feed["title"],
                "url": feed["url"],
                "site_url": feed["site_url"],
            }
            name = feed.get("category_name")
            if name and name in grouped:
                grouped[name].append(item)
            else:
                uncategorized.append(item)
        groups = [(name, items) for name, items in grouped.items() if items]
        if uncategorized:
            groups.append(("", uncategorized))
        return build_opml(groups)

    # -------------------------------------------------------------------- settings

    def get_settings(self) -> dict[str, Any]:
        return self.store.get_settings()

    def set_settings(self, patch: Any) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ServiceError("设置参数无效", "invalid_settings")
        settings = self.store.set_settings(patch)
        self.maybe_prune(force=True)
        return settings

    # ----------------------------------------------------------------------- misc

    def media(self, url: Any) -> FetchedImage:
        try:
            return fetch_image(str(url or ""))
        except FeedError as exc:
            status = 400 if exc.code in {"invalid_url", "unsupported_media"} else 502
            raise ServiceError(exc.message, exc.code, status) from exc

    def stats(self) -> dict[str, Any]:
        data = self.store.stats()
        data["version"] = __version__
        data["refresh_running"] = self.refresh_all_running()
        return data

    def health(self) -> dict[str, Any]:
        settings = self.store.get_settings()
        stats = self.store.stats()
        return {
            "app": APP_ID,
            "version": __version__,
            "status": "ok",
            "feeds": stats["feeds"],
            "articles": stats["articles"],
            "unread": stats["unread"],
            "refresh_interval": settings["refresh_interval"],
        }
