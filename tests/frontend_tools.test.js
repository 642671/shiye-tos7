"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const Tools = require("../frontend/tools.js");

function localDate(year, month, day, hour, minute) {
  return new Date(year, month - 1, day, hour || 0, minute || 0, 0, 0);
}

test("formatRelativeTime handles recent and older timestamps", () => {
  const now = localDate(2024, 6, 15, 12, 0);
  assert.equal(Tools.formatRelativeTime(null, now), "");
  assert.equal(
    Tools.formatRelativeTime(new Date(now.getTime() + 30000), now),
    "刚刚"
  );
  assert.equal(
    Tools.formatRelativeTime(new Date(now.getTime() - 30000), now),
    "刚刚"
  );
  assert.equal(
    Tools.formatRelativeTime(new Date(now.getTime() - 5 * 60000), now),
    "5 分钟前"
  );
  assert.equal(
    Tools.formatRelativeTime(localDate(2024, 6, 15, 9, 0), now),
    "3 小时前"
  );
  assert.equal(
    Tools.formatRelativeTime(localDate(2024, 6, 14, 20, 0), now),
    "昨天"
  );
  assert.equal(
    Tools.formatRelativeTime(localDate(2024, 6, 12, 20, 0), now),
    "3 天前"
  );
  assert.equal(
    Tools.formatRelativeTime(localDate(2024, 3, 2, 8, 0), now),
    "3月2日"
  );
  assert.equal(
    Tools.formatRelativeTime(localDate(2023, 3, 2, 8, 0), now),
    "2023年3月2日"
  );
  assert.equal(Tools.formatRelativeTime("not a date", now), "");
});

test("date formatting pads values", () => {
  const date = localDate(2024, 1, 2, 3, 4);
  assert.equal(Tools.formatDateTime(date), "2024-01-02 03:04");
  assert.equal(Tools.formatFullDate(date), "2024年1月2日 03:04");
  assert.equal(Tools.formatDateTime(""), "");
  assert.equal(Tools.formatFullDate(null), "");
});

test("truncate respects limits", () => {
  assert.equal(Tools.truncate("  hello  ", 10), "hello");
  assert.equal(Tools.truncate("abcdef", 4), "abc…");
  assert.equal(Tools.truncate("abcdef", 6), "abcdef");
  assert.equal(Tools.truncate("abcdef", 0), "abcdef");
  assert.equal(Tools.truncate(null, 5), "");
  assert.equal(Tools.truncate("a b c", 4), "a b…");
});

test("clampNumber rounds and falls back", () => {
  assert.equal(Tools.clampNumber("12.6", 1, 20, 5), 13);
  assert.equal(Tools.clampNumber(0, 1, 20, 5), 1);
  assert.equal(Tools.clampNumber(99, 1, 20, 5), 20);
  assert.equal(Tools.clampNumber("abc", 1, 20, 5), 5);
  assert.equal(Tools.clampNumber(undefined, 1, 20, 7), 7);
});

test("buildQuery skips empty values and encodes", () => {
  assert.equal(Tools.buildQuery(null), "");
  assert.equal(Tools.buildQuery("x"), "");
  assert.equal(
    Tools.buildQuery({
      feed_id: 3,
      unread: true,
      starred: false,
      q: "a b&c",
      empty: "",
      missing: null,
      zero: 0,
    }),
    "feed_id=3&unread=1&starred=0&q=a%20b%26c&zero=0"
  );
});

test("nextArticleId moves within the list", () => {
  const items = [{ id: 1 }, { id: 2 }, { id: 3 }];
  assert.equal(Tools.nextArticleId(items, 1, 1), 2);
  assert.equal(Tools.nextArticleId(items, 2, -1), 1);
  assert.equal(Tools.nextArticleId(items, 1, -1), 1);
  assert.equal(Tools.nextArticleId(items, 3, 1), 3);
  assert.equal(Tools.nextArticleId(items, 99, 1), 1);
  assert.equal(Tools.nextArticleId(items, 99, -1), 3);
  assert.equal(Tools.nextArticleId([], 1, 1), null);
  assert.equal(Tools.nextArticleId(null, 1, 1), null);
});

test("scopeLabel resolves scopes", () => {
  const feeds = [{ id: 7, title: "Feed Seven" }];
  const categories = [{ id: 3, name: "Tech" }];
  assert.equal(Tools.scopeLabel({ type: "all" }, feeds, categories), "全部文章");
  assert.equal(Tools.scopeLabel({ type: "unread" }, feeds, categories), "未读");
  assert.equal(Tools.scopeLabel({ type: "saved" }, feeds, categories), "稍后读");
  assert.equal(Tools.scopeLabel({ type: "starred" }, feeds, categories), "星标");
  assert.equal(
    Tools.scopeLabel({ type: "feed", id: "7" }, feeds, categories),
    "Feed Seven"
  );
  assert.equal(
    Tools.scopeLabel({ type: "feed", id: 99 }, feeds, categories),
    "订阅"
  );
  assert.equal(
    Tools.scopeLabel({ type: "category", id: 3 }, feeds, categories),
    "Tech"
  );
  assert.equal(
    Tools.scopeLabel({ type: "category", id: 99 }, feeds, categories),
    "分类"
  );
  assert.equal(Tools.scopeLabel(null, feeds, categories), "全部文章");
});

test("formatBytes scales units", () => {
  assert.equal(Tools.formatBytes(0), "0 B");
  assert.equal(Tools.formatBytes(-1), "0 B");
  assert.equal(Tools.formatBytes(512), "512 B");
  assert.equal(Tools.formatBytes(2048), "2 KiB");
  assert.equal(Tools.formatBytes(5 * 1024 * 1024), "5 MiB");
  assert.equal(Tools.formatBytes(2.5 * 1024 * 1024 * 1024), "2.5 GiB");
  assert.equal(Tools.formatBytes("nope"), "0 B");
});

test("readingMeta describes length and minutes", () => {
  assert.equal(Tools.formatWordCount(0), "");
  assert.equal(Tools.formatWordCount(980), "980 字");
  assert.equal(Tools.formatWordCount(12800), "1.3 万字");
  assert.equal(Tools.readingMeta(4, 1800), "约 4 分钟 · 1800 字");
  assert.equal(Tools.readingMeta(2, 0), "约 2 分钟");
  assert.equal(Tools.readingMeta(0, 640), "640 字");
  assert.equal(Tools.readingMeta(0, 0), "");
  assert.equal(Tools.readingMeta("x", "y"), "");
});

test("readingProgress tracks the scrollable part of the article", () => {
  assert.equal(Tools.readingProgress(0, 1000, 500), 0);
  assert.equal(Tools.readingProgress(250, 1000, 500), 50);
  assert.equal(Tools.readingProgress(500, 1000, 500), 100);
  assert.equal(Tools.readingProgress(900, 1000, 500), 100);
  assert.equal(Tools.readingProgress(0, 400, 500), 0);
  assert.equal(Tools.readingProgress(-200, 1000, 500), 0);
  assert.equal(Tools.readingProgress(0, 0, 500), 0);
  assert.equal(Tools.readingProgress("x", 1000, 500), 0);
});

test("adjustFontSize clamps and rounds", () => {
  assert.equal(Tools.adjustFontSize(17, 1, 15, 24), 18);
  assert.equal(Tools.adjustFontSize(24, 1, 15, 24), 24);
  assert.equal(Tools.adjustFontSize(15, -1, 15, 24), 15);
  assert.equal(Tools.adjustFontSize("bad", 2, 15, 24), 19);
  assert.equal(Tools.adjustFontSize(18.4, "x", 15, 24), 18);
});

test("healthCandidates finds the proxy route and its API base", () => {
  function urls(pathname) {
    return Tools.healthCandidates(pathname).map((item) => item.health);
  }

  const proxied = Tools.healthCandidates("/v2/proxy/shiye/index.html");
  assert.equal(proxied[0].health, "/v2/proxy/shiye/api/health");
  assert.equal(proxied[0].apiBase, "/v2/proxy/shiye/api/");
  assert.equal(proxied[1].health, "/v2/proxy/shiye/health");
  assert.equal(proxied[1].apiBase, "/v2/proxy/shiye/api/");

  const direct = Tools.healthCandidates("/shiye/");
  assert.equal(direct[0].health, "/shiye/api/health");
  assert.equal(direct[0].apiBase, "/shiye/api/");

  const custom = Tools.healthCandidates("/custom/app/index.html");
  assert.equal(custom[0].health, "/v2/proxy/shiye/api/health");
  assert.ok(custom.some((item) => item.health === "/custom/app/api/health"));
  assert.ok(custom.some((item) => item.health === "/api/health"));
  assert.ok(custom.some((item) => item.health === "/health"));

  const all = urls("/v2/proxy/shiye/");
  assert.equal(new Set(all).size, all.length);
  assert.ok(all.includes("/api/health"));
  assert.ok(all.includes("/v2/proxy/shiye/health"));

  assert.deepEqual(Tools.healthCandidates("")[0], {
    health: "/v2/proxy/shiye/api/health",
    apiBase: "/v2/proxy/shiye/api/",
  });
});

test("debounce coalesces calls", async () => {
  let calls = [];
  const debounced = Tools.debounce(function (value) {
    calls.push(value);
  }, 20);
  debounced("a");
  debounced("b");
  debounced("c");
  assert.deepEqual(calls, []);
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.deepEqual(calls, ["c"]);
});
