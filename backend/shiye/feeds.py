"""Feed fetching, discovery, parsing, OPML and image proxying for Shiye."""

from __future__ import annotations

import codecs
import gzip
import hashlib
import html
import re
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Iterable
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr

from . import __version__
from .sanitize import html_to_text
from .util import clean_text, format_utc, utc_now

USER_AGENT = (
    f"Shiye/{__version__} (+https://github.com/642671/shiye-tos7)"
)
MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_PAGE_BYTES = 10 * 1024 * 1024
MAX_ENTRIES = 200
MAX_TITLE_CHARS = 500
MAX_PERSON_CHARS = 200
MAX_FEED_URLS = 2000
MAX_OPML_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
FETCH_TIMEOUT = 20
MEDIA_TIMEOUT = 15
MAX_REDIRECTS = 5
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/avif",
}
_FEED_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/rdf+xml",
    "application/xml",
    "text/xml",
}
_MEDIA_SEMAPHORE = threading.BoundedSemaphore(6)


class FeedError(Exception):
    """A feed could not be fetched or understood."""

    def __init__(self, message: str, code: str = "feed_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    body: bytes = b""
    content_type: str = ""
    charset: str = ""
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False


@dataclass
class ParsedEntry:
    guid: str
    url: str
    title: str
    author: str
    published_at: str | None
    summary_html: str
    content_html: str


@dataclass
class ParsedFeed:
    title: str
    site_url: str
    description: str
    icon_url: str
    entries: list[ParsedEntry] = field(default_factory=list)
    kind: str = "rss"


@dataclass
class OpmlEntry:
    title: str
    xml_url: str
    category: str = ""
    html_url: str = ""


@dataclass
class FetchedImage:
    body: bytes
    content_type: str


def normalize_feed_url(raw: Any) -> str:
    return _normalize_url(raw, "订阅地址", "请输入订阅地址")


def normalize_page_url(raw: Any) -> str:
    return _normalize_url(raw, "文章链接", "请输入文章链接")


def _normalize_url(raw: Any, label: str, empty_message: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise FeedError(empty_message, "invalid_url")
    if len(value) > 2048:
        raise FeedError(f"{label}过长", "invalid_url")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", value) and not re.match(
        r"^https?://", value, re.IGNORECASE
    ):
        raise FeedError(f"只支持 http/https {label}", "invalid_url")
    if "://" not in value:
        value = "https://" + value
    parts = urllib.parse.urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.hostname:
        raise FeedError(f"只支持 http/https {label}", "invalid_url")
    try:
        parts.port
    except ValueError as exc:
        raise FeedError(f"{label}的端口无效", "invalid_url") from exc
    if parts.username or parts.password:
        raise FeedError(f"{label}不能包含用户名或密码", "invalid_url")
    netloc = parts.netloc
    path = parts.path or "/"
    return urllib.parse.urlunsplit((scheme, netloc, path, parts.query, ""))


def _validate_http_url(raw: Any, label: str = "地址") -> str:
    value = str(raw or "").strip()
    if not value:
        raise FeedError(f"{label}为空", "invalid_url")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise FeedError(f"{label}必须是 http/https 地址", "invalid_url")
    if parts.username or parts.password:
        raise FeedError(f"{label}不能包含用户名或密码", "invalid_url")
    return value


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    max_repeats = 2
    max_redirections = MAX_REDIRECTS

    def __init__(self, label: str = "订阅源") -> None:
        super().__init__()
        self.label = label

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme.lower() not in {"http", "https"}:
            raise FeedError(
                f"{self.label}重定向到了不支持的协议", "invalid_redirect"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_limited(response: Any, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(65536, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        chunks.append(chunk)
        if total > limit:
            break
    return b"".join(chunks)


def _decompress(body: bytes, encoding: str, label: str = "订阅源") -> bytes:
    value = (encoding or "").strip().lower()
    if value in {"gzip", "x-gzip"}:
        try:
            return gzip.decompress(body)
        except OSError as exc:
            raise FeedError(f"{label}的 gzip 内容损坏：{exc}", "bad_encoding") from exc
    if value == "deflate":
        try:
            return zlib.decompress(body)
        except zlib.error:
            try:
                return zlib.decompress(body, -zlib.MAX_WBITS)
            except zlib.error as exc:
                raise FeedError(
                    f"{label}的 deflate 内容损坏：{exc}", "bad_encoding"
                ) from exc
    return body


def _limit_label(max_bytes: int) -> str:
    mib = max_bytes / (1024 * 1024)
    if mib >= 1:
        rounded = int(mib) if abs(mib - int(mib)) < 0.05 else round(mib, 1)
        return f"{rounded} MiB"
    return f"{max(1, round(max_bytes / 1024))} KiB"


def fetch(
    url: str,
    *,
    etag: str = "",
    last_modified: str = "",
    timeout: float = FETCH_TIMEOUT,
    max_bytes: int = MAX_FEED_BYTES,
    accept: str = (
        "application/rss+xml, application/atom+xml, application/xml, "
        "text/xml, text/html;q=0.8, */*;q=0.5"
    ),
    label: str = "订阅源",
    invalid_label: str = "订阅地址",
) -> FetchResult:
    target = _validate_http_url(url, invalid_label)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Encoding": "gzip, deflate",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    request = urllib.request.Request(target, headers=headers, method="GET")
    opener = urllib.request.build_opener(_RedirectHandler(label))
    limit_text = _limit_label(max_bytes)

    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return FetchResult(
                url=target,
                final_url=exc.geturl() or target,
                status=304,
                etag=etag,
                last_modified=last_modified,
                not_modified=True,
            )
        detail = f"HTTP {exc.code}"
        if exc.reason:
            detail += f" {exc.reason}"
        raise FeedError(f"{label}返回 {detail}", "http_error") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            message = f"{label}连接超时"
        elif isinstance(reason, ssl.SSLError):
            message = f"{label} TLS 证书校验失败：{reason}"
        elif isinstance(reason, socket.gaierror):
            message = f"无法解析{label}域名，请检查 NAS 的网络与 DNS"
        else:
            message = f"无法连接{label}：{reason}"
        raise FeedError(message, "network_error") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise FeedError(f"{label}连接超时", "network_error") from exc

    with response:
        status = int(getattr(response, "status", 200) or 200)
        if status >= 400:
            raise FeedError(f"{label}返回 HTTP {status}", "http_error")
        content_encoding = response.headers.get("Content-Encoding", "")
        raw = _read_limited(response, max_bytes)
        if len(raw) > max_bytes:
            raise FeedError(f"{label}超过 {limit_text} 大小限制", "too_large")
        body = _decompress(raw, content_encoding, label=label)
        if len(body) > max_bytes:
            raise FeedError(
                f"{label}解压后超过 {limit_text} 大小限制", "too_large"
            )
        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0]
        charset = response.headers.get_content_charset() or ""
        final_url = response.geturl() or target
        _validate_http_url(final_url, f"{label}最终地址")
        return FetchResult(
            url=target,
            final_url=final_url,
            status=status,
            body=body,
            content_type=content_type.strip().lower(),
            charset=charset,
            etag=response.headers.get("ETag", "") or "",
            last_modified=response.headers.get("Last-Modified", "") or "",
        )


_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
_XML_DECL = re.compile(
    rb"<\?xml[^>]*encoding\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE
)


def _encoding_candidates(declared: str) -> list[str]:
    value = (declared or "").strip().strip("\"'").lower()
    if not value:
        return []
    aliases = {
        "gb2312": "gb18030",
        "gbk": "gb18030",
        "gb18030": "gb18030",
        "big5": "big5hkscs",
        "iso-8859-1": "cp1252",
        "latin1": "cp1252",
        "latin-1": "cp1252",
    }
    return [aliases.get(value, value), value]


def decode_body(body: bytes, header_charset: str = "") -> str:
    for bom, encoding in _BOMS:
        if body.startswith(bom):
            try:
                return body[len(bom) :].decode(encoding, "strict")
            except (LookupError, UnicodeDecodeError):
                break
    declared = ""
    match = _XML_DECL.search(body[:1024])
    if match:
        declared = match.group(1).decode("ascii", "ignore")
    candidates = _encoding_candidates(header_charset) + _encoding_candidates(declared)
    for encoding in candidates:
        try:
            return body.decode(encoding, "strict")
        except (LookupError, UnicodeDecodeError):
            continue
    for encoding in ("utf-8", "gb18030", "big5hkscs", "utf-16"):
        try:
            return body.decode(encoding, "strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _find_child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    target = name.lower()
    for child in parent:
        if _local(child.tag) == target:
            return child
    return None


def _find_children(parent: ET.Element | None, name: str) -> list[ET.Element]:
    if parent is None:
        return []
    target = name.lower()
    return [child for child in parent if _local(child.tag) == target]


def _text_of(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def _serialize_element(element: ET.Element) -> str:
    """Serialize an Atom XHTML element without namespace prefixes."""

    tag = _local(element.tag)
    attributes = []
    for key, value in element.attrib.items():
        name = _local(key)
        if name.startswith("xmlns") or name.startswith("on"):
            continue
        attributes.append(f" {name}={quoteattr(str(value))}")
    inner = html.escape(element.text or "")
    for child in element:
        inner += _serialize_element(child)
        inner += html.escape(child.tail or "")
    return f"<{tag}{''.join(attributes)}>{inner}</{tag}>"


def _inner_html(element: ET.Element | None) -> str:
    if element is None:
        return ""
    children = list(element)
    if not children:
        return element.text or ""
    return "".join(
        _serialize_element(child) + (child.tail or "") for child in children
    )


def _safe_xml(text: str) -> None:
    if re.search(r"<!ENTITY", text[:100000], re.IGNORECASE):
        raise FeedError("订阅源包含不支持的 XML 实体声明", "unsafe_feed")


def parse_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        candidate = text.replace("Z", "+00:00")
        candidate = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", candidate)
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(candidate.replace(" ", "T", 1))
            except ValueError:
                parsed = None
    if parsed is None:
        for pattern in (
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if parsed.year < 1970 or parsed.year > 2100:
        return None
    return format_utc(parsed)


def _entry_guid(raw: str, url: str, title: str, published: str | None) -> str:
    value = (raw or "").strip()
    if value:
        return value[:1000]
    if url:
        return url
    digest = hashlib.sha1(
        f"{title}\n{published or ''}".encode("utf-8", "replace")
    ).hexdigest()
    return "hash:" + digest


def _rss_link(item: ET.Element, base_url: str) -> str:
    link = _find_child(item, "link")
    value = _text_of(link)
    if not value:
        guid = _find_child(item, "guid")
        if guid is not None and (guid.get("isPermaLink") or "").lower() != "false":
            value = _text_of(guid)
    if not value:
        for child in item:
            if _local(child.tag) == "link" and child.get("href"):
                value = child.get("href") or ""
                break
    if not value:
        return ""
    return urllib.parse.urljoin(base_url, value.strip())


def _atom_link(parent: ET.Element, base_url: str) -> str:
    fallback = ""
    for child in _find_children(parent, "link"):
        href = (child.get("href") or "").strip()
        if not href:
            continue
        resolved = urllib.parse.urljoin(base_url, href)
        rel = (child.get("rel") or "alternate").lower()
        if rel == "alternate":
            return resolved
        if not fallback:
            fallback = resolved
    return fallback


def _rss_content(item: ET.Element) -> str:
    encoded = _find_child(item, "encoded")
    if encoded is not None:
        value = _inner_html(encoded) if len(encoded) else (encoded.text or "")
        if value.strip():
            return value
    description = _find_child(item, "description")
    if description is not None:
        return _inner_html(description) if len(description) else (description.text or "")
    return ""


def _atom_content(entry: ET.Element) -> tuple[str, str]:
    content = _find_child(entry, "content")
    summary = _find_child(entry, "summary")
    content_html = ""
    if content is not None:
        content_type = (content.get("type") or "html").strip().lower()
        if content_type == "xhtml":
            content_html = _inner_html(content)
        elif content_type == "text":
            content_html = "<p>" + html.escape(content.text or "").replace("\n", "<br>") + "</p>"
        else:
            content_html = content.text or ""
    summary_html = ""
    if summary is not None:
        summary_type = (summary.get("type") or "html").strip().lower()
        if summary_type == "xhtml":
            summary_html = _inner_html(summary)
        elif summary_type == "text":
            summary_html = "<p>" + html.escape(summary.text or "") + "</p>"
        else:
            summary_html = summary.text or ""
    return content_html, summary_html


def _feed_icon(channel: ET.Element, base_url: str) -> str:
    icon = _find_child(channel, "icon")
    if icon is None:
        icon = _find_child(channel, "logo")
    value = _text_of(icon)
    if not value:
        image = _find_child(channel, "image")
        if image is not None:
            value = _text_of(_find_child(image, "url"))
    if not value:
        return ""
    resolved = urllib.parse.urljoin(base_url, value.strip())
    if urllib.parse.urlsplit(resolved).scheme.lower() not in {"http", "https"}:
        return ""
    return resolved


_META_CHARSET = re.compile(
    rb"<meta[^>]+charset\s*=\s*[\"']?\s*([A-Za-z0-9._-]+)", re.IGNORECASE
)


def decode_html_body(body: bytes, header_charset: str = "") -> str:
    """Decode an HTML page, checking meta charset tags as fallback."""

    declared = ""
    match = _META_CHARSET.search(body[:8192])
    if match:
        declared = match.group(1).decode("ascii", "ignore")
    candidates = (
        _encoding_candidates(header_charset)
        + _encoding_candidates(declared)
        + _encoding_candidates(_xml_declared(body))
    )
    for bom, encoding in _BOMS:
        if body.startswith(bom):
            try:
                return body[len(bom) :].decode(encoding, "strict")
            except (LookupError, UnicodeDecodeError):
                break
    for encoding in candidates:
        try:
            return body.decode(encoding, "strict")
        except (LookupError, UnicodeDecodeError):
            continue
    for encoding in ("utf-8", "gb18030", "big5hkscs", "cp1252"):
        try:
            return body.decode(encoding, "strict")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def _xml_declared(body: bytes) -> str:
    match = _XML_DECL.search(body[:1024])
    return match.group(1).decode("ascii", "ignore") if match else ""


def _feed_address_error() -> FeedError:
    return FeedError(
        "这是一个订阅源地址；请直接填写文章网页链接，或在「订阅」里添加它",
        "is_feed",
    )


def _looks_like_feed(result: FetchResult, text: str) -> bool:
    if result.content_type in _FEED_TYPES:
        return True
    head = text.lstrip()[:2048]
    if not head.startswith("<?xml") and not head.startswith("<rss") and not head.startswith("<feed") and not head.startswith("<rdf"):
        return False
    return bool(
        re.search(r"<(?:rss|feed|rdf:RDF)[\s>]", head, re.IGNORECASE)
    )


def fetch_page(
    raw_url: str,
    *,
    timeout: float = FETCH_TIMEOUT,
) -> tuple[FetchResult, str]:
    """Fetch an ordinary web page for the save-for-later reader."""

    url = normalize_page_url(raw_url)
    result = fetch(
        url,
        timeout=timeout,
        max_bytes=MAX_PAGE_BYTES,
        accept=(
            "text/html, application/xhtml+xml, application/xml;q=0.8, "
            "*/*;q=0.5"
        ),
        label="这篇文章",
        invalid_label="文章链接",
    )
    if result.not_modified:
        raise FeedError("这个页面没有返回内容", "no_content")
    content_type = result.content_type
    if content_type in _FEED_TYPES:
        raise _feed_address_error()
    if content_type and not (
        content_type.startswith("text/")
        or content_type in {"application/xhtml+xml", "application/xml"}
        or "html" in content_type
        or content_type == "application/octet-stream"
    ):
        raise FeedError("这个链接不是网页，无法提取正文", "not_html")
    text = decode_html_body(result.body, result.charset)
    if _looks_like_feed(result, text):
        raise _feed_address_error()
    return result, text


def parse_feed(text: str, base_url: str = "") -> ParsedFeed:
    _safe_xml(text)
    if len(text) > MAX_FEED_BYTES:
        raise FeedError("订阅源内容过大", "too_large")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FeedError(f"订阅源不是有效的 XML：{exc}", "invalid_feed") from exc

    root_name = _local(root.tag)
    entries: list[ParsedEntry] = []
    if root_name == "rss":
        channel = _find_child(root, "channel")
        if channel is None:
            raise FeedError("RSS 订阅源缺少 channel 节点", "invalid_feed")
        base = base_url or ""
        title = html_to_text(_text_of(_find_child(channel, "title")))
        site = urllib.parse.urljoin(
            base, _text_of(_find_child(channel, "link")).strip() or base
        )
        description = clean_text(
            html_to_text(_text_of(_find_child(channel, "description"))), 1000
        )
        icon = _feed_icon(channel, base)
        items = _find_children(channel, "item")
        kind = "rss"
        for item in items[:MAX_ENTRIES]:
            link = _rss_link(item, base)
            item_title = html_to_text(
                _text_of(_find_child(item, "title"))
            ) or "(无标题)"
            author = clean_text(
                _text_of(_find_child(item, "author"))
                or _text_of(_find_child(item, "creator")),
                MAX_PERSON_CHARS,
            )
            published = parse_date(
                _text_of(_find_child(item, "pubdate"))
                or _text_of(_find_child(item, "date"))
                or _text_of(_find_child(item, "published"))
            )
            content_html = _rss_content(item)
            item_description = _find_child(item, "description")
            summary_html = (
                _inner_html(item_description) if item_description is not None else ""
            )
            guid_raw = _text_of(_find_child(item, "guid"))
            guid = _entry_guid(guid_raw, link, item_title, published)
            entries.append(
                ParsedEntry(
                    guid=guid,
                    url=link,
                    title=item_title[:MAX_TITLE_CHARS],
                    author=author,
                    published_at=published,
                    summary_html=summary_html,
                    content_html=content_html or summary_html,
                )
            )
        return ParsedFeed(
            title=title[:MAX_TITLE_CHARS],
            site_url=site,
            description=description,
            icon_url=icon,
            entries=_dedupe_entries(entries),
            kind=kind,
        )

    if root_name == "rdf":
        channel = _find_child(root, "channel")
        base = base_url or ""
        title = html_to_text(_text_of(_find_child(channel, "title")))
        site = urllib.parse.urljoin(
            base, _text_of(_find_child(channel, "link")).strip() or base
        )
        description = clean_text(
            html_to_text(_text_of(_find_child(channel, "description"))), 1000
        )
        icon = ""
        for item in _find_children(root, "item")[:MAX_ENTRIES]:
            link = _rss_link(item, base)
            item_title = html_to_text(
                _text_of(_find_child(item, "title"))
            ) or "(无标题)"
            author = clean_text(
                _text_of(_find_child(item, "creator")), MAX_PERSON_CHARS
            )
            published = parse_date(
                _text_of(_find_child(item, "date"))
                or _text_of(_find_child(item, "pubdate"))
            )
            content_html = _rss_content(item)
            item_description = _find_child(item, "description")
            summary_html = (
                _inner_html(item_description) if item_description is not None else ""
            )
            guid = _entry_guid(
                _text_of(_find_child(item, "guid")),
                link,
                item_title,
                published,
            )
            entries.append(
                ParsedEntry(
                    guid=guid,
                    url=link,
                    title=item_title[:MAX_TITLE_CHARS],
                    author=author,
                    published_at=published,
                    summary_html=summary_html,
                    content_html=content_html or summary_html,
                )
            )
        return ParsedFeed(
            title=title[:MAX_TITLE_CHARS],
            site_url=site,
            description=description,
            icon_url=icon,
            entries=_dedupe_entries(entries),
            kind="rdf",
        )

    if root_name == "feed":
        base = base_url or ""
        title = html_to_text(_text_of(_find_child(root, "title")))
        site = _atom_link(root, base)
        description = clean_text(
            html_to_text(_text_of(_find_child(root, "subtitle"))), 1000
        )
        icon = _feed_icon(root, base)
        for entry in _find_children(root, "entry")[:MAX_ENTRIES]:
            link = _atom_link(entry, base)
            item_title = html_to_text(
                _text_of(_find_child(entry, "title"))
            ) or "(无标题)"
            author_el = _find_child(entry, "author")
            author = clean_text(
                _text_of(_find_child(author_el, "name")), MAX_PERSON_CHARS
            )
            published = parse_date(
                _text_of(_find_child(entry, "published"))
                or _text_of(_find_child(entry, "updated"))
            )
            content_html, summary_html = _atom_content(entry)
            guid = _entry_guid(
                _text_of(_find_child(entry, "id")),
                link,
                item_title,
                published,
            )
            entries.append(
                ParsedEntry(
                    guid=guid,
                    url=link,
                    title=item_title[:MAX_TITLE_CHARS],
                    author=author,
                    published_at=published,
                    summary_html=summary_html,
                    content_html=content_html or summary_html,
                )
            )
        return ParsedFeed(
            title=title[:MAX_TITLE_CHARS],
            site_url=site,
            description=description,
            icon_url=icon,
            entries=_dedupe_entries(entries),
            kind="atom",
        )

    raise FeedError(
        "无法识别的订阅格式（支持 RSS 2.0、RSS 1.0 和 Atom）", "invalid_feed"
    )


def _dedupe_entries(entries: Iterable[ParsedEntry]) -> list[ParsedEntry]:
    seen: set[str] = set()
    result: list[ParsedEntry] = []
    for entry in entries:
        if entry.guid in seen:
            continue
        seen.add(entry.guid)
        result.append(entry)
    return result


class _FeedLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link":
            return
        values = {name.lower(): value or "" for name, value in attrs}
        rel = values.get("rel", "").lower().split()
        content_type = values.get("type", "").lower().split(";", 1)[0].strip()
        href = values.get("href", "").strip()
        if (
            "alternate" in rel
            and content_type in _FEED_TYPES
            and href
            and len(self.links) < 10
        ):
            resolved = urllib.parse.urljoin(self.base_url, href)
            if resolved not in self.links:
                self.links.append(resolved)


def discover_feed_links(page: str, base_url: str) -> list[str]:
    parser = _FeedLinkParser(base_url)
    try:
        parser.feed(page)
        parser.close()
    except Exception:
        return parser.links
    return parser.links


def fetch_feed(
    raw_url: str,
    *,
    etag: str = "",
    last_modified: str = "",
    timeout: float = FETCH_TIMEOUT,
) -> tuple[FetchResult, ParsedFeed | None]:
    url = normalize_feed_url(raw_url)
    result = fetch(url, etag=etag, last_modified=last_modified, timeout=timeout)
    if result.not_modified:
        return result, None
    text = decode_body(result.body, result.charset)
    try:
        parsed = parse_feed(text, result.final_url)
        return result, parsed
    except FeedError as first_error:
        links = discover_feed_links(text, result.final_url)
        last_error = first_error
        for link in links[:3]:
            try:
                followed = fetch(link, timeout=timeout)
                if followed.not_modified:
                    continue
                followed_text = decode_body(followed.body, followed.charset)
                parsed = parse_feed(followed_text, followed.final_url)
                return followed, parsed
            except FeedError as exc:
                last_error = exc
        if not links:
            raise FeedError(
                f"{first_error.message}；如果这是网页地址，请填写其 RSS/Atom 订阅地址",
                first_error.code,
            ) from first_error
        raise last_error


def fetch_image(raw_url: str, *, timeout: float = MEDIA_TIMEOUT) -> FetchedImage:
    url = _validate_http_url(raw_url, "图片地址")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,image/*;q=0.8",
            "Accept-Encoding": "identity",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_RedirectHandler())
    with _MEDIA_SEMAPHORE:
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            raise FeedError(f"图片返回 HTTP {exc.code}", "image_error") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise FeedError(f"无法获取图片：{exc}", "image_error") from exc
        with response:
            content_type = (
                response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            )
            if content_type not in ALLOWED_IMAGE_TYPES:
                raise FeedError("远程资源不是受支持的图片类型", "unsupported_media")
            body = _read_limited(response, MAX_IMAGE_BYTES)
            if len(body) > MAX_IMAGE_BYTES:
                raise FeedError("图片超过 8 MiB 大小限制", "too_large")
            if not body:
                raise FeedError("图片内容为空", "image_error")
            return FetchedImage(body=body, content_type=content_type)


def _opml_category(node: ET.Element, parent: str) -> str:
    text = (node.get("text") or node.get("title") or "").strip()
    return parent or clean_text(text, 60)


def parse_opml(text: str) -> list[OpmlEntry]:
    if len(text.encode("utf-8", "replace")) > MAX_OPML_BYTES:
        raise FeedError("OPML 文件超过 4 MiB 大小限制", "too_large")
    _safe_xml(text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FeedError(f"OPML 不是有效的 XML：{exc}", "invalid_opml") from exc
    if _local(root.tag) != "opml":
        raise FeedError("文件不是 OPML 订阅列表", "invalid_opml")
    body = _find_child(root, "body")
    if body is None:
        raise FeedError("OPML 缺少 body 节点", "invalid_opml")

    entries: list[OpmlEntry] = []
    seen: set[str] = set()

    def walk(node: ET.Element, category: str) -> None:
        for child in node:
            if _local(child.tag) != "outline":
                continue
            xml_url = (child.get("xmlUrl") or child.get("xmlurl") or "").strip()
            text_value = (child.get("text") or child.get("title") or "").strip()
            if xml_url:
                try:
                    normalized = normalize_feed_url(xml_url)
                except FeedError:
                    continue
                key = normalized.lower()
                if key in seen or len(entries) >= MAX_FEED_URLS:
                    continue
                seen.add(key)
                entries.append(
                    OpmlEntry(
                        title=clean_text(text_value, MAX_TITLE_CHARS),
                        xml_url=normalized,
                        category=clean_text(category, 60),
                        html_url=(child.get("htmlUrl") or "").strip(),
                    )
                )
            else:
                walk(child, _opml_category(child, category))

    walk(body, "")
    return entries


def build_opml(groups: list[tuple[str, list[dict[str, Any]]]]) -> str:
    now = format_datetime(datetime.now(timezone.utc))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head>",
        "    <title>Shiye subscriptions</title>",
        f"    <dateCreated>{xml_escape(now)}</dateCreated>",
        "  </head>",
        "  <body>",
    ]

    def outline(feed: dict[str, Any], indent: str) -> str:
        title = clean_text(feed.get("title") or feed.get("url") or "", MAX_TITLE_CHARS)
        url = str(feed.get("url") or "")
        site = str(feed.get("site_url") or "")
        parts = [
            f"type={quoteattr('rss')}",
            f"text={quoteattr(title)}",
            f"title={quoteattr(title)}",
            f"xmlUrl={quoteattr(url)}",
        ]
        if site:
            parts.append(f"htmlUrl={quoteattr(site)}")
        return f"{indent}<outline {' '.join(parts)}/>"

    for name, feeds in groups:
        if name:
            lines.append(f"    <outline text={quoteattr(name)} title={quoteattr(name)}>")
            for feed in feeds:
                lines.append(outline(feed, "      "))
            lines.append("    </outline>")
        else:
            for feed in feeds:
                lines.append(outline(feed, "    "))
    lines.extend(["  </body>", "</opml>", ""])
    return "\n".join(lines)
