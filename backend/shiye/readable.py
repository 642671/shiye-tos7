"""Readability-style article extraction built on the Python standard library.

Saved links are the second half of Shiye's reading inbox.  This module turns a
raw HTML page into a clean article body without pulling in a third-party
readability package: it parses the document into a small tree, scores the
likely content containers, removes navigation and promotional noise, promotes
lazy-loaded images and finally hands the result to the existing sanitizer.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterable

from .sanitize import html_to_text, sanitize_fragment

MAX_PAGE_CHARS = 6_000_000
MAX_CONTENT_CHARS = 400_000
MAX_TREE_DEPTH = 180


class ReadableError(Exception):
    def __init__(self, message: str, code: str = "no_content") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class ReadableArticle:
    title: str
    byline: str
    site_name: str
    excerpt: str
    content_html: str
    lead_image: str
    published_hint: str
    word_count: int
    reading_minutes: int


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(
        self,
        tag: str,
        attrs: dict[str, str] | None = None,
        parent: "_Node | None" = None,
    ) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[_Node | str] = []
        self.parent = parent


_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

_DROP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "object",
    "embed",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "option",
    "dialog",
    "nav",
    "aside",
    "footer",
    "menu",
    "video",
    "audio",
    "map",
    "link",
    "meta",
    "head",
    "title",
}

_UNWRAP_TAGS = {
    "html",
    "body",
    "font",
    "center",
    "big",
    "tt",
    "marquee",
    "picture",
    "hgroup",
}

_CONTAINER_TAGS = {"article", "main", "section", "div", "td", "body"}
_TEXT_BLOCK_TAGS = {"p", "pre", "blockquote", "li", "td"}
_CONTENT_HINTS = (
    "article-body",
    "article-content",
    "article__body",
    "article__content",
    "post-content",
    "post-body",
    "post__content",
    "entry-content",
    "entry-body",
    "story-body",
    "story-content",
    "content-body",
    "content__body",
    "markdown-body",
    "rich_media_content",
    "正文",
)

_BAD_TOKEN_PATTERN = re.compile(
    r"(?:^|[-_])(?:"
    r"comment|comments|share|sharing|social|related|recommend|recommended|"
    r"sidebar|footer|foot|nav|navbar|navigation|menu|cookie|consent|banner|"
    r"promo|promotion|sponsor|sponsored|subscribe|subscription|newsletter|"
    r"popup|modal|breadcrumb|pagination|pager|toolbar|login|signup|sign-up|"
    r"advert|advertisement|advertising|ad-slot|adsbygoogle|outbrain|taboola|"
    r"widget|disqus|rating|follow|author-box|bio|meta-bar|tags|tag-list|"
    r"hidden|screen-reader|sr-only|skip-link"
    r")(?:$|[-_])"
)

_BAD_TOKEN_EXACT = {
    "ad",
    "ads",
    "nav",
    "menu",
    "footer",
    "header-nav",
    "comments",
    "share",
    "social",
    "promo",
    "sponsored",
    "subscribe",
    "newsletter",
    "cookie",
    "consent",
    "modal",
    "popup",
    "sidebar",
    "related",
    "recommended",
    "breadcrumb",
    "pagination",
    "pager",
    "toolbar",
    "hidden",
    "sr-only",
    "screen-reader",
}

_ALLOWED_OUTPUT_TAGS = {
    "a",
    "abbr",
    "article",
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
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "ins",
    "kbd",
    "li",
    "main",
    "mark",
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
}

_KEEP_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "srcset", "alt", "width", "height", "title"},
    "blockquote": {"cite"},
    "q": {"cite"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "ol": {"start", "type"},
    "li": {"value"},
    "time": {"datetime"},
    "details": {"open"},
    "code": {"class"},
    "pre": {"class"},
}

_LAZY_SRC_ATTRS = (
    "data-src",
    "data-original",
    "data-lazy-src",
    "data-lazy",
    "data-url",
    "data-image",
    "data-actualsrc",
)
_LAZY_SRCSET_ATTRS = ("data-srcset", "data-lazy-srcset")
_DATE_PATTERN = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")
_ARTICLE_TYPES = {
    "article",
    "newsarticle",
    "blogposting",
    "report",
    "reportagenewsarticle",
    "analysisnewsarticle",
    "opinionnewsarticle",
    "techarticle",
    "scholarlyarticle",
}
_CJK_PATTERN = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿가-힯]"
)
_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['’\-][A-Za-z0-9]+)*")


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document")
        self.stack: list[_Node] = [self.root]

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self.stack) >= MAX_TREE_DEPTH:
            return
        values = {
            name.lower(): (value or "").strip()
            for name, value in attrs
            if name
        }
        node = _Node(tag, values, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_starttag(self, tag: str, attrs) -> None:
        name = tag.lower()
        if name in {"p", "li", "dt", "dd", "tr", "td", "th"}:
            while len(self.stack) > 1 and self.stack[-1].tag == name:
                self.stack.pop()
        self._open(name, attrs)

    def handle_startendtag(self, tag: str, attrs) -> None:
        name = tag.lower()
        values = {
            name.lower(): (value or "").strip()
            for name, value in attrs
            if name
        }
        node = _Node(name, values, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == name:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)

    def handle_entityref(self, name: str) -> None:
        self.handle_data(html.unescape("&" + name + ";"))

    def handle_charref(self, name: str) -> None:
        self.handle_data(html.unescape("&#" + name + ";"))


def _walk(node: _Node) -> Iterable[_Node]:
    yield node
    for child in node.children:
        if isinstance(child, _Node):
            yield from _walk(child)


def _attr_text(node: _Node) -> str:
    return " ".join(
        [node.attrs.get("class", ""), node.attrs.get("id", "")]
    ).lower()


def _is_noise(node: _Node) -> bool:
    if node.tag in _DROP_TAGS:
        return True
    ident = _attr_text(node)
    if ident:
        tokens = {token for token in re.split(r"[^a-z0-9]+", ident) if token}
        if tokens & _BAD_TOKEN_EXACT or _BAD_TOKEN_PATTERN.search(ident):
            return True
    if "hidden" in node.attrs or node.attrs.get("aria-hidden") == "true":
        return True
    return False


def _node_text(node: _Node | str) -> str:
    parts: list[str] = []

    def walk(value: _Node | str) -> None:
        if isinstance(value, str):
            parts.append(value)
            return
        if value.tag in {"script", "style", "noscript", "template"}:
            return
        for child in value.children:
            walk(child)
        if value.tag in _TEXT_BLOCK_TAGS or value.tag in {
            "div",
            "section",
            "article",
            "br",
            "li",
        }:
            parts.append(" ")

    walk(node)
    return " ".join("".join(parts).split())


def _measure(node: _Node, memo: dict[int, tuple[int, int]]) -> tuple[int, int]:
    key = id(node)
    cached = memo.get(key)
    if cached is not None:
        return cached
    if node.tag in {"script", "style", "noscript", "template"}:
        result = (0, 0)
        memo[key] = result
        return result
    total = 0
    linked = 0
    for child in node.children:
        if isinstance(child, str):
            total += len(child)
        else:
            child_total, child_linked = _measure(child, memo)
            total += child_total
            linked += child_linked
    if node.tag == "a":
        linked = total
    result = (total, linked)
    memo[key] = result
    return result


def _first(node: _Node, tags: set[str]) -> _Node | None:
    for item in _walk(node):
        if item.tag in tags:
            return item
    return None


def _find_meta(root: _Node) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in _walk(root):
        if node.tag == "meta":
            key = (
                node.attrs.get("property")
                or node.attrs.get("name")
                or node.attrs.get("itemprop")
                or ""
            ).lower()
            content = node.attrs.get("content", "").strip()
            if key and content and key not in values:
                values[key] = content
        elif node.tag == "link":
            rel = node.attrs.get("rel", "").lower()
            href = node.attrs.get("href", "").strip()
            if "canonical" in rel and href and "canonical" not in values:
                values["canonical"] = href
    return values


def _raw_script_text(node: _Node) -> str:
    return "".join(
        child for child in node.children if isinstance(child, str)
    ).strip()


def _json_ld_objects(root: _Node) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in _walk(root):
        if node.tag != "script":
            continue
        if node.attrs.get("type", "").lower() != "application/ld+json":
            continue
        raw = _raw_script_text(node)
        if not raw or len(raw) > 1_000_000:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        queue = payload if isinstance(payload, list) else [payload]
        while queue:
            item = queue.pop(0)
            if isinstance(item, list):
                queue.extend(item[:50])
                continue
            if not isinstance(item, dict):
                continue
            result.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                queue.extend(graph[:50])
            if len(result) > 200:
                return result
    return result


def _type_names(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.rsplit("/", 1)[-1].lower()}
    if isinstance(value, list):
        names: set[str] = set()
        for item in value:
            names |= _type_names(item)
        return names
    return set()


def _json_ld_article(root: _Node) -> dict[str, Any]:
    for item in _json_ld_objects(root):
        if _type_names(item.get("@type")) & _ARTICLE_TYPES:
            return item
    return {}


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("name", "headline", "@value"):
            if isinstance(value.get(key), str):
                return value[key]
    if isinstance(value, list):
        for item in value:
            text = _as_text(item)
            if text:
                return text
    return ""


def _as_image(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("url", "contentUrl", "@id"):
            if isinstance(value.get(key), str):
                return value[key]
    if isinstance(value, list):
        for item in value:
            found = _as_image(item)
            if found:
                return found
    return ""


def _host_label(url: str) -> str:
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _resolve(url: str, base_url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    try:
        resolved = urllib.parse.urljoin(base_url, value)
        parts = urllib.parse.urlsplit(resolved)
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return ""
    return resolved


def _clean_title(raw: str, site_name: str, host: str) -> str:
    title = " ".join(html.unescape(raw or "").split())
    if not title:
        return ""
    lowered = title.lower()
    site_lowered = site_name.lower().strip()
    host_lowered = host.lower()
    for separator in (" | ", " — ", " – ", " - ", " · ", " :: ", " / "):
        if separator not in title:
            continue
        head, tail = title.split(separator, 1)
        tail_lowered = tail.strip().lower()
        if len(tail) <= 60 and (
            tail_lowered == site_lowered
            or tail_lowered == host_lowered
            or tail_lowered.endswith("." + host_lowered)
        ):
            title = head.strip()
            break
        if len(head) <= 60 and (
            head.lower() == site_lowered or head.lower() == host_lowered
        ):
            title = tail.strip()
            break
    if len(title) > 300:
        title = title[:299].rstrip() + "…"
    return title


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _image_source(node: _Node, base_url: str) -> tuple[str, str]:
    src = node.attrs.get("src", "").strip()
    lowered = src.lower()
    if not src or lowered.startswith("data:") or "blank.gif" in lowered:
        src = ""
    if not src:
        for name in _LAZY_SRC_ATTRS:
            candidate = node.attrs.get(name, "").strip()
            if candidate and not candidate.lower().startswith("data:"):
                src = candidate
                break
    srcset = node.attrs.get("srcset", "").strip()
    if not srcset:
        for name in _LAZY_SRCSET_ATTRS:
            candidate = node.attrs.get(name, "").strip()
            if candidate:
                srcset = candidate
                break
    if not src and srcset:
        first = srcset.split(",", 1)[0].strip().split()
        if first:
            src = first[0]
    resolved = _resolve(src, base_url)
    resolved_srcset = ""
    if srcset:
        candidates: list[str] = []
        for part in srcset.split(",")[:5]:
            bits = part.strip().split()
            if not bits:
                continue
            candidate = _resolve(bits[0], base_url)
            if not candidate:
                continue
            descriptor = ""
            if len(bits) > 1 and re.match(r"^\d{1,4}(?:\.\d)?[wx]$", bits[1]):
                descriptor = " " + bits[1]
            candidates.append(candidate + descriptor)
        resolved_srcset = ", ".join(candidates)
    return resolved, resolved_srcset


def _is_tracker_image(node: _Node) -> bool:
    width = node.attrs.get("width", "").strip().lower()
    height = node.attrs.get("height", "").strip().lower()
    if width in {"1", "1px"} and height in {"1", "1px"}:
        return True
    ident = _attr_text(node)
    if ident and re.search(r"(?:^|[-_])(tracking|pixel|beacon)(?:$|[-_])", ident):
        return True
    src = node.attrs.get("src", "").lower()
    return any(
        marker in src
        for marker in ("1x1.", "/pixel.", "pixel.gif", "pixel.png", "beacon.gif")
    )


def _serialize(
    node: _Node,
    out: list[str],
    *,
    base_url: str,
    title_key: str,
    title_removed: list[bool],
    depth: int = 0,
) -> None:
    if depth > MAX_TREE_DEPTH:
        return
    if isinstance(node, str):
        return
    if _is_noise(node):
        return
    tag = node.tag
    if tag in _UNWRAP_TAGS:
        for child in node.children:
            _serialize(
                child,
                out,
                base_url=base_url,
                title_key=title_key,
                title_removed=title_removed,
                depth=depth + 1,
            )
        return
    if tag not in _ALLOWED_OUTPUT_TAGS:
        for child in node.children:
            _serialize(
                child,
                out,
                base_url=base_url,
                title_key=title_key,
                title_removed=title_removed,
                depth=depth + 1,
            )
        return
    if tag in {"h1", "h2"} and not title_removed[0] and title_key:
        if _normalized(_node_text(node)) == title_key:
            title_removed[0] = True
            return
    if tag == "img":
        if _is_tracker_image(node):
            return
        src, srcset = _image_source(node, base_url)
        if not src:
            return
        attrs = [f'src="{html.escape(src, quote=True)}"']
        if srcset:
            attrs.append(f'srcset="{html.escape(srcset, quote=True)}"')
        alt = node.attrs.get("alt", "").strip()[:300]
        if alt:
            attrs.append(f'alt="{html.escape(alt, quote=True)}"')
        out.append("<img " + " ".join(attrs) + ">")
        return
    if tag == "figure":
        if not any(
            item.tag == "img" for item in _walk(node) if isinstance(item, _Node)
        ):
            return
    if tag == "a":
        href = _resolve(node.attrs.get("href", ""), base_url)
        if not href:
            for child in node.children:
                _serialize(
                    child,
                    out,
                    base_url=base_url,
                    title_key=title_key,
                    title_removed=title_removed,
                    depth=depth + 1,
                )
            return
        out.append(f'<a href="{html.escape(href, quote=True)}">')
        for child in node.children:
            _serialize(
                child,
                out,
                base_url=base_url,
                title_key=title_key,
                title_removed=title_removed,
                depth=depth + 1,
            )
        out.append("</a>")
        return
    attrs: list[str] = []
    for name in _KEEP_ATTRS.get(tag, ()):
        value = node.attrs.get(name, "").strip()
        if not value:
            continue
        if name in {"href", "src"}:
            value = _resolve(value, base_url)
            if not value:
                continue
        if name == "srcset":
            continue
        if name == "class" and not re.match(
            r"^language-[A-Za-z0-9_+#.\-]{1,30}$", value
        ):
            continue
        attrs.append(f'{name}="{html.escape(value[:4000], quote=True)}"')
    rendered = (" " + " ".join(attrs)) if attrs else ""
    if tag == "br":
        out.append("<br>")
        return
    if tag == "hr":
        out.append("<hr>")
        return
    out.append(f"<{tag}{rendered}>")
    for child in node.children:
        if isinstance(child, str):
            out.append(html.escape(child, quote=False))
        else:
            _serialize(
                child,
                out,
                base_url=base_url,
                title_key=title_key,
                title_removed=title_removed,
                depth=depth + 1,
            )
    out.append(f"</{tag}>")


def _score_candidates(root: _Node) -> _Node | None:
    metrics: dict[int, tuple[int, int, int]] = {}
    nodes: dict[int, _Node] = {}
    scores: dict[int, float] = {}
    paragraph_points: dict[int, float] = {}

    def measure(node: _Node) -> tuple[int, int, int]:
        key = id(node)
        cached = metrics.get(key)
        if cached is not None:
            return cached
        if node.tag in {"script", "style", "noscript", "template"}:
            result = (0, 0, 0)
            metrics[key] = result
            return result
        total = 0
        linked = 0
        paragraphs = 0
        for child in node.children:
            if isinstance(child, str):
                total += len(child)
                continue
            child_total, child_linked, child_paragraphs = measure(child)
            total += child_total
            linked += child_linked
            paragraphs += child_paragraphs
        if node.tag == "a":
            linked = total
        if node.tag in {"p", "pre", "blockquote"} and total >= 35:
            paragraphs = max(paragraphs, 1)
        result = (total, linked, paragraphs)
        metrics[key] = result
        return result

    measure(root)
    for node in _walk(root):
        if node.tag not in _TEXT_BLOCK_TAGS:
            continue
        total, _, _ = metrics[id(node)]
        if total < 35 or node.parent is None:
            continue
        points = min(total, 800) * 0.6
        paragraph_points[id(node.parent)] = (
            paragraph_points.get(id(node.parent), 0.0) + points
        )
        if node.parent.parent is not None:
            grandparent = node.parent.parent
            paragraph_points[id(grandparent)] = (
                paragraph_points.get(id(grandparent), 0.0) + points * 0.5
            )

    for node in _walk(root):
        if node.tag not in _CONTAINER_TAGS or _is_noise(node):
            continue
        total, linked, paragraphs = metrics[id(node)]
        if total < 60:
            continue
        density = (linked / total) if total else 1.0
        if density > 0.62 and total < 900:
            continue
        score = total * (1.0 - min(density, 0.95))
        score += 28 * min(paragraphs, 18)
        if node.tag == "article":
            score += 220
        elif node.tag == "main":
            score += 120
        elif node.tag == "body":
            score -= 140
        ident = _attr_text(node)
        if ident and any(hint in ident for hint in _CONTENT_HINTS):
            score += 260
        score += paragraph_points.get(id(node), 0.0)
        scores[id(node)] = score
        nodes[id(node)] = node

    if not nodes:
        return None
    best_id = max(scores, key=lambda key: scores[key])
    return nodes[best_id]


def _extract_published(meta: dict[str, str], article_ld: dict[str, Any], root: _Node) -> str:
    for key in (
        "article:published_time",
        "datepublished",
        "date",
        "og:published_time",
        "pubdate",
    ):
        value = meta.get(key)
        if value and _DATE_PATTERN.match(value.strip()):
            return value.strip()[:60]
    for key in ("datePublished", "dateCreated", "uploadDate"):
        value = article_ld.get(key)
        if isinstance(value, str) and _DATE_PATTERN.match(value.strip()):
            return value.strip()[:60]
    for node in _walk(root):
        if node.tag == "time":
            value = node.attrs.get("datetime", "").strip()
            if value and _DATE_PATTERN.match(value):
                return value[:60]
    return ""


def _extract_byline(meta: dict[str, str], article_ld: dict[str, Any], root: _Node) -> str:
    for key in ("author", "article:author", "og:article:author", "twitter:creator"):
        value = meta.get(key, "").strip()
        if value and not value.lower().startswith(("http://", "https://")):
            return value[:200]
    author = _as_text(article_ld.get("author")) or _as_text(
        article_ld.get("creator")
    )
    if author:
        return author[:200]
    for node in _walk(root):
        ident = _attr_text(node)
        if node.tag in {"span", "div", "p", "a", "address"} and re.search(
            r"(?:^|[-_])(byline|author|writer)(?:$|[-_])", ident
        ):
            text = _node_text(node)
            if 1 < len(text) <= 120:
                return text
    return ""


def _extract_site_name(
    meta: dict[str, str], article_ld: dict[str, Any], url: str
) -> str:
    for key in ("og:site_name", "application-name", "twitter:site"):
        value = meta.get(key, "").strip().lstrip("@")
        if value:
            return value[:200]
    publisher = article_ld.get("publisher")
    name = _as_text(publisher)
    if name:
        return name[:200]
    return _host_label(url)


def text_stats(text: str) -> tuple[int, int]:
    """Return ``(word_count, reading_minutes)`` for mixed CJK/Latin text."""

    cjk = len(_CJK_PATTERN.findall(text or ""))
    latin = len(_WORD_PATTERN.findall(text or ""))
    words = cjk + latin
    if words <= 0:
        return 0, 0
    return words, max(1, round(words / 400))


def extract_article(page_html: str, url: str) -> ReadableArticle:
    if not page_html or not page_html.strip():
        raise ReadableError("这个页面没有返回内容", "no_content")
    if len(page_html) > MAX_PAGE_CHARS:
        page_html = page_html[:MAX_PAGE_CHARS]

    builder = _TreeBuilder()
    try:
        builder.feed(page_html)
        builder.close()
    except Exception as exc:  # HTMLParser is forgiving; still protect callers
        raise ReadableError("网页结构无法解析", "no_content") from exc
    root = builder.root

    meta = _find_meta(root)
    article_ld = _json_ld_article(root)
    host = _host_label(url)

    site_name = _extract_site_name(meta, article_ld, url)
    raw_title = (
        meta.get("og:title", "")
        or meta.get("twitter:title", "")
        or _as_text(article_ld.get("headline"))
    )
    if not raw_title:
        title_node = _first(root, {"title"})
        raw_title = _node_text(title_node) if title_node else ""
    if not raw_title:
        for node in _walk(root):
            if node.tag == "h1":
                candidate = _node_text(node)
                if 2 < len(candidate) < 300:
                    raw_title = candidate
                    break
    title = _clean_title(raw_title, site_name, host)
    if not title:
        title = site_name or host or "未命名文章"

    best = _score_candidates(root)
    if best is None:
        best = _first(root, {"article"})
    if best is None:
        best = _first(root, {"main"})
    if best is None:
        body = _first(root, {"body"})
        best = body or root
    if _measure(best, {})[0] < 60:
        raise ReadableError("这个页面没有可读的正文", "no_content")

    out: list[str] = []
    _serialize(
        best,
        out,
        base_url=url,
        title_key=_normalized(title),
        title_removed=[False],
    )
    raw_content = "".join(out)
    content = sanitize_fragment(
        raw_content, base_url=url, limit=MAX_CONTENT_CHARS
    )
    plain = html_to_text(content, limit=MAX_CONTENT_CHARS)
    if len(plain) < 50:
        raise ReadableError("这个页面没有可读的正文", "no_content")

    description = (
        meta.get("og:description", "")
        or meta.get("description", "")
        or meta.get("twitter:description", "")
        or _as_text(article_ld.get("description"))
    )
    excerpt = " ".join(html.unescape(description).split())
    if not excerpt:
        first_paragraph = next(
            (
                _node_text(item)
                for item in _walk(best)
                if item.tag == "p" and len(_node_text(item)) > 40
            ),
            "",
        )
        excerpt = first_paragraph
    if len(excerpt) > 240:
        excerpt = excerpt[:239].rstrip() + "…"

    lead_image = ""
    for key in ("og:image", "og:image:url", "twitter:image"):
        lead_image = _resolve(meta.get(key, ""), url)
        if lead_image:
            break
    if not lead_image:
        lead_image = _resolve(_as_image(article_ld.get("image")), url)
    if not lead_image:
        for item in _walk(best):
            if item.tag == "img" and not _is_tracker_image(item):
                lead_image, _ = _image_source(item, url)
                if lead_image:
                    break

    word_count, reading_minutes = text_stats(plain)
    return ReadableArticle(
        title=title,
        byline=_extract_byline(meta, article_ld, root),
        site_name=site_name,
        excerpt=excerpt,
        content_html=content,
        lead_image=lead_image,
        published_hint=_extract_published(meta, article_ld, root),
        word_count=word_count,
        reading_minutes=reading_minutes,
    )
