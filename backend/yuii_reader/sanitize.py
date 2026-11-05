"""Conservative HTML sanitizing and text extraction for feed content."""

from __future__ import annotations

import html
import re
import urllib.parse
from html.parser import HTMLParser

MAX_SANITIZED_CHARS = 400_000
MAX_SUMMARY_CHARS = 600
MAX_ALT_CHARS = 300

_BLOCKED_TAGS = {
    "applet",
    "audio",
    "base",
    "basefont",
    "blink",
    "button",
    "canvas",
    "embed",
    "form",
    "frame",
    "frameset",
    "head",
    "iframe",
    "input",
    "link",
    "marquee",
    "math",
    "meta",
    "noscript",
    "object",
    "option",
    "optgroup",
    "script",
    "select",
    "source",
    "style",
    "svg",
    "template",
    "textarea",
    "title",
    "track",
    "video",
}

_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

_ALLOWED_TAGS = {
    "a",
    "abbr",
    "article",
    "aside",
    "b",
    "blockquote",
    "br",
    "caption",
    "cite",
    "code",
    "dd",
    "del",
    "details",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "i",
    "img",
    "ins",
    "kbd",
    "li",
    "main",
    "mark",
    "nav",
    "ol",
    "p",
    "pre",
    "q",
    "s",
    "section",
    "small",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "time",
    "tr",
    "u",
    "ul",
    "var",
    "wbr",
}

_GLOBAL_ATTRS = {"title", "lang", "dir"}
_TAG_ATTRS = {
    "a": {"href"},
    "img": {"src", "srcset", "alt", "width", "height"},
    "blockquote": {"cite"},
    "q": {"cite"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "ol": {"start", "reversed", "type"},
    "li": {"value"},
    "time": {"datetime"},
    "details": {"open"},
}
_CLASS_TAGS = {"code", "pre"}
_CLASS_PATTERN = re.compile(r"^language-[A-Za-z0-9_+#.\-]{1,30}$")
_DATA_IMAGE = re.compile(
    r"^data:image/(?:png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]{1,120000}$",
    re.IGNORECASE,
)
_INTEGER = re.compile(r"^\d{1,6}$")
_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "gbraid",
    "wbraid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "mkt_tok",
    "yclid",
    "_hsenc",
    "_hsmi",
    "spm",
}
_TRACKING_PREFIXES = ("utm_",)
_BLOCK_BREAK = {
    "address",
    "article",
    "aside",
    "blockquote",
    "dd",
    "details",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "summary",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}


def strip_tracking_params(url: str) -> str:
    """Remove common campaign parameters without touching other query data."""

    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    kept: list[tuple[str, str]] = []
    changed = False
    for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in _TRACKING_PARAMS or any(
            lowered.startswith(prefix) for prefix in _TRACKING_PREFIXES
        ):
            changed = True
            continue
        kept.append((key, value))
    if not changed:
        return url
    query = urllib.parse.urlencode(kept, doseq=True)
    return urllib.parse.urlunsplit(parts._replace(query=query))


def proxy_image_url(url: str) -> str:
    return "api/media?url=" + urllib.parse.quote(strip_tracking_params(url), safe="")


def _clean_url(value: str, base_url: str, schemes: set[str]) -> str:
    raw = (value or "").strip()
    if not raw or raw.startswith("#"):
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    if raw.lower().startswith(("javascript:", "vbscript:", "file:", "data:")):
        return ""
    try:
        resolved = urllib.parse.urljoin(base_url, raw) if base_url else raw
        parts = urllib.parse.urlsplit(resolved)
    except ValueError:
        return ""
    if parts.scheme.lower() not in schemes:
        return ""
    if parts.scheme.lower() in {"http", "https"}:
        if not parts.hostname:
            return ""
        return strip_tracking_params(resolved)
    if parts.scheme.lower() == "mailto":
        if not parts.path or "\n" in parts.path:
            return ""
        return resolved
    return ""


def _clean_class(value: str) -> str:
    tokens = [token for token in value.split() if _CLASS_PATTERN.match(token)]
    return " ".join(tokens[-3:])


def _is_tracker(src: str, width: str, height: str) -> bool:
    if width == "1" and height == "1":
        return True
    if not width and not height:
        lowered = src.lower()
        if any(
            marker in lowered
            for marker in (
                "1x1.",
                "/pixel.",
                "pixel.gif",
                "pixel.png",
                "beacon.gif",
                "beacon.png",
                "spacer.gif",
                "spacer.png",
                "open.aspx",
                "open.php",
            )
        ):
            return True
    return False


class _Sanitizer(HTMLParser):
    def __init__(self, base_url: str = "", limit: int = MAX_SANITIZED_CHARS) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.limit = limit
        self._output: list[str] = []
        self._open: list[str] = []
        self._blocked: list[str] = []
        self._length = 0
        self.truncated = False

    def _emit(self, piece: str) -> None:
        if self.truncated:
            return
        if self._length + len(piece) > self.limit:
            self.truncated = True
            self._output.append("…")
            return
        self._output.append(piece)
        self._length += len(piece)

    def _clean_attributes(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> list[tuple[str, str]]:
        allowed = _GLOBAL_ATTRS | _TAG_ATTRS.get(tag, set())
        if tag in _CLASS_TAGS:
            allowed = allowed | {"class"}
        result: list[tuple[str, str]] = []
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            value = (raw_value or "").strip()[:4000]
            if name not in allowed or not value:
                continue
            if name == "class":
                cleaned = _clean_class(value)
                if cleaned:
                    result.append(("class", cleaned))
                continue
            if name in {"width", "height", "colspan", "rowspan", "start", "value"}:
                if not _INTEGER.match(value):
                    continue
            if name == "href":
                value = _clean_url(value, self.base_url, {"http", "https", "mailto"})
                if not value:
                    continue
            elif name == "cite":
                value = _clean_url(value, self.base_url, {"http", "https"})
                if not value:
                    continue
            elif name == "src":
                if _DATA_IMAGE.match(value):
                    pass
                else:
                    value = _clean_url(value, self.base_url, {"http", "https"})
                    if not value:
                        continue
                    value = proxy_image_url(value)
            elif name == "srcset":
                candidates: list[str] = []
                for item in value.split(","):
                    bits = item.strip().split()
                    if not bits:
                        continue
                    candidate = _clean_url(bits[0], self.base_url, {"http", "https"})
                    if not candidate:
                        continue
                    descriptor = ""
                    if len(bits) > 1 and re.match(r"^\d{1,4}(?:\.\d)?[wx]$", bits[1]):
                        descriptor = " " + bits[1]
                    candidates.append(proxy_image_url(candidate) + descriptor)
                    if len(candidates) >= 6:
                        break
                if not candidates:
                    continue
                value = ", ".join(candidates)
            elif name == "datetime":
                value = value[:80]
            result.append((name, value))
        return result

    def _start(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._blocked:
            if tag in _BLOCKED_TAGS:
                self._blocked.append(tag)
            return
        if tag in _BLOCKED_TAGS:
            self._blocked.append(tag)
            return
        if tag not in _ALLOWED_TAGS:
            return
        if tag == "img":
            values = {name.lower(): (value or "").strip() for name, value in attrs}
            if _is_tracker(
                values.get("src", ""), values.get("width", ""), values.get("height", "")
            ):
                return
        if len(self._open) >= 24:
            return
        cleaned = self._clean_attributes(tag, attrs)
        rendered = "".join(
            f' {name}="{html.escape(value, quote=True)}"' for name, value in cleaned
        )
        if tag == "a" and not any(name == "href" for name, _ in cleaned):
            return
        if tag == "img" and not any(name == "src" for name, _ in cleaned):
            return
        if tag == "img":
            rendered += (
                ' loading="lazy" decoding="async" referrerpolicy="no-referrer"'
            )
        if tag == "a":
            rendered += ' target="_blank" rel="noopener noreferrer nofollow"'
        if tag == "details":
            rendered += " open"
        if tag in _VOID_TAGS:
            self._emit(f"<{tag}{rendered}>")
            return
        self._emit(f"<{tag}{rendered}>")
        self._open.append(tag)

    def _end(self, tag: str) -> None:
        if self._blocked:
            if tag in _BLOCKED_TAGS:
                for index in range(len(self._blocked) - 1, -1, -1):
                    if self._blocked[index] == tag:
                        del self._blocked[index:]
                        break
            return
        if tag not in self._open:
            return
        while self._open:
            current = self._open.pop()
            self._emit(f"</{current}>")
            if current == tag:
                break

    def handle_starttag(self, tag: str, attrs) -> None:
        self._start(tag.lower(), attrs)

    def handle_startendtag(self, tag: str, attrs) -> None:
        name = tag.lower()
        depth = len(self._open)
        self._start(name, attrs)
        if (
            name not in _VOID_TAGS
            and len(self._open) == depth + 1
            and self._open[-1] == name
        ):
            self._open.pop()
            self._emit(f"</{name}>")

    def handle_endtag(self, tag: str) -> None:
        self._end(tag.lower())

    def handle_data(self, data: str) -> None:
        if self._blocked or not data:
            return
        self._emit(html.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def close(self) -> str:
        super().close()
        while self._open:
            self._emit(f"</{self._open.pop()}>")
        return "".join(self._output)


def sanitize_fragment(
    value: str,
    base_url: str = "",
    limit: int = MAX_SANITIZED_CHARS,
) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) > MAX_SANITIZED_CHARS * 2:
        text = text[: MAX_SANITIZED_CHARS * 2]
    parser = _Sanitizer(base_url=base_url, limit=limit)
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        # HTMLParser is forgiving, but never let malformed content break a refresh.
        return html.escape(text[:4000])
    return "".join(parser._output)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._blocked: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        name = tag.lower()
        if self._blocked:
            if name in _BLOCKED_TAGS:
                self._blocked.append(name)
            return
        if name in _BLOCKED_TAGS:
            self._blocked.append(name)
            return
        if name == "br":
            self._parts.append("\n")
        elif name in _BLOCK_BREAK:
            self._parts.append("\n")
            if name == "li":
                self._parts.append("• ")
        elif name in {"td", "th"}:
            self._parts.append(" ")
        elif name == "img":
            for raw_name, raw_value in attrs:
                if raw_name.lower() == "alt" and raw_value:
                    self._parts.append(" " + raw_value + " ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if self._blocked:
            if name in _BLOCKED_TAGS:
                for index in range(len(self._blocked) - 1, -1, -1):
                    if self._blocked[index] == name:
                        del self._blocked[index:]
                        break
            return
        if name in _BLOCK_BREAK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._blocked or not data:
            return
        self._parts.append(data)


def html_to_text(value: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Return a single-line plain-text representation, capped by ``limit``."""

    if not value:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(str(value))
        parser.close()
        text = "".join(parser._parts)
    except Exception:
        text = str(value)
    text = html.unescape(text)
    text = text.replace(" ", " ").replace("​", "")
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def summarize_html(value: str, base_url: str = "", limit: int = MAX_SUMMARY_CHARS) -> str:
    if not value:
        return ""
    sanitized = sanitize_fragment(value, base_url=base_url, limit=120_000)
    return html_to_text(sanitized, limit=limit)
