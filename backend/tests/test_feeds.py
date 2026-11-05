"""Feed parsing, decoding, discovery and OPML tests."""

from __future__ import annotations

import gzip
import sys
import unittest
import zlib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from yuii_reader.feeds import (
    FeedError,
    _decompress,
    build_opml,
    decode_body,
    discover_feed_links,
    normalize_feed_url,
    parse_date,
    parse_feed,
    parse_opml,
)

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Example &amp; Friends</title>
    <link>https://example.com/</link>
    <description>Channel description</description>
    <image><url>https://example.com/logo.png</url></image>
    <item>
      <title>First post</title>
      <link>https://example.com/first</link>
      <author>alice@example.com (Alice)</author>
      <pubDate>Wed, 02 Oct 2002 13:00:00 GMT</pubDate>
      <description>Short &lt;b&gt;summary&lt;/b&gt;.</description>
      <content:encoded><![CDATA[<p>Full <strong>article</strong>.</p>]]></content:encoded>
      <guid isPermaLink="false">post-1</guid>
    </item>
    <item>
      <title>Second post</title>
      <link>/second</link>
      <description>Another summary.</description>
      <guid>https://example.com/second</guid>
    </item>
  </channel>
</rss>
"""

RDF_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel rdf:about="https://example.org/">
    <title>RDF feed</title>
    <link>https://example.org/</link>
    <description>An RDF channel</description>
  </channel>
  <item rdf:about="https://example.org/a">
    <title>RDF item</title>
    <link>https://example.org/a</link>
    <dc:creator>Bob</dc:creator>
    <dc:date>2024-02-03T04:05:06Z</dc:date>
    <description>RDF body</description>
  </item>
</rdf:RDF>
"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Example</title>
  <subtitle>A small Atom feed</subtitle>
  <link href="https://atom.example/" rel="alternate"/>
  <icon>https://atom.example/icon.png</icon>
  <entry>
    <id>tag:atom.example,2024:1</id>
    <title>Atom entry</title>
    <link href="/entry/1" rel="alternate"/>
    <published>2024-03-04T05:06:07+08:00</published>
    <author><name>Carol</name></author>
    <content type="xhtml">
      <div xmlns="http://www.w3.org/1999/xhtml"><p>Hello <b>Atom</b>.</p></div>
    </content>
    <summary type="text">Plain summary</summary>
  </entry>
</feed>
"""


class NormalizeUrlTests(unittest.TestCase):
    def test_adds_https_and_default_path(self) -> None:
        self.assertEqual(normalize_feed_url("example.com"), "https://example.com/")
        self.assertEqual(
            normalize_feed_url("example.com/feed.xml?x=1#frag"),
            "https://example.com/feed.xml?x=1",
        )

    def test_keeps_http_and_https(self) -> None:
        self.assertEqual(
            normalize_feed_url("http://Example.com/rss"), "http://Example.com/rss"
        )
        self.assertEqual(
            normalize_feed_url("https://example.com/rss"), "https://example.com/rss"
        )

    def test_rejects_invalid_urls(self) -> None:
        for value in ("", "   ", "ftp://example.com/feed", "https:///feed", None):
            with self.assertRaises(FeedError):
                normalize_feed_url(value)

    def test_rejects_credentials(self) -> None:
        with self.assertRaises(FeedError):
            normalize_feed_url("https://user:pass@example.com/feed")


class DecodeTests(unittest.TestCase):
    def test_utf8_bom(self) -> None:
        body = b"\xef\xbb\xbf<?xml version='1.0'?><rss/>"
        self.assertIn("<rss/>", decode_body(body))

    def test_declared_gbk_is_decoded_as_gb18030(self) -> None:
        text = '<?xml version="1.0" encoding="gb2312"?><rss><channel><title>中文标题</title></channel></rss>'
        body = text.encode("gb18030")
        decoded = decode_body(body)
        self.assertIn("中文标题", decoded)

    def test_invalid_bytes_fall_back(self) -> None:
        self.assertTrue(decode_body(b"<rss>\xff\xfe</rss>"))

    def test_gzip_and_deflate(self) -> None:
        payload = b"<rss>hello</rss>"
        self.assertEqual(_decompress(gzip.compress(payload), "gzip"), payload)
        self.assertEqual(_decompress(zlib.compress(payload), "deflate"), payload)
        self.assertEqual(
            _decompress(zlib.compress(payload)[2:-4], "deflate"), payload
        )

    def test_bad_gzip_raises(self) -> None:
        with self.assertRaises(FeedError) as caught:
            _decompress(b"not gzip", "gzip")
        self.assertEqual(caught.exception.code, "bad_encoding")


class DateTests(unittest.TestCase):
    def test_rfc822(self) -> None:
        self.assertEqual(
            parse_date("Wed, 02 Oct 2002 13:00:00 GMT"), "2002-10-02T13:00:00Z"
        )

    def test_iso_with_offset(self) -> None:
        self.assertEqual(
            parse_date("2024-01-02T03:04:05+08:00"), "2024-01-01T19:04:05Z"
        )
        self.assertEqual(
            parse_date("2024-01-02T03:04:05+0800"), "2024-01-01T19:04:05Z"
        )

    def test_date_only_and_invalid(self) -> None:
        self.assertEqual(parse_date("2024-01-02"), "2024-01-02T00:00:00Z")
        self.assertIsNone(parse_date("not a date"))
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date(None))
        self.assertIsNone(parse_date("1960-01-01"))


class ParseFeedTests(unittest.TestCase):
    def test_rss(self) -> None:
        parsed = parse_feed(RSS_SAMPLE, "https://example.com/feed.xml")
        self.assertEqual(parsed.kind, "rss")
        self.assertEqual(parsed.title, "Example & Friends")
        self.assertEqual(parsed.site_url, "https://example.com/")
        self.assertEqual(parsed.description, "Channel description")
        self.assertEqual(parsed.icon_url, "https://example.com/logo.png")
        self.assertEqual(len(parsed.entries), 2)
        first = parsed.entries[0]
        self.assertEqual(first.guid, "post-1")
        self.assertEqual(first.title, "First post")
        self.assertEqual(first.url, "https://example.com/first")
        self.assertEqual(first.author, "alice@example.com (Alice)")
        self.assertEqual(first.published_at, "2002-10-02T13:00:00Z")
        self.assertEqual(first.summary_html, "Short <b>summary</b>.")
        self.assertEqual(first.content_html, "<p>Full <strong>article</strong>.</p>")
        second = parsed.entries[1]
        self.assertEqual(second.url, "https://example.com/second")
        self.assertEqual(second.content_html, "Another summary.")

    def test_rss_description_is_not_overwritten_by_items(self) -> None:
        parsed = parse_feed(RSS_SAMPLE)
        self.assertIsInstance(parsed.description, str)
        self.assertEqual(parsed.description, "Channel description")

    def test_rdf(self) -> None:
        parsed = parse_feed(RDF_SAMPLE, "https://example.org/feed.rdf")
        self.assertEqual(parsed.kind, "rdf")
        self.assertEqual(parsed.title, "RDF feed")
        self.assertEqual(parsed.description, "An RDF channel")
        self.assertEqual(len(parsed.entries), 1)
        entry = parsed.entries[0]
        self.assertEqual(entry.title, "RDF item")
        self.assertEqual(entry.author, "Bob")
        self.assertEqual(entry.published_at, "2024-02-03T04:05:06Z")
        self.assertEqual(entry.content_html, "RDF body")

    def test_atom(self) -> None:
        parsed = parse_feed(ATOM_SAMPLE, "https://atom.example/feed.xml")
        self.assertEqual(parsed.kind, "atom")
        self.assertEqual(parsed.title, "Atom Example")
        self.assertEqual(parsed.site_url, "https://atom.example/")
        self.assertEqual(parsed.description, "A small Atom feed")
        entry = parsed.entries[0]
        self.assertEqual(entry.guid, "tag:atom.example,2024:1")
        self.assertEqual(entry.url, "https://atom.example/entry/1")
        self.assertEqual(entry.author, "Carol")
        self.assertEqual(entry.published_at, "2024-03-03T21:06:07Z")
        self.assertIn("<b>Atom</b>", entry.content_html)
        self.assertIn("Plain summary", entry.summary_html)

    def test_deduplicates_guids(self) -> None:
        duplicate = RSS_SAMPLE.replace("post-1", "same-guid").replace(
            "https://example.com/second", "same-guid"
        )
        duplicate = duplicate.replace(
            "https://example.com/first", "same-guid"
        )
        parsed = parse_feed(duplicate)
        self.assertEqual(len(parsed.entries), 1)

    def test_invalid_xml_and_format(self) -> None:
        for text in ("<rss><channel>", "<html><body>hi</body></html>", ""):
            with self.assertRaises(FeedError):
                parse_feed(text)

    def test_rejects_entity_declarations(self) -> None:
        payload = (
            '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "boom">]>'
            "<rss><channel><title>&x;</title></channel></rss>"
        )
        with self.assertRaises(FeedError) as caught:
            parse_feed(payload)
        self.assertEqual(caught.exception.code, "unsafe_feed")

    def test_missing_channel(self) -> None:
        with self.assertRaises(FeedError):
            parse_feed("<rss version='2.0'></rss>")


class DiscoveryTests(unittest.TestCase):
    def test_finds_alternate_feed_links(self) -> None:
        page = """
        <html><head>
          <link rel="stylesheet" href="/style.css">
          <link rel="alternate" type="application/rss+xml" href="/feed.xml">
          <link rel="alternate" type="application/atom+xml; charset=utf-8" href="atom.xml">
          <link rel="alternate" type="application/rss+xml" href="/feed.xml">
        </head></html>
        """
        links = discover_feed_links(page, "https://example.com/blog/post")
        self.assertEqual(
            links,
            ["https://example.com/feed.xml", "https://example.com/blog/atom.xml"],
        )

    def test_ignores_irrelevant_links(self) -> None:
        page = '<link rel="alternate" type="text/html" href="/other">'
        self.assertEqual(discover_feed_links(page, "https://example.com/"), [])


class OpmlTests(unittest.TestCase):
    OPML = """<?xml version="1.0" encoding="UTF-8"?>
    <opml version="2.0">
      <head><title>Subscriptions</title></head>
      <body>
        <outline text="Tech">
          <outline text="Example" type="rss" xmlUrl="https://example.com/feed.xml"
                   htmlUrl="https://example.com/"/>
          <outline text="Duplicate" type="rss" xmlUrl="https://example.com/feed.xml"/>
        </outline>
        <outline text="Loose" type="rss" xmlUrl="example.org/rss"/>
        <outline text="Bad" type="rss" xmlUrl="ftp://example.net/feed"/>
      </body>
    </opml>
    """

    def test_parse_categories_and_deduplicate(self) -> None:
        entries = parse_opml(self.OPML)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].title, "Example")
        self.assertEqual(entries[0].xml_url, "https://example.com/feed.xml")
        self.assertEqual(entries[0].category, "Tech")
        self.assertEqual(entries[0].html_url, "https://example.com/")
        self.assertEqual(entries[1].xml_url, "https://example.org/rss")

    def test_build_round_trip(self) -> None:
        groups = [
            (
                "Tech",
                [
                    {
                        "title": 'A & B "quoted"',
                        "url": "https://example.com/feed?a=1&b=2",
                        "site_url": "https://example.com/",
                    }
                ],
            ),
            (
                "",
                [
                    {
                        "title": "Loose",
                        "url": "https://example.org/rss",
                        "site_url": "",
                    }
                ],
            ),
        ]
        document = build_opml(groups)
        entries = parse_opml(document)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].category, "Tech")
        self.assertEqual(entries[0].title, 'A & B "quoted"')
        self.assertEqual(entries[0].xml_url, "https://example.com/feed?a=1&b=2")
        self.assertEqual(entries[1].title, "Loose")
        self.assertEqual(entries[1].category, "")

    def test_rejects_bad_opml(self) -> None:
        for text in ("<opml><head/></opml>", "<not-opml/>", "<opml>"):
            with self.assertRaises(FeedError):
                parse_opml(text)


if __name__ == "__main__":
    unittest.main()
