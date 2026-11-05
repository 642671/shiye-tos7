"""HTTP routing and static-file tests over a local TCP socket."""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from shiye import __version__
from shiye.feeds import FetchResult, ParsedEntry, ParsedFeed
from shiye.server import make_server, normalize_path
from shiye.service import AppService

FRONTEND_FILES = {
    "index.html": (
        "<!doctype html><html><body><div id=\"app\"></div>"
        "<span>v__APP_VERSION__</span></body></html>"
    ),
    "styles.css": "body { margin: 0; }",
    "app.js": "console.log('app');",
    "theme.js": "console.log('theme');",
    "tools.js": "console.log('tools');",
    "favicon.svg": "<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>",
}


class ServerHarness:
    def __init__(self, root: Path) -> None:
        self.frontend = root / "frontend"
        self.frontend.mkdir(parents=True, exist_ok=True)
        for name, content in FRONTEND_FILES.items():
            (self.frontend / name).write_text(content, encoding="utf-8")
        self.service = AppService(str(root / "data"), str(self.frontend))
        self.server = make_server(self.service, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.host = host
        self.port = int(port)

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def json(
        self,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> tuple[int, dict[str, str], object]:
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, response_headers, raw = self.request(
            method, path, body=body, headers=headers
        )
        parsed = json.loads(raw.decode("utf-8")) if raw else None
        return status, response_headers, parsed

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.service.close()


def parsed_feed() -> ParsedFeed:
    return ParsedFeed(
        title="Example",
        site_url="https://example.com/",
        description="Description",
        icon_url="",
        entries=[
            ParsedEntry(
                guid="one",
                url="https://example.com/one",
                title="One",
                author="",
                published_at="2025-01-01T00:00:00Z",
                summary_html="summary",
                content_html="<p>body</p>",
            )
        ],
    )


def fetch_result() -> FetchResult:
    return FetchResult(
        url="https://example.com/feed",
        final_url="https://example.com/feed",
        status=200,
        body=b"<rss/>",
        etag='W/"1"',
    )


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.harness = ServerHarness(self.root)

    def tearDown(self) -> None:
        self.harness.close()
        self.temp.cleanup()

    def subscribe(self) -> dict:
        with mock.patch(
            "shiye.service.fetch_feed",
            return_value=(fetch_result(), parsed_feed()),
        ):
            status, _, payload = self.harness.json(
                "POST", "/api/feeds", {"url": "https://example.com/feed"}
            )
        self.assertEqual(status, 201, payload)
        return payload

    def test_health_get_and_head(self) -> None:
        status, headers, payload = self.harness.json("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["app"], "shiye")
        self.assertEqual(payload["status"], "ok")
        self.assertIn("application/json", headers["Content-Type"])
        status, headers, raw = self.harness.request("HEAD", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_proxy_prefixes(self) -> None:
        for path in (
            "/v2/proxy/shiye/api/health",
            "/proxy/shiye/api/health",
            "/shiye/api/health",
        ):
            status, _, payload = self.harness.json("GET", path)
            self.assertEqual(status, 200, path)
            self.assertEqual(payload["app"], "shiye")
        status, _, raw = self.harness.request("GET", "/shiye/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="app"', raw)

    def test_normalize_path(self) -> None:
        self.assertEqual(normalize_path("/shiye/"), "/")
        self.assertEqual(normalize_path("/shiye/api/health/"), "/api/health")
        self.assertEqual(normalize_path("/api/health?x=1"), "/api/health")
        self.assertEqual(normalize_path("api/health"), "/api/health")

    def test_static_files_and_headers(self) -> None:
        status, headers, raw = self.harness.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn(f"v{__version__}".encode(), raw)
        self.assertNotIn(b"__APP_VERSION__", raw)
        status, headers, raw = self.harness.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "public, max-age=3600")
        status, _, raw = self.harness.request("HEAD", "/styles.css")
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"")

    def test_unknown_static_and_api(self) -> None:
        status, _, payload = self.harness.json("GET", "/missing.txt")
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "not_found")
        status, _, payload = self.harness.json("GET", "/api/nope")
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "not_found")

    def test_method_not_allowed(self) -> None:
        status, headers, payload = self.harness.json("POST", "/")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")
        status, headers, payload = self.harness.json("DELETE", "/api/feeds")
        self.assertEqual(status, 405)
        self.assertEqual(payload["code"], "method_not_allowed")

    def test_options(self) -> None:
        status, headers, raw = self.harness.request("OPTIONS", "/api/feeds")
        self.assertEqual(status, 204)
        self.assertEqual(raw, b"")
        self.assertIn("POST", headers["Allow"])

    def test_subscribe_and_list_feeds(self) -> None:
        created = self.subscribe()
        self.assertEqual(created["feed"]["title"], "Example")
        self.assertEqual(created["new_articles"], 1)
        status, _, payload = self.harness.json("GET", "/api/feeds")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["items"]), 1)
        feed_id = payload["items"][0]["id"]
        status, _, payload = self.harness.json("GET", f"/api/feeds/{feed_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["feed"]["id"], feed_id)
        status, _, payload = self.harness.json("GET", "/api/feeds/99999")
        self.assertEqual(status, 404)

    def test_article_endpoints(self) -> None:
        self.subscribe()
        status, _, payload = self.harness.json("GET", "/api/articles")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)
        article = payload["items"][0]
        article_id = article["id"]
        self.assertTrue(article["is_read"] is False)
        status, _, payload = self.harness.json(
            "GET", f"/api/articles/{article_id}"
        )
        self.assertEqual(status, 200)
        self.assertIn("body", payload["article"]["content"])
        status, _, payload = self.harness.json(
            "POST", f"/api/articles/{article_id}/read", {"read": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["changed"], 1)
        self.assertEqual(payload["unread"], 0)
        status, _, payload = self.harness.json(
            "POST", f"/api/articles/{article_id}/star", {"starred": True}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["article"]["is_starred"])
        status, _, payload = self.harness.json(
            "POST", "/api/articles/mark-read", {"ids": [article_id], "read": False}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["unread"], 1)
        status, _, payload = self.harness.json(
            "POST", "/api/articles/mark-all-read", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["changed"], 1)

    def test_feed_patch_refresh_delete(self) -> None:
        feed_id = self.subscribe()["feed"]["id"]
        status, _, payload = self.harness.json(
            "PATCH", f"/api/feeds/{feed_id}", {"disabled": True}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["feed"]["disabled"])
        with mock.patch(
            "shiye.service.fetch_feed",
            return_value=(fetch_result(), parsed_feed()),
        ):
            status, _, payload = self.harness.json(
                "PATCH", f"/api/feeds/{feed_id}", {"disabled": False}
            )
            self.assertEqual(status, 200)
            status, _, payload = self.harness.json(
                "POST", f"/api/feeds/{feed_id}/refresh"
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        status, _, raw = self.harness.request("DELETE", f"/api/feeds/{feed_id}")
        self.assertEqual(status, 204)
        self.assertEqual(raw, b"")

    def test_categories_crud(self) -> None:
        status, _, payload = self.harness.json(
            "POST", "/api/categories", {"name": "Tech"}
        )
        self.assertEqual(status, 201)
        category_id = payload["category"]["id"]
        status, _, payload = self.harness.json("GET", "/api/categories")
        self.assertEqual(len(payload["items"]), 1)
        status, _, payload = self.harness.json(
            "PATCH", f"/api/categories/{category_id}", {"name": "News"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["category"]["name"], "News")
        status, _, payload = self.harness.json(
            "POST", "/api/categories", {"name": "news"}
        )
        self.assertEqual(status, 409)
        status, _, raw = self.harness.request(
            "DELETE", f"/api/categories/{category_id}"
        )
        self.assertEqual(status, 204)

    def test_settings(self) -> None:
        status, _, payload = self.harness.json("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(payload["refresh_interval"], 30)
        status, _, payload = self.harness.json(
            "PUT", "/api/settings", {"refresh_interval": 1, "auto_refresh": False}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["refresh_interval"], 5)
        self.assertFalse(payload["auto_refresh"])

    def test_stats_and_refresh(self) -> None:
        status, _, payload = self.harness.json("GET", "/api/stats")
        self.assertEqual(status, 200)
        self.assertEqual(payload["feeds"], 0)
        status, _, payload = self.harness.json("POST", "/api/refresh")
        self.assertEqual(status, 202)
        self.assertIn("started", payload)

    def test_opml_export_and_import(self) -> None:
        self.subscribe()
        status, headers, raw = self.harness.request("GET", "/api/opml")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn(b"<opml", raw)
        self.assertIn(b"https://example.com/feed", raw)
        opml = (
            '<?xml version="1.0"?><opml version="2.0"><body>'
            '<outline text="New" type="rss" xmlUrl="https://new.example/feed"/>'
            "</body></opml>"
        )
        with mock.patch.object(
            self.harness.service, "request_refresh_all", return_value=False
        ):
            status, _, payload = self.harness.json("POST", "/api/opml", {"opml": opml})
        self.assertEqual(status, 200)
        self.assertEqual(payload["imported"], 1)
        status, _, payload = self.harness.json("POST", "/api/opml", {})
        self.assertEqual(status, 400)

    def test_media_and_bad_requests(self) -> None:
        status, _, payload = self.harness.json("GET", "/api/media")
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "invalid_url")
        status, _, payload = self.harness.json("GET", "/api/articles?feed_id=abc")
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "invalid_parameter")
        status, _, payload = self.harness.json(
            "POST", "/api/settings", {"refresh_interval": 60}
        )
        self.assertEqual(status, 405)
        status, _, payload = self.harness.json(
            "PUT", "/api/settings", ["not", "an", "object"]
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "invalid_json")
        body = b"{not json"
        status, _, raw = self.harness.request(
            "PUT",
            "/api/settings",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["code"], "invalid_json")

    def test_missing_article_is_404(self) -> None:
        status, _, payload = self.harness.json("GET", "/api/articles/99999")
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "article_not_found")


if __name__ == "__main__":
    unittest.main()
