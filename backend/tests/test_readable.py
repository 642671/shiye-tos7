"""Tests for the standard-library readability extraction."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from shiye.readable import ReadableError, extract_article, text_stats


LONG_ZH = (
    "这是第一段正文，用来验证正文识别是否稳定。阅读器需要把导航、侧栏和页脚都去掉，"
    "只保留真正值得阅读的内容。文章里还应该有足够多的文字，才能通过可读性阈值。"
)
LONG_EN = (
    "This paragraph contains enough words to be treated as article body text. "
    "A good reader removes navigation, sidebars, sharing widgets and footers, "
    "then keeps the paragraphs that a person actually wants to read."
)


class ReadableTests(unittest.TestCase):
    def test_extracts_article_metadata_and_removes_noise(self) -> None:
        page = f"""
        <html><head>
          <title>测试文章 - 示例站</title>
          <meta property="og:site_name" content="示例站">
          <meta name="author" content="张三">
          <meta property="article:published_time" content="2026-01-02T03:04:05Z">
          <meta name="description" content="一段用于列表展示的摘要。">
        </head><body>
          <nav><a href="/">首页</a></nav>
          <div class="sidebar"><p>侧栏推荐文字，不应该进入正文。</p></div>
          <article class="post-content">
            <h1>测试文章</h1>
            <p>{LONG_ZH}</p>
            <p>{LONG_EN}</p>
            <img src="/images/photo.jpg" alt="一张图片">
            <script>alert("x")</script>
          </article>
          <footer>页脚内容</footer>
        </body></html>
        """
        article = extract_article(page, "https://example.com/2026/01/post")
        self.assertEqual(article.title, "测试文章")
        self.assertEqual(article.site_name, "示例站")
        self.assertEqual(article.byline, "张三")
        self.assertEqual(article.published_hint, "2026-01-02T03:04:05Z")
        self.assertEqual(article.excerpt, "一段用于列表展示的摘要。")
        self.assertNotIn("侧栏推荐", article.content_html)
        self.assertNotIn("页脚内容", article.content_html)
        self.assertNotIn("<script", article.content_html.lower())
        self.assertNotIn("测试文章", article.content_html)
        self.assertIn("api/media?url=", article.content_html)
        self.assertIn("https%3A%2F%2Fexample.com%2Fimages%2Fphoto.jpg", article.content_html)
        self.assertGreater(article.word_count, 60)
        self.assertGreaterEqual(article.reading_minutes, 1)

    def test_json_ld_supplies_title_author_and_cover(self) -> None:
        page = f"""
        <html><head>
          <script type="application/ld+json">
            {{"@context":"https://schema.org","@type":"NewsArticle",
              "headline":"来自 JSON-LD 的标题",
              "author":{{"@type":"Person","name":"李四"}},
              "publisher":{{"@type":"Organization","name":"结构数据站"}},
              "datePublished":"2026-02-03T04:05:06+08:00",
              "image":{{"url":"https://cdn.example.com/cover.jpg"}}}}
          </script>
        </head><body>
          <main><p>{LONG_ZH}</p><p>{LONG_EN}</p></main>
        </body></html>
        """
        article = extract_article(page, "https://example.com/json")
        self.assertEqual(article.title, "来自 JSON-LD 的标题")
        self.assertEqual(article.byline, "李四")
        self.assertEqual(article.site_name, "结构数据站")
        self.assertEqual(article.lead_image, "https://cdn.example.com/cover.jpg")
        self.assertIn("2026-02-03", article.published_hint)

    def test_lazy_image_and_relative_link_are_resolved(self) -> None:
        page = f"""
        <html><body><article>
          <p>{LONG_ZH}</p>
          <p><a href="/next/page">继续阅读</a></p>
          <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="
               data-src="/images/lazy.jpg" alt="懒加载图片">
        </article></body></html>
        """
        article = extract_article(page, "https://example.com/a/b")
        self.assertIn("https://example.com/next/page", article.content_html)
        self.assertIn("https%3A%2F%2Fexample.com%2Fimages%2Flazy.jpg", article.content_html)
        self.assertNotIn("data:image/gif", article.content_html)

    def test_short_page_raises_readable_error(self) -> None:
        with self.assertRaises(ReadableError) as context:
            extract_article("<html><body><p>too short</p></body></html>", "https://x.test")
        self.assertEqual(context.exception.code, "no_content")

    def test_tracking_pixel_is_removed(self) -> None:
        page = f"""
        <html><body><article>
          <p>{LONG_ZH}</p>
          <img src="/pixel.gif" width="1" height="1">
        </article></body></html>
        """
        article = extract_article(page, "https://example.com/p")
        self.assertNotIn("pixel.gif", article.content_html)

    def test_text_stats_mixes_cjk_and_latin(self) -> None:
        words, minutes = text_stats("阅读 120 篇文章 with some English words")
        self.assertGreaterEqual(words, 10)
        self.assertEqual(minutes, 1)
        self.assertEqual(text_stats(""), (0, 0))


if __name__ == "__main__":
    unittest.main()
