"""HTML sanitizing and text extraction tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from shiye.sanitize import (
    html_to_text,
    proxy_image_url,
    sanitize_fragment,
    strip_tracking_params,
    summarize_html,
)


class TrackingTests(unittest.TestCase):
    def test_strips_campaign_parameters_only(self) -> None:
        url = (
            "https://example.com/a?utm_source=news&id=42&fbclid=abc"
            "&utm_medium=email&page=2#section"
        )
        self.assertEqual(
            strip_tracking_params(url),
            "https://example.com/a?id=42&page=2#section",
        )

    def test_leaves_clean_url_untouched(self) -> None:
        url = "https://example.com/a?id=42"
        self.assertEqual(strip_tracking_params(url), url)
        self.assertEqual(strip_tracking_params("not a url"), "not a url")

    def test_proxy_image_url_is_encoded(self) -> None:
        proxied = proxy_image_url("https://example.com/i.png?utm_source=x&a=1")
        self.assertTrue(proxied.startswith("api/media?url="))
        self.assertNotIn("utm_source", proxied)
        self.assertIn("%3A%2F%2F", proxied)


class SanitizeTests(unittest.TestCase):
    def test_removes_scripts_and_event_handlers(self) -> None:
        html = (
            '<p onclick="steal()">Hello <script>alert(1)</script>world</p>'
            '<iframe src="https://evil.example/"></iframe>'
        )
        output = sanitize_fragment(html)
        self.assertEqual(output, "<p>Hello world</p>")

    def test_removes_javascript_links(self) -> None:
        output = sanitize_fragment(
            '<a href="javascript:alert(1)">x</a>'
            '<a href="vbscript:msgbox(1)">y</a>'
        )
        # The unsafe href is dropped with its anchor, but visible text stays.
        self.assertEqual(output, "xy")
        self.assertNotIn("<a", output)
        self.assertNotIn("javascript:", output)
        self.assertNotIn("vbscript:", output)

    def test_resolves_links_and_strips_tracking(self) -> None:
        output = sanitize_fragment(
            '<a href="/post?utm_source=rss&amp;id=9">Read</a>',
            base_url="https://example.com/feed/",
        )
        self.assertIn('href="https://example.com/post?id=9"', output)
        self.assertIn('target="_blank"', output)
        self.assertIn('rel="noopener noreferrer nofollow"', output)

    def test_anchor_without_href_is_removed(self) -> None:
        self.assertEqual(sanitize_fragment("<a name='x'>text</a>"), "text")

    def test_images_are_proxied_and_hardened(self) -> None:
        output = sanitize_fragment(
            '<img src="/img/a.png" alt="A" width="640" height="480">',
            base_url="https://example.com/post",
        )
        self.assertIn("api/media?url=https%3A%2F%2Fexample.com%2Fimg%2Fa.png", output)
        self.assertIn('alt="A"', output)
        self.assertIn('width="640"', output)
        self.assertIn('loading="lazy"', output)
        self.assertIn('referrerpolicy="no-referrer"', output)

    def test_srcset_is_proxied(self) -> None:
        output = sanitize_fragment(
            '<img src="/a.png" srcset="/a-2x.png 2x, /a-3x.png 3x">',
            base_url="https://example.com/",
        )
        self.assertIn("api/media?url=", output)
        self.assertIn(" 2x", output)
        self.assertIn(" 3x", output)

    def test_tracking_pixels_are_removed(self) -> None:
        cases = [
            '<img src="https://example.com/a.png" width="1" height="1">',
            '<img src="https://example.com/pixel.gif">',
            '<img src="https://example.com/open.aspx?id=1">',
        ]
        for html in cases:
            self.assertEqual(sanitize_fragment(html), "", html)

    def test_data_images_are_preserved(self) -> None:
        data = "data:image/png;base64,iVBORw0KGgo="
        output = sanitize_fragment(f'<img src="{data}" alt="inline">')
        self.assertIn(f'src="{data}"', output)
        self.assertNotIn("api/media", output)

    def test_attributes_are_always_quoted(self) -> None:
        output = sanitize_fragment("<p title=x lang=en>text</p>")
        self.assertEqual(output, '<p title="x" lang="en">text</p>')

    def test_code_language_class_is_kept(self) -> None:
        output = sanitize_fragment(
            '<pre class="highlight language-python extra"><code>print(1)</code></pre>'
        )
        self.assertIn('class="language-python"', output)
        self.assertNotIn("highlight", output)

    def test_dangerous_tags_and_comments_are_dropped(self) -> None:
        output = sanitize_fragment(
            "<style>body{display:none}</style><!-- comment -->"
            "<form><input value='x'></form><p>safe</p>"
        )
        self.assertEqual(output, "<p>safe</p>")

    def test_svg_is_not_allowed(self) -> None:
        output = sanitize_fragment(
            '<svg><script>alert(1)</script><text>hi</text></svg><p>ok</p>'
        )
        self.assertEqual(output, "<p>ok</p>")

    def test_details_is_open_and_void_tags_self_close(self) -> None:
        output = sanitize_fragment("<details><summary>S</summary>Body<br>More</details>")
        self.assertEqual(
            output,
            '<details open><summary>S</summary>Body<br>More</details>',
        )

    def test_unbalanced_markup_is_closed(self) -> None:
        self.assertEqual(sanitize_fragment("<p><b>text"), "<p><b>text</b></p>")

    def test_limit_truncates_output(self) -> None:
        output = sanitize_fragment("<p>" + "a" * 100 + "</p>", limit=20)
        self.assertLessEqual(len(output), 20)
        self.assertTrue(output.endswith("…"))

    def test_empty_input(self) -> None:
        self.assertEqual(sanitize_fragment(""), "")
        self.assertEqual(sanitize_fragment(None), "")


class TextTests(unittest.TestCase):
    def test_html_to_text_drops_script_and_styles(self) -> None:
        text = html_to_text(
            "<p>Hello <b>world</b>.</p><script>alert(1)</script>"
            "<style>.x{}</style><p>Second &amp; last</p>"
        )
        self.assertEqual(text, "Hello world. Second & last")

    def test_lists_and_alt_text(self) -> None:
        text = html_to_text("<ul><li>One</li><li>Two</li></ul><img alt='photo'>")
        self.assertIn("One", text)
        self.assertIn("Two", text)
        self.assertIn("photo", text)

    def test_limit_and_whitespace(self) -> None:
        self.assertEqual(html_to_text("  a   b  ", limit=10), "a b")
        self.assertEqual(html_to_text("a" * 30, limit=10), "a" * 9 + "…")

    def test_summary_is_plain_text(self) -> None:
        summary = summarize_html(
            '<p>Read <a href="/x?utm_source=rss">more</a></p>'
            "<script>bad()</script>",
            base_url="https://example.com/",
        )
        self.assertEqual(summary, "Read more")


if __name__ == "__main__":
    unittest.main()
