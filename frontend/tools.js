(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.ShiyeTools = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function toDate(value) {
    if (value instanceof Date) {
      return Number.isNaN(value.getTime()) ? null : value;
    }
    if (value === null || value === undefined || value === "") {
      return null;
    }
    var parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function pad(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function startOfDay(date) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate());
  }

  function formatRelativeTime(value, nowValue) {
    var date = toDate(value);
    if (!date) {
      return "";
    }
    var now = toDate(nowValue) || new Date();
    var diffMs = now.getTime() - date.getTime();
    var diffSeconds = Math.round(diffMs / 1000);
    if (diffSeconds < 0) {
      if (diffSeconds > -60) {
        return "刚刚";
      }
      return formatFullDate(date);
    }
    if (diffSeconds < 60) {
      return "刚刚";
    }
    var minutes = Math.floor(diffSeconds / 60);
    if (minutes < 60) {
      return minutes + " 分钟前";
    }
    var dayDiff = Math.round(
      (startOfDay(now).getTime() - startOfDay(date).getTime()) / 86400000
    );
    if (dayDiff === 0) {
      return Math.floor(minutes / 60) + " 小时前";
    }
    if (dayDiff === 1) {
      return "昨天";
    }
    if (dayDiff > 1 && dayDiff < 7) {
      return dayDiff + " 天前";
    }
    if (date.getFullYear() === now.getFullYear()) {
      return date.getMonth() + 1 + "月" + date.getDate() + "日";
    }
    return date.getFullYear() + "年" + (date.getMonth() + 1) + "月" + date.getDate() + "日";
  }

  function formatFullDate(value) {
    var date = toDate(value);
    if (!date) {
      return "";
    }
    return (
      date.getFullYear() +
      "年" +
      (date.getMonth() + 1) +
      "月" +
      date.getDate() +
      "日 " +
      pad(date.getHours()) +
      ":" +
      pad(date.getMinutes())
    );
  }

  function formatDateTime(value) {
    var date = toDate(value);
    if (!date) {
      return "";
    }
    return (
      date.getFullYear() +
      "-" +
      pad(date.getMonth() + 1) +
      "-" +
      pad(date.getDate()) +
      " " +
      pad(date.getHours()) +
      ":" +
      pad(date.getMinutes())
    );
  }

  function truncate(text, max) {
    var value = String(text === null || text === undefined ? "" : text).trim();
    var limit = Number(max);
    if (!Number.isFinite(limit) || limit <= 0 || value.length <= limit) {
      return value;
    }
    return value.slice(0, Math.max(1, Math.floor(limit) - 1)).replace(/\s+$/, "") + "…";
  }

  function clampNumber(value, minimum, maximum, fallback) {
    var number = Number(value);
    if (!Number.isFinite(number)) {
      return fallback;
    }
    return Math.min(maximum, Math.max(minimum, Math.round(number)));
  }

  function buildQuery(params) {
    if (!params || typeof params !== "object") {
      return "";
    }
    var parts = [];
    Object.keys(params).forEach(function (key) {
      var value = params[key];
      if (value === null || value === undefined || value === "") {
        return;
      }
      if (typeof value === "boolean") {
        value = value ? "1" : "0";
      }
      parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(String(value)));
    });
    return parts.join("&");
  }

  function nextArticleId(items, currentId, direction) {
    if (!Array.isArray(items) || items.length === 0) {
      return null;
    }
    var current = Number(currentId);
    var index = -1;
    for (var i = 0; i < items.length; i += 1) {
      if (Number(items[i].id) === current) {
        index = i;
        break;
      }
    }
    var step = direction >= 0 ? 1 : -1;
    if (index === -1) {
      return items[step === 1 ? 0 : items.length - 1].id;
    }
    var target = Math.min(items.length - 1, Math.max(0, index + step));
    return items[target].id;
  }

  function scopeLabel(filter, feeds, categories) {
    var scope = filter || { type: "all" };
    if (scope.type === "unread") {
      return "未读";
    }
    if (scope.type === "saved") {
      return "稍后读";
    }
    if (scope.type === "starred") {
      return "星标";
    }
    if (scope.type === "feed") {
      var feed = (feeds || []).find(function (item) {
        return Number(item.id) === Number(scope.id);
      });
      return feed ? feed.title : "订阅";
    }
    if (scope.type === "category") {
      var category = (categories || []).find(function (item) {
        return Number(item.id) === Number(scope.id);
      });
      return category ? category.name : "分类";
    }
    return "全部文章";
  }

  function formatBytes(value) {
    var bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return "0 B";
    }
    if (bytes < 1024) {
      return Math.round(bytes) + " B";
    }
    if (bytes < 1024 * 1024) {
      return (bytes / 1024).toFixed(1).replace(/\.0$/, "") + " KiB";
    }
    if (bytes < 1024 * 1024 * 1024) {
      return (bytes / (1024 * 1024)).toFixed(1).replace(/\.0$/, "") + " MiB";
    }
    return (bytes / (1024 * 1024 * 1024)).toFixed(1).replace(/\.0$/, "") + " GiB";
  }

  function formatWordCount(value) {
    var words = Number(value);
    if (!Number.isFinite(words) || words <= 0) {
      return "";
    }
    if (words >= 10000) {
      return (words / 10000).toFixed(1).replace(/\.0$/, "") + " 万字";
    }
    return Math.round(words) + " 字";
  }

  function readingMeta(minutes, words) {
    var count = Number(minutes);
    var wordLabel = formatWordCount(words);
    var minuteLabel = "";
    if (Number.isFinite(count) && count > 0) {
      minuteLabel = "约 " + Math.round(count) + " 分钟";
    }
    if (minuteLabel && wordLabel) {
      return minuteLabel + " · " + wordLabel;
    }
    return minuteLabel || wordLabel;
  }

  function readingProgress(scrollTop, scrollHeight, clientHeight) {
    var top = Number(scrollTop);
    var height = Number(scrollHeight);
    var viewport = Number(clientHeight);
    if (
      !Number.isFinite(top) ||
      !Number.isFinite(height) ||
      !Number.isFinite(viewport) ||
      height <= 0 ||
      viewport <= 0
    ) {
      return 0;
    }
    if (height <= viewport + 1) {
      return 0;
    }
    var ratio = top / (height - viewport);
    return Math.min(100, Math.max(0, Math.round(ratio * 100)));
  }

  function adjustFontSize(current, delta, minimum, maximum) {
    var min = Number.isFinite(Number(minimum)) ? Number(minimum) : 15;
    var max = Number.isFinite(Number(maximum)) ? Number(maximum) : 24;
    if (max < min) {
      var swap = min;
      min = max;
      max = swap;
    }
    var base = Number(current);
    if (!Number.isFinite(base) || base <= 0) {
      base = 17;
    }
    var step = Number(delta);
    if (!Number.isFinite(step)) {
      step = 0;
    }
    return Math.min(max, Math.max(min, Math.round(base + step)));
  }

  function apiBaseCandidates(pathname) {
    var path = String(pathname === null || pathname === undefined ? "/" : pathname);
    if (!path) {
      path = "/";
    }
    var prefixes = ["/v2/proxy/shiye", "/proxy/shiye", "/shiye"];
    var bases = [];
    function add(base) {
      if (base && bases.indexOf(base) === -1) {
        bases.push(base);
      }
    }
    var matched = "";
    prefixes.forEach(function (prefix) {
      if (
        path === prefix ||
        path === prefix + "/" ||
        path.indexOf(prefix + "/") === 0
      ) {
        matched = prefix;
      }
    });
    if (matched) {
      add(matched + "/api/");
    } else {
      var directory = path;
      if (directory.charAt(directory.length - 1) !== "/") {
        directory = directory.slice(0, directory.lastIndexOf("/") + 1);
      }
      if (!directory) {
        directory = "/";
      }
      add(directory + "api/");
    }
    prefixes.forEach(function (prefix) {
      add(prefix + "/api/");
    });
    return bases;
  }

  function debounce(fn, wait) {
    var timer = null;
    return function debounced() {
      var args = arguments;
      var context = this;
      if (timer) {
        clearTimeout(timer);
      }
      timer = setTimeout(function () {
        timer = null;
        fn.apply(context, args);
      }, wait);
    };
  }

  return {
    formatRelativeTime: formatRelativeTime,
    formatFullDate: formatFullDate,
    formatDateTime: formatDateTime,
    truncate: truncate,
    clampNumber: clampNumber,
    buildQuery: buildQuery,
    nextArticleId: nextArticleId,
    scopeLabel: scopeLabel,
    formatBytes: formatBytes,
    formatWordCount: formatWordCount,
    readingMeta: readingMeta,
    readingProgress: readingProgress,
    adjustFontSize: adjustFontSize,
    apiBaseCandidates: apiBaseCandidates,
    debounce: debounce,
  };
});
