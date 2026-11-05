"""HTTP server for Shiye over a Unix socket or local TCP port."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import socket
import socketserver
import stat
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from . import APP_ID, __version__
from .service import AppService, ServiceError

LOGGER = logging.getLogger("shiye.server")

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_OPML_BYTES = 4 * 1024 * 1024

STATIC_FILES = {
    "index.html": ("text/html; charset=utf-8", True),
    "styles.css": ("text/css; charset=utf-8", False),
    "app.js": ("application/javascript; charset=utf-8", False),
    "theme.js": ("application/javascript; charset=utf-8", False),
    "tools.js": ("application/javascript; charset=utf-8", False),
    "favicon.svg": ("image/svg+xml", False),
}

CSP = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)

_PATH_PREFIXES = ("/v2/proxy/" + APP_ID, "/proxy/" + APP_ID, "/" + APP_ID)
_FEED_PATH = re.compile(r"^/api/feeds/(\d+)$")
_FEED_REFRESH = re.compile(r"^/api/feeds/(\d+)/refresh$")
_ARTICLE_PATH = re.compile(r"^/api/articles/(\d+)$")
_ARTICLE_READ = re.compile(r"^/api/articles/(\d+)/read$")
_ARTICLE_STAR = re.compile(r"^/api/articles/(\d+)/star$")
_ARTICLE_EXTRACT = re.compile(r"^/api/articles/(\d+)/extract$")
_CATEGORY_PATH = re.compile(r"^/api/categories/(\d+)$")


def normalize_path(path: str) -> str:
    value = (path or "/").split("?", 1)[0].split("#", 1)[0]
    for prefix in _PATH_PREFIXES:
        if value == prefix or value == prefix + "/":
            value = "/"
            break
        if value.startswith(prefix + "/"):
            value = value[len(prefix) :]
            break
    if not value.startswith("/"):
        value = "/" + value
    while len(value) > 1 and value.endswith("/"):
        value = value[:-1]
    return value or "/"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _query_value(query: dict[str, list[str]], name: str, default: str = "") -> str:
    values = query.get(name)
    return values[0] if values else default


class AppHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"Shiye/{__version__}"
    sys_version = ""

    @property
    def service(self) -> AppService:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.debug("%s %s", self.address_string(), fmt % args)

    def address_string(self) -> str:
        return self.client_address[0] if self.client_address else "-"

    # ------------------------------------------------------------------ dispatching

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch(self, method: str) -> None:
        self._responded = False
        try:
            self._route(method)
        except ServiceError as exc:
            if not self._responded:
                self._send_json(
                    exc.status, {"error": exc.message, "code": exc.code}
                )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            LOGGER.exception("unhandled request error: %s %s", method, self.path)
            if not self._responded:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "服务器内部错误", "code": "internal_error"},
                )

    def _route(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = normalize_path(parsed.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        if method in {"GET", "HEAD"} and path in {"/api/health", "/health"}:
            self._send_json(HTTPStatus.OK, self.service.health())
            return

        if path.startswith("/api/"):
            self._route_api(method, path, query)
            return

        if method in {"GET", "HEAD"}:
            self._serve_static(path)
            return
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": "请求方法不被允许", "code": "method_not_allowed"},
            headers={"Allow": "GET, HEAD"},
        )

    def _route_api(
        self, method: str, path: str, query: dict[str, list[str]]
    ) -> None:
        if path == "/api/feeds":
            if method == "GET":
                self._send_json(
                    HTTPStatus.OK, {"items": self.service.list_feeds()}
                )
                return
            if method == "POST":
                body = self._read_json()
                result = self.service.subscribe(
                    body.get("url"),
                    category_id=body.get("category_id"),
                    category_name=body.get("category_name"),
                    title=body.get("title", ""),
                )
                self._send_json(HTTPStatus.CREATED, result)
                return
            self._method_not_allowed("GET, POST")
            return

        match = _FEED_PATH.match(path)
        if match:
            feed_id = int(match.group(1))
            if method == "GET":
                feeds = [f for f in self.service.list_feeds() if f["id"] == feed_id]
                if not feeds:
                    raise ServiceError("订阅不存在", "feed_not_found", 404)
                self._send_json(HTTPStatus.OK, {"feed": feeds[0]})
                return
            if method == "PATCH":
                body = self._read_json()
                feed = self.service.update_feed(feed_id, body)
                self._send_json(HTTPStatus.OK, {"feed": feed})
                return
            if method == "DELETE":
                self.service.delete_feed(feed_id)
                self._send_empty(HTTPStatus.NO_CONTENT)
                return
            self._method_not_allowed("GET, PATCH, DELETE")
            return

        match = _FEED_REFRESH.match(path)
        if match and method == "POST":
            result = self.service.refresh_feed(int(match.group(1)))
            self._send_json(HTTPStatus.OK, result)
            return

        if path == "/api/articles":
            if method != "GET":
                self._method_not_allowed("GET")
                return
            result = self.service.list_articles(
                feed_id=_query_value(query, "feed_id"),
                category_id=_query_value(query, "category_id"),
                unread_only=_as_bool(_query_value(query, "unread")),
                starred_only=_as_bool(_query_value(query, "starred")),
                saved_only=_as_bool(_query_value(query, "saved")),
                search=_query_value(query, "q"),
                limit=_query_value(query, "limit", "30"),
                offset=_query_value(query, "offset", "0"),
            )
            self._send_json(HTTPStatus.OK, result)
            return

        if path == "/api/saved":
            if method != "POST":
                self._method_not_allowed("POST")
                return
            body = self._read_json()
            result = self.service.save_article(
                body.get("url"),
                fallback=_as_bool(body.get("fallback")),
                title=body.get("title", ""),
            )
            self._send_json(HTTPStatus.CREATED, result)
            return

        if path == "/api/articles/mark-read":
            if method != "POST":
                self._method_not_allowed("POST")
                return
            body = self._read_json()
            result = self.service.mark_read(
                body.get("ids"), read=_as_bool(body.get("read", True))
            )
            self._send_json(HTTPStatus.OK, result)
            return

        if path == "/api/articles/mark-all-read":
            if method != "POST":
                self._method_not_allowed("POST")
                return
            body = self._read_json()
            result = self.service.mark_all_read(
                feed_id=body.get("feed_id"),
                category_id=body.get("category_id"),
                saved_only=_as_bool(body.get("saved")),
            )
            self._send_json(HTTPStatus.OK, result)
            return

        match = _ARTICLE_PATH.match(path)
        if match:
            if method == "GET":
                article = self.service.get_article(int(match.group(1)))
                self._send_json(HTTPStatus.OK, {"article": article})
                return
            if method == "DELETE":
                result = self.service.delete_article(int(match.group(1)))
                self._send_json(HTTPStatus.OK, result)
                return
            self._method_not_allowed("GET, DELETE")
            return

        match = _ARTICLE_EXTRACT.match(path)
        if match:
            if method != "POST":
                self._method_not_allowed("POST")
                return
            article = self.service.reload_saved_content(int(match.group(1)))
            self._send_json(HTTPStatus.OK, {"article": article})
            return

        match = _ARTICLE_READ.match(path)
        if match:
            if method != "POST":
                self._method_not_allowed("POST")
                return
            body = self._read_json()
            result = self.service.mark_read(
                [int(match.group(1))], read=_as_bool(body.get("read", True))
            )
            self._send_json(HTTPStatus.OK, result)
            return

        match = _ARTICLE_STAR.match(path)
        if match:
            if method != "POST":
                self._method_not_allowed("POST")
                return
            body = self._read_json()
            starred = body.get("starred")
            result = self.service.toggle_star(
                int(match.group(1)),
                None if starred is None else _as_bool(starred),
            )
            self._send_json(HTTPStatus.OK, result)
            return

        if path == "/api/categories":
            if method == "GET":
                self._send_json(
                    HTTPStatus.OK, {"items": self.service.store.list_categories()}
                )
                return
            if method == "POST":
                body = self._read_json()
                category = self.service.create_category(body.get("name"))
                self._send_json(HTTPStatus.CREATED, {"category": category})
                return
            self._method_not_allowed("GET, POST")
            return

        match = _CATEGORY_PATH.match(path)
        if match:
            category_id = int(match.group(1))
            if method == "PATCH":
                body = self._read_json()
                category = self.service.rename_category(
                    category_id, body.get("name")
                )
                self._send_json(HTTPStatus.OK, {"category": category})
                return
            if method == "DELETE":
                self.service.delete_category(category_id)
                self._send_empty(HTTPStatus.NO_CONTENT)
                return
            self._method_not_allowed("PATCH, DELETE")
            return

        if path == "/api/settings":
            if method == "GET":
                self._send_json(HTTPStatus.OK, self.service.get_settings())
                return
            if method == "PUT":
                settings = self.service.set_settings(self._read_json())
                self._send_json(HTTPStatus.OK, settings)
                return
            self._method_not_allowed("GET, PUT")
            return

        if path == "/api/stats":
            if method != "GET":
                self._method_not_allowed("GET")
                return
            self._send_json(HTTPStatus.OK, self.service.stats())
            return

        if path == "/api/refresh":
            if method != "POST":
                self._method_not_allowed("POST")
                return
            started = self.service.request_refresh_all()
            self._send_json(HTTPStatus.ACCEPTED, {"started": started})
            return

        if path == "/api/opml":
            if method == "GET":
                body = self.service.export_opml().encode("utf-8")
                self._send_bytes(
                    HTTPStatus.OK,
                    body,
                    "text/x-opml; charset=utf-8",
                    extra_headers={
                        "Content-Disposition": (
                            'attachment; filename="shiye-subscriptions.opml"'
                        )
                    },
                )
                return
            if method == "POST":
                content_type = (self.headers.get("Content-Type") or "").lower()
                if "application/json" in content_type:
                    body = self._read_json(MAX_OPML_BYTES)
                    text = body.get("opml") or body.get("content") or ""
                else:
                    text = self._read_text(MAX_OPML_BYTES)
                result = self.service.import_opml(text)
                self._send_json(HTTPStatus.OK, result)
                return
            self._method_not_allowed("GET, POST")
            return

        if path == "/api/media":
            if method != "GET":
                self._method_not_allowed("GET")
                return
            image = self.service.media(_query_value(query, "url"))
            self._send_bytes(
                HTTPStatus.OK,
                image.body,
                image.content_type,
                extra_headers={"Cache-Control": "public, max-age=86400"},
            )
            return

        raise ServiceError("接口不存在", "not_found", 404)

    def _method_not_allowed(self, allow: str) -> None:
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": "请求方法不被允许", "code": "method_not_allowed"},
            headers={"Allow": allow},
        )

    # ----------------------------------------------------------------------- static

    def _serve_static(self, path: str) -> None:
        name = "index.html" if path == "/" else path.lstrip("/")
        info = STATIC_FILES.get(name)
        if info is None:
            self._send_json(
                HTTPStatus.NOT_FOUND, {"error": "页面不存在", "code": "not_found"}
            )
            return
        target = os.path.join(self.service.frontend_dir, name)
        try:
            with open(target, "rb") as handle:
                body = handle.read()
        except OSError:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "前端资源未安装", "code": "frontend_missing"},
            )
            return
        content_type, is_html = info
        if is_html:
            # Builds replace the placeholder, but source runs should also
            # show the real version instead of "__APP_VERSION__".
            body = body.replace(b"__APP_VERSION__", __version__.encode("utf-8"))
        headers: dict[str, str] = {
            "Cache-Control": "no-cache" if is_html else "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        }
        if is_html:
            headers["Content-Security-Policy"] = CSP
        self._send_bytes(HTTPStatus.OK, body, content_type, extra_headers=headers)

    # ------------------------------------------------------------------- request IO

    def _read_body(self, limit: int) -> bytes:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ServiceError("缺少 Content-Length", "length_required", 411)
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ServiceError("Content-Length 无效", "invalid_length") from exc
        if length < 0:
            raise ServiceError("Content-Length 无效", "invalid_length")
        if length > limit:
            raise ServiceError("请求体过大", "payload_too_large", 413)
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        if len(body) != length:
            raise ServiceError("请求体不完整", "incomplete_body")
        return body

    def _read_json(self, limit: int = MAX_JSON_BYTES) -> dict[str, Any]:
        body = self._read_body(limit)
        if not body.strip():
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ServiceError("JSON 请求体无效", "invalid_json") from exc
        if not isinstance(payload, dict):
            raise ServiceError("JSON 请求体必须是对象", "invalid_json")
        return payload

    def _read_text(self, limit: int) -> str:
        body = self._read_body(limit)
        try:
            return body.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ServiceError("文本内容必须是 UTF-8", "invalid_encoding") from exc

    # ----------------------------------------------------------------- response IO

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self._send_bytes(
            status,
            body,
            "application/json; charset=utf-8",
            extra_headers=headers,
        )

    def _send_empty(self, status: int) -> None:
        self._responded = True
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._responded = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        if content_type.startswith("application/json"):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


_UNIX_FAMILY = getattr(socket, "AF_UNIX", None)


class _UnixHTTPServer(_ThreadingHTTPServer):
    if _UNIX_FAMILY is not None:
        address_family = _UNIX_FAMILY
    allow_reuse_address = False

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = 0


def make_server(
    service: AppService,
    *,
    socket_path: str | None = None,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> socketserver.BaseServer:
    if port is not None:
        server = _ThreadingHTTPServer((host, int(port)), AppHandler)
    else:
        if not socket_path:
            raise ValueError("socket_path or port is required")
        if _UNIX_FAMILY is None:
            raise RuntimeError("Unix domain sockets are not supported on this platform")
        parent = os.path.dirname(os.path.abspath(socket_path))
        if not os.path.isdir(parent):
            raise RuntimeError(f"socket directory does not exist: {parent}")
        if os.path.lexists(socket_path):
            mode = os.lstat(socket_path).st_mode
            if not stat.S_ISSOCK(mode):
                raise RuntimeError(f"refusing to remove non-socket: {socket_path}")
            os.unlink(socket_path)
        server = _UnixHTTPServer(socket_path, AppHandler)
        try:
            os.chmod(socket_path, 0o666)
        except OSError as exc:
            LOGGER.warning("could not chmod socket %s: %s", socket_path, exc)
    server.service = service  # type: ignore[attr-defined]
    return server


def _configure_logging() -> None:
    level_name = os.environ.get("SHIYE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=None,
    )


def main() -> int:
    _configure_logging()
    here = os.path.dirname(os.path.abspath(__file__))
    default_root = os.path.dirname(os.path.dirname(here))
    data_dir = os.environ.get(
        "SHIYE_DATA_DIR", os.path.join(default_root, "data")
    )
    frontend_dir = os.environ.get(
        "SHIYE_FRONTEND_DIR", os.path.join(default_root, "frontend")
    )
    socket_path = os.environ.get(
        "SHIYE_SOCKET", f"/var/api/{APP_ID}.sock"
    )
    host = os.environ.get("SHIYE_HOST", "127.0.0.1")
    port_raw = os.environ.get("SHIYE_PORT", "").strip()

    service = AppService(data_dir, frontend_dir=frontend_dir)
    server: socketserver.BaseServer | None = None
    try:
        if port_raw:
            server = make_server(service, host=host, port=int(port_raw))
            LOGGER.info(
                "%s %s listening on http://%s:%s", APP_ID, __version__, host, port_raw
            )
        else:
            server = make_server(service, socket_path=socket_path)
            LOGGER.info(
                "%s %s listening on %s", APP_ID, __version__, socket_path
            )
        service.start_background()
        stop = threading.Event()

        def _handle_signal(signum: int, _frame: Any) -> None:
            if stop.is_set():
                return
            stop.set()
            LOGGER.info("received signal %s, shutting down", signum)
            threading.Thread(target=server.shutdown, daemon=True).start()

        for name in ("SIGTERM", "SIGINT"):
            signum = getattr(signal, name, None)
            if signum is not None:
                try:
                    signal.signal(signum, _handle_signal)
                except (ValueError, OSError):
                    pass
        server.serve_forever(poll_interval=0.5)
        return 0
    finally:
        service.close()
        if server is not None:
            try:
                server.server_close()
            except Exception:
                pass
        if not port_raw and os.path.lexists(socket_path):
            try:
                os.unlink(socket_path)
            except OSError:
                pass
        LOGGER.info("%s stopped", APP_ID)


if __name__ == "__main__":
    raise SystemExit(main())
