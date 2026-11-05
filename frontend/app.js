(function () {
  "use strict";

  var Tools = window.ShiyeTools;
  var Theme = window.ShiyeTheme;

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function isMobile() {
    return window.matchMedia("(max-width: 980px)").matches;
  }

  function formatCount(value) {
    var number = Number(value) || 0;
    if (number > 9999) {
      return "9999+";
    }
    return String(number);
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  function defaultStatusMessage(status) {
    if (status === 401 || status === 403) {
      return "会话已失效，请刷新页面重新登录。";
    }
    if (status === 404) {
      return "没有找到这项内容，可能已经被移除。";
    }
    if (status === 409) {
      return "操作与当前状态冲突。";
    }
    if (status === 413) {
      return "请求内容过大。";
    }
    if (status === 422) {
      return "请求被订阅源或服务拒绝。";
    }
    if (status >= 500) {
      return "服务暂时不可用，请稍后重试。";
    }
    return "请求失败（HTTP " + status + "）。";
  }

  function errorKind(status) {
    if (status === 401 || status === 403) {
      return "session";
    }
    if (status === 502 || status === 503 || status === 504) {
      return "gateway";
    }
    return "rejected";
  }

  class AppError extends Error {
    constructor(message, code, kind, status) {
      super(message);
      this.name = "AppError";
      this.code = code || "error";
      this.kind = kind || "error";
      this.status = status || 0;
    }
  }

  function errorMessage(error) {
    if (error && error.message) {
      return error.message;
    }
    return "操作失败，请稍后重试。";
  }

  function siteLabel(article) {
    var site = String((article && article.site_name) || "").trim();
    if (site) {
      return site;
    }
    try {
      return new URL(article.url, window.location.href).hostname.replace(
        /^www\./,
        ""
      );
    } catch (error) {
      return "";
    }
  }

  var API_PREFIXES = ["/v2/proxy/shiye", "/proxy/shiye", "/shiye"];
  var API_BASES =
    Tools && typeof Tools.apiBaseCandidates === "function"
      ? Tools.apiBaseCandidates(window.location.pathname || "/")
      : ["/api/"].concat(
          API_PREFIXES.map(function (prefix) {
            return prefix + "/api/";
          })
        );

  var Api = {
    base: API_BASES[0],
    url: function (path) {
      return this.base + String(path || "").replace(/^\/+/, "");
    },
    request: async function (path, options) {
      options = options || {};
      var method = options.method || "GET";
      var headers = {};
      var body;
      if (options.rawBody !== undefined) {
        body = options.rawBody;
        headers["Content-Type"] =
          options.contentType || "text/plain; charset=utf-8";
      } else if (options.body !== undefined) {
        body = JSON.stringify(options.body);
        headers["Content-Type"] = "application/json; charset=utf-8";
      }
      var init = {
        method: method,
        credentials: "include",
        cache: "no-store",
        headers: headers,
      };
      if (body !== undefined) {
        init.body = body;
      }
      var controller = null;
      var timer = null;
      var timedOut = false;
      if (typeof AbortController !== "undefined") {
        controller = new AbortController();
        init.signal = controller.signal;
        timer = setTimeout(function () {
          timedOut = true;
          controller.abort();
        }, options.timeout || 20000);
      }
      var response;
      try {
        response = await fetch(this.url(path), init);
      } catch (error) {
        if (timedOut) {
          setConnection("error", "请求超时");
          throw new AppError("请求超时，请稍后重试。", "timeout", "network", 0);
        }
        setConnection("error", "服务离线");
        throw new AppError(
          "无法连接拾页服务，请确认应用正在运行。",
          "offline",
          "offline",
          0
        );
      } finally {
        if (timer) {
          clearTimeout(timer);
        }
      }

      var text = "";
      try {
        text = await response.text();
      } catch (error) {
        text = "";
      }
      var data = null;
      var parsed = false;
      if (text) {
        try {
          data = JSON.parse(text);
          parsed = true;
        } catch (error) {
          parsed = false;
        }
      }

      if (!response.ok) {
        var kind = errorKind(response.status);
        var message =
          (data && (data.error || data.message)) ||
          defaultStatusMessage(response.status);
        if (kind === "session") {
          setConnection("error", "会话失效");
        } else if (kind === "gateway") {
          setConnection("error", "服务异常");
        }
        throw new AppError(
          message,
          (data && data.code) || "http_" + response.status,
          kind,
          response.status
        );
      }

      if (response.status === 204 || !text) {
        setConnection("ok", "已连接");
        return null;
      }
      if (!parsed) {
        setConnection("error", "响应异常");
        throw new AppError(
          "服务返回了无法识别的响应，请确认应用已正确安装。",
          "invalid_response",
          "gateway",
          response.status
        );
      }
      setConnection("ok", "已连接");
      return data;
    },
    get: function (path, options) {
      return this.request(path, options);
    },
    post: function (path, body, timeout) {
      return this.request(path, { method: "POST", body: body, timeout: timeout });
    },
    patch: function (path, body) {
      return this.request(path, { method: "PATCH", body: body });
    },
    del: function (path) {
      return this.request(path, { method: "DELETE" });
    },
    postRaw: function (path, text, contentType, timeout) {
      return this.request(path, {
        method: "POST",
        rawBody: text,
        contentType: contentType,
        timeout: timeout,
      });
    },
    media: function (remoteUrl) {
      return this.base + "media?url=" + encodeURIComponent(remoteUrl);
    },
  };

  var ui = {
    sidebar: $("sidebar"),
    sidebarToggle: $("sidebarToggle"),
    sidebarScrim: $("sidebarScrim"),
    searchInput: $("searchInput"),
    searchClear: $("searchClear"),
    connectionPill: $("connectionPill"),
    connectionText: $("connectionText"),
    saveLinkButton: $("saveLinkButton"),
    sidebarSaveLink: $("sidebarSaveLink"),
    refreshButton: $("refreshButton"),
    themeToggle: $("themeToggle"),
    settingsButton: $("settingsButton"),
    appVersion: $("appVersion"),
    addCategoryButton: $("addCategoryButton"),
    addFeedButton: $("addFeedButton"),
    sidebarAddFeed: $("sidebarAddFeed"),
    feedTree: $("feedTree"),
    listTitle: $("listTitle"),
    listMeta: $("listMeta"),
    unreadToggle: $("unreadToggle"),
    markAllButton: $("markAllButton"),
    listNotice: $("listNotice"),
    listNoticeText: $("listNoticeText"),
    listRetry: $("listRetry"),
    articleList: $("articleList"),
    listEmpty: $("listEmpty"),
    listEmptyTitle: $("listEmptyTitle"),
    listEmptyText: $("listEmptyText"),
    listEmptyAction: $("listEmptyAction"),
    listEmptySecondary: $("listEmptySecondary"),
    loadMoreButton: $("loadMoreButton"),
    listFooterText: $("listFooterText"),
    readerPane: $("readerPane"),
    readerProgress: $("readerProgress"),
    readerProgressBar: $("readerProgressBar"),
    readerEmpty: $("readerEmpty"),
    readerEmptyTitle: $("readerEmptyTitle"),
    readerEmptyText: $("readerEmptyText"),
    readerEmptyAction: $("readerEmptyAction"),
    reader: $("reader"),
    readerBack: $("readerBack"),
    readerFeed: $("readerFeed"),
    readerReading: $("readerReading"),
    readerTime: $("readerTime"),
    readerTitle: $("readerTitle"),
    readerAuthor: $("readerAuthor"),
    readerSite: $("readerSite"),
    readerSiteDot: $("readerSiteDot"),
    readerOriginalTop: $("readerOriginalTop"),
    readerNotice: $("readerNotice"),
    readerNoticeTitle: $("readerNoticeTitle"),
    readerNoticeText: $("readerNoticeText"),
    readerExtractButton: $("readerExtractButton"),
    readerRetryButton: $("readerRetryButton"),
    readerNoticeOriginal: $("readerNoticeOriginal"),
    readerBody: $("readerBody"),
    readerOriginalBottom: $("readerOriginalBottom"),
    readerFooterHint: $("readerFooterHint"),
    starButton: $("starButton"),
    readToggleButton: $("readToggleButton"),
    copyLinkButton: $("copyLinkButton"),
    deleteSavedButton: $("deleteSavedButton"),
    fontSmaller: $("fontSmaller"),
    fontReset: $("fontReset"),
    fontLarger: $("fontLarger"),
    workspace: document.querySelector(".workspace"),
    statusText: $("statusText"),
    footerStats: $("footerStats"),
    shortcutsButton: $("shortcutsButton"),
    addFeedDialog: $("addFeedDialog"),
    addFeedForm: $("addFeedForm"),
    feedUrl: $("feedUrl"),
    feedCategory: $("feedCategory"),
    newCategoryField: $("newCategoryField"),
    newCategoryName: $("newCategoryName"),
    addFeedError: $("addFeedError"),
    addFeedSubmit: $("addFeedSubmit"),
    saveLinkDialog: $("saveLinkDialog"),
    saveLinkForm: $("saveLinkForm"),
    saveLinkUrl: $("saveLinkUrl"),
    saveLinkError: $("saveLinkError"),
    saveLinkSubmit: $("saveLinkSubmit"),
    saveLinkFallback: $("saveLinkFallback"),
    settingsDialog: $("settingsDialog"),
    themeSelect: $("themeSelect"),
    readerFontSize: $("readerFontSize"),
    autoRefresh: $("autoRefresh"),
    refreshInterval: $("refreshInterval"),
    retentionDays: $("retentionDays"),
    maxPerFeed: $("maxPerFeed"),
    saveSettingsButton: $("saveSettingsButton"),
    settingsSavedHint: $("settingsSavedHint"),
    settingsFeedList: $("settingsFeedList"),
    importOpmlButton: $("importOpmlButton"),
    exportOpmlButton: $("exportOpmlButton"),
    opmlHint: $("opmlHint"),
    opmlFileInput: $("opmlFileInput"),
    aboutVersion: $("aboutVersion"),
    aboutDatabase: $("aboutDatabase"),
    aboutCounts: $("aboutCounts"),
    categoryDialog: $("categoryDialog"),
    categoryForm: $("categoryForm"),
    categoryDialogTitle: $("categoryDialogTitle"),
    categoryNameInput: $("categoryNameInput"),
    categoryError: $("categoryError"),
    confirmDialog: $("confirmDialog"),
    confirmTitle: $("confirmTitle"),
    confirmText: $("confirmText"),
    confirmCancel: $("confirmCancel"),
    confirmOk: $("confirmOk"),
    shortcutsDialog: $("shortcutsDialog"),
    toast: $("toast"),
    bylineDot: $("readerBylineDot"),
    searchKbd: document.querySelector(".search-field kbd"),
  };

  var DEFAULT_SETTINGS = {
    refresh_interval: 30,
    retention_days: 90,
    max_per_feed: 500,
    auto_refresh: true,
  };

  var state = {
    version: "",
    booted: false,
    feeds: [],
    categories: [],
    stats: {
      feeds: 0,
      categories: 0,
      articles: 0,
      unread: 0,
      starred: 0,
      saved: 0,
      last_refresh_at: "",
      database_bytes: 0,
      version: "",
      refresh_running: false,
    },
    settings: Object.assign({}, DEFAULT_SETTINGS),
    filter: { type: "all" },
    unreadOnly: false,
    search: "",
    articles: [],
    total: 0,
    selectedId: null,
    readerArticle: null,
    listLoading: false,
    listRequestId: 0,
    readerRequestId: 0,
    refreshing: false,
    pollTimer: null,
    toastTimer: null,
    retryHandler: null,
    confirmAction: null,
    categoryEditingId: null,
    contextMenu: null,
    readerFontSize: 17,
    progressFrame: 0,
    settingsNoticeTimer: null,
    opmlNoticeTimer: null,
  };

  // ------------------------------------------------------------------ connection

  function setConnection(stateName, label) {
    ui.connectionPill.dataset.state = stateName;
    ui.connectionText.textContent = label;
  }

  // ---------------------------------------------------------------- notifications

  function showToast(message, isError) {
    if (!message) {
      return;
    }
    ui.toast.textContent = message;
    ui.toast.classList.toggle("is-error", Boolean(isError));
    ui.toast.hidden = false;
    if (state.toastTimer) {
      clearTimeout(state.toastTimer);
    }
    state.toastTimer = setTimeout(function () {
      ui.toast.hidden = true;
      state.toastTimer = null;
    }, isError ? 4600 : 3000);
  }

  function handleActionError(error, fallback) {
    var message = errorMessage(error) || fallback;
    showToast(message, true);
    if (
      error &&
      (error.kind === "offline" ||
        error.kind === "network" ||
        error.kind === "gateway" ||
        error.kind === "session")
    ) {
      showListError(message);
    }
  }

  function showListError(message) {
    ui.listNoticeText.textContent = message;
    ui.listNotice.hidden = false;
  }

  function hideListError() {
    ui.listNotice.hidden = true;
  }

  function setHint(node, message, isError, timerName) {
    node.textContent = message || "";
    node.classList.toggle("is-error", Boolean(isError));
    if (timerName && state[timerName]) {
      clearTimeout(state[timerName]);
      state[timerName] = null;
    }
    if (timerName && message) {
      state[timerName] = setTimeout(function () {
        node.textContent = "";
        node.classList.remove("is-error");
      }, 4000);
    }
  }

  function formError(node, message) {
    if (!message) {
      node.textContent = "";
      node.hidden = true;
      return;
    }
    node.textContent = message;
    node.hidden = false;
  }

  // -------------------------------------------------------------------- dialogs

  function showDialog(dialog) {
    if (!dialog || dialog.open) {
      return;
    }
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  }

  function openAddFeedDialog() {
    ui.addFeedForm.reset();
    formError(ui.addFeedError, "");
    ui.newCategoryField.hidden = true;
    ui.newCategoryName.required = false;
    ui.addFeedSubmit.disabled = false;
    ui.addFeedSubmit.textContent = "添加订阅";
    fillCategoryOptions(ui.feedCategory, "", true);
    showDialog(ui.addFeedDialog);
    setTimeout(function () {
      ui.feedUrl.focus();
    }, 30);
  }

  function openSaveLinkDialog(prefillUrl) {
    ui.saveLinkForm.reset();
    formError(ui.saveLinkError, "");
    ui.saveLinkUrl.value = prefillUrl || "";
    ui.saveLinkSubmit.disabled = false;
    ui.saveLinkFallback.disabled = false;
    ui.saveLinkSubmit.textContent = "提取并保存";
    ui.saveLinkFallback.textContent = "仅保存链接";
    showDialog(ui.saveLinkDialog);
    setTimeout(function () {
      ui.saveLinkUrl.focus();
      ui.saveLinkUrl.select();
    }, 30);
  }

  function openSettingsDialog() {
    renderSettingsFeeds();
    renderAbout();
    populateSettingsForm();
    setHint(ui.settingsSavedHint, "", false);
    setHint(ui.opmlHint, "", false);
    showDialog(ui.settingsDialog);
  }

  function openCategoryDialog(category) {
    state.categoryEditingId = category ? Number(category.id) : null;
    ui.categoryDialogTitle.textContent = category ? "重命名分类" : "新建分类";
    ui.categoryNameInput.value = category ? category.name : "";
    formError(ui.categoryError, "");
    showDialog(ui.categoryDialog);
    setTimeout(function () {
      ui.categoryNameInput.focus();
      ui.categoryNameInput.select();
    }, 30);
  }

  function askConfirm(options) {
    state.confirmAction = options.action || null;
    ui.confirmTitle.textContent = options.title || "确认操作";
    ui.confirmText.textContent = options.text || "";
    ui.confirmOk.textContent = options.okLabel || "确定";
    ui.confirmOk.className =
      "primary-button" + (options.danger === false ? "" : " danger");
    ui.confirmOk.disabled = false;
    showDialog(ui.confirmDialog);
  }

  // ---------------------------------------------------------- sidebar rendering

  function fillCategoryOptions(select, selectedValue, includeCreate) {
    select.replaceChildren();
    var none = el("option", null, "未分类");
    none.value = "";
    select.appendChild(none);
    state.categories.forEach(function (category) {
      var option = el("option", null, category.name);
      option.value = String(category.id);
      select.appendChild(option);
    });
    if (includeCreate) {
      var create = el("option", null, "＋ 新建分类…");
      create.value = "__new__";
      select.appendChild(create);
    }
    select.value = selectedValue === null || selectedValue === undefined
      ? ""
      : String(selectedValue);
    if (!select.value && selectedValue) {
      select.value = "";
    }
  }

  function feedIcon(feed) {
    if (feed.icon_url) {
      var image = el("img", "feed-icon");
      image.alt = "";
      image.loading = "lazy";
      image.referrerPolicy = "no-referrer";
      image.src = Api.media(feed.icon_url);
      image.addEventListener(
        "error",
        function () {
          image.replaceWith(feedFallbackIcon(feed));
        },
        { once: true }
      );
      return image;
    }
    return feedFallbackIcon(feed);
  }

  function feedFallbackIcon(feed) {
    var letter = String(feed.title || "?")
      .trim()
      .charAt(0)
      .toUpperCase();
    return el("span", "feed-icon-fallback", letter || "?");
  }

  function categoryUnread(category) {
    return Number(category.unread_count) || 0;
  }

  function renderSidebar() {
    ui.feedTree.replaceChildren();
    document.querySelectorAll("[data-nav]").forEach(function (button) {
      button.classList.toggle(
        "is-active",
        button.dataset.nav === state.filter.type
      );
    });

    var grouped = new Map();
    state.categories.forEach(function (category) {
      grouped.set(Number(category.id), []);
    });
    var uncategorized = [];
    state.feeds.forEach(function (feed) {
      var key = feed.category_id === null ? null : Number(feed.category_id);
      if (key !== null && grouped.has(key)) {
        grouped.get(key).push(feed);
      } else {
        uncategorized.push(feed);
      }
    });

    var fragment = document.createDocumentFragment();

    state.categories.forEach(function (category) {
      var group = el("div", "feed-group");
      var row = el("div", "category-row");
      var select = el("button", "category-select");
      select.type = "button";
      select.dataset.categoryId = String(category.id);
      select.classList.toggle(
        "is-active",
        state.filter.type === "category" &&
          Number(state.filter.id) === Number(category.id)
      );
      select.appendChild(el("span", "category-name", category.name));
      var count = el(
        "span",
        "category-count",
        categoryUnread(category) > 0 ? formatCount(categoryUnread(category)) : ""
      );
      select.appendChild(count);
      select.addEventListener("click", function () {
        setFilter({ type: "category", id: Number(category.id) });
      });
      var menu = el("button", "category-menu", "⋯");
      menu.type = "button";
      menu.dataset.categoryMenu = String(category.id);
      menu.setAttribute("aria-label", "分类操作：" + category.name);
      menu.addEventListener("click", function (event) {
        event.stopPropagation();
        openCategoryMenu(menu, category);
      });
      row.appendChild(select);
      row.appendChild(menu);
      group.appendChild(row);

      var feeds = grouped.get(Number(category.id)) || [];
      feeds.forEach(function (feed) {
        group.appendChild(renderFeedRow(feed));
      });
      fragment.appendChild(group);
    });

    if (uncategorized.length) {
      var group = el("div", "feed-group");
      var row = el("div", "category-row");
      var label = el("div", "category-select");
      label.appendChild(el("span", "category-name", "未分类"));
      row.appendChild(label);
      group.appendChild(row);
      uncategorized.forEach(function (feed) {
        group.appendChild(renderFeedRow(feed));
      });
      fragment.appendChild(group);
    }

    if (!state.feeds.length) {
      fragment.appendChild(
        el(
          "p",
          "feed-empty",
          state.categories.length
            ? "分类已经建好，添加订阅后就可以开始阅读。"
            : "还没有订阅。点击下方按钮添加第一个 RSS 地址。"
        )
      );
    }

    ui.feedTree.appendChild(fragment);
    renderNavCounts();
  }

  function renderFeedRow(feed) {
    var row = el("button", "feed-row");
    row.type = "button";
    row.dataset.feedId = String(feed.id);
    row.classList.toggle(
      "is-active",
      state.filter.type === "feed" && Number(state.filter.id) === Number(feed.id)
    );
    row.title = feed.url;
    row.appendChild(feedIcon(feed));
    row.appendChild(el("span", "feed-name", feed.title || "未命名订阅"));
    var unread = Number(feed.unread_count) || 0;
    row.appendChild(
      el("span", "feed-count", unread > 0 ? formatCount(unread) : "")
    );
    row.addEventListener("click", function () {
      setFilter({ type: "feed", id: Number(feed.id) });
    });
    return row;
  }

  function renderNavCounts() {
    var values = {
      all: state.stats.articles,
      unread: state.stats.unread,
      saved: state.stats.saved,
      starred: state.stats.starred,
    };
    document.querySelectorAll("[data-count]").forEach(function (node) {
      var key = node.dataset.count;
      node.textContent = formatCount(values[key] || 0);
    });
    updateUnreadButton();
  }

  function updateUnreadButton() {
    ui.unreadToggle.setAttribute(
      "aria-pressed",
      state.filter.type === "unread" || state.unreadOnly ? "true" : "false"
    );
  }

  function updateCategoryCount(categoryId) {
    var category = state.categories.find(function (item) {
      return Number(item.id) === Number(categoryId);
    });
    if (!category) {
      return;
    }
    var select = ui.feedTree.querySelector(
      '.category-select[data-category-id="' + categoryId + '"]'
    );
    if (!select) {
      return;
    }
    var count = select.querySelector(".category-count");
    if (count) {
      var unread = categoryUnread(category);
      count.textContent = unread > 0 ? formatCount(unread) : "";
    }
  }

  // ----------------------------------------------------------------- filter state

  function isUnreadFilter() {
    return state.filter.type === "unread" || state.unreadOnly;
  }

  function setFilter(filter) {
    state.filter = filter;
    state.unreadOnly = false;
    ui.workspace.classList.remove("reader-open");
    closeSidebar();
    renderSidebar();
    updateUnreadButton();
    renderFilterHeader();
    ui.listMeta.textContent = "正在加载…";
    loadArticles(true);
  }

  function toggleUnreadOnly() {
    if (state.filter.type === "unread") {
      state.filter = { type: "all" };
      state.unreadOnly = false;
    } else {
      state.unreadOnly = !state.unreadOnly;
    }
    renderSidebar();
    updateUnreadButton();
    ui.listMeta.textContent = "正在加载…";
    loadArticles(true);
  }

  function showAllArticles() {
    state.unreadOnly = false;
    state.filter = { type: "all" };
    renderSidebar();
    updateUnreadButton();
    loadArticles(true);
  }

  function clearSearch() {
    state.search = "";
    ui.searchInput.value = "";
    syncSearchUi();
    loadArticles(true);
  }

  function syncSearchUi() {
    var value = ui.searchInput.value;
    ui.searchClear.hidden = !value;
    if (ui.searchKbd) {
      ui.searchKbd.hidden = Boolean(value);
    }
  }

  function renderFilterHeader() {
    var label = Tools.scopeLabel(state.filter, state.feeds, state.categories);
    ui.listTitle.textContent = label;
    var meta;
    if (state.search) {
      meta =
        "搜索 “" +
        Tools.truncate(state.search, 22) +
        "” · " +
        state.total +
        " 篇";
    } else if (state.total) {
      meta = "共 " + state.total + " 篇";
    } else if (state.filter.type === "saved") {
      meta = "还没有保存文章";
    } else {
      meta = "还没有文章";
    }
    if (state.unreadOnly && state.filter.type !== "unread") {
      meta += " · 只看未读";
    }
    ui.listMeta.textContent = meta;
    ui.markAllButton.disabled = state.total === 0;
  }

  // ---------------------------------------------------------------- article list

  function createArticleCard(article) {
    var card = el("button", "article-card");
    card.type = "button";
    card.dataset.articleId = String(article.id);
    card.classList.toggle("is-unread", !article.is_read);
    card.classList.toggle("is-read", Boolean(article.is_read));
    card.classList.toggle("is-saved", article.feed_kind === "saved");
    card.classList.toggle(
      "is-selected",
      Number(state.selectedId) === Number(article.id)
    );
    card.title = article.title || "";

    var topline = el("div", "article-topline");
    topline.appendChild(
      el("span", "article-feed", article.feed_title || "订阅")
    );
    var time = el(
      "span",
      "article-time",
      Tools.formatRelativeTime(article.published_at)
    );
    time.title = Tools.formatFullDate(article.published_at);
    topline.appendChild(time);
    card.appendChild(topline);

    card.appendChild(
      el("span", "article-title", article.title || "(无标题)")
    );
    if (article.summary) {
      card.appendChild(el("span", "article-summary", article.summary));
    }

    var bottom = el("div", "article-bottom");
    var star = el("span", "article-star", "★ 星标");
    star.hidden = !article.is_starred;
    bottom.appendChild(star);
    if (article.feed_kind === "saved" && !Number(article.word_count)) {
      bottom.appendChild(el("span", "article-saved", "仅链接"));
    }
    var reading = Tools.readingMeta(
      article.reading_minutes,
      article.word_count
    );
    if (reading) {
      bottom.appendChild(el("span", "article-reading", reading));
    }
    bottom.appendChild(el("span", "article-read-state"));
    card.appendChild(bottom);

    card.addEventListener("click", function () {
      openArticle(Number(article.id));
    });

    var wrapper = el("div");
    wrapper.setAttribute("role", "listitem");
    wrapper.appendChild(card);
    return wrapper;
  }

  function configureEmptyState() {
    var title = "";
    var text = "";
    var actionLabel = "";
    var action = null;
    var secondaryLabel = "";
    var secondaryAction = null;
    var type = state.filter.type;

    if (state.search) {
      title = "没有找到文章";
      text = "换个关键词试试，或者清除搜索查看全部内容。";
      actionLabel = "清除搜索";
      action = function () {
        clearSearch();
        ui.searchInput.focus();
      };
    } else if (type === "saved") {
      title = "稍后读还是空的";
      text =
        "看到想读的网页时，用顶部的「收进稍后读」保存。拾页会抓取正文，之后安静地读。";
      actionLabel = "保存一个链接";
      action = function () {
        openSaveLinkDialog("");
      };
      secondaryLabel = "添加订阅";
      secondaryAction = openAddFeedDialog;
    } else if (!state.feeds.length) {
      title = "把想读的都收进来";
      text =
        "订阅喜欢的网站，或把任何一篇文章存进稍后读，正文会留在这里慢慢读。";
      actionLabel = "添加订阅";
      action = openAddFeedDialog;
      secondaryLabel = "保存链接";
      secondaryAction = function () {
        openSaveLinkDialog("");
      };
    } else if (isUnreadFilter()) {
      title = "没有未读文章";
      text = "这个范围内的文章都已经读完了。新内容会在订阅刷新后出现。";
      actionLabel = "查看全部文章";
      action = showAllArticles;
      secondaryLabel = "刷新订阅";
      secondaryAction = refreshAll;
    } else if (type === "starred") {
      title = "还没有星标文章";
      text = "读到喜欢的文章时，按 S 或点「星标」，它就会留在这里。";
      actionLabel = "查看全部文章";
      action = showAllArticles;
    } else {
      title = "这里还没有文章";
      text =
        "订阅可能还没有完成第一次抓取，可以手动刷新一次；也可以直接保存一个链接。";
      actionLabel = "刷新订阅";
      action = refreshAll;
      secondaryLabel = "保存链接";
      secondaryAction = function () {
        openSaveLinkDialog("");
      };
    }

    ui.listEmptyTitle.textContent = title;
    ui.listEmptyText.textContent = text;
    ui.listEmptyAction.textContent = actionLabel;
    ui.listEmptyAction.onclick = action;
    ui.listEmptySecondary.hidden = !secondaryLabel;
    ui.listEmptySecondary.textContent = secondaryLabel;
    ui.listEmptySecondary.onclick = secondaryAction;
  }

  function renderArticles(scrollMode) {
    var previousScroll = ui.articleList.scrollTop;
    ui.articleList.replaceChildren();
    if (state.articles.length) {
      var fragment = document.createDocumentFragment();
      state.articles.forEach(function (article) {
        fragment.appendChild(createArticleCard(article));
      });
      ui.articleList.appendChild(fragment);
    }

    ui.listEmpty.hidden = state.articles.length > 0;
    if (!state.articles.length) {
      configureEmptyState();
      ui.articleList.scrollTop = 0;
    } else if (scrollMode === "keep") {
      ui.articleList.scrollTop = previousScroll;
    } else {
      ui.articleList.scrollTop = 0;
    }
    renderFilterHeader();
    updateListFooter();
  }

  function updateListFooter() {
    ui.loadMoreButton.hidden =
      state.listLoading ||
      state.total === 0 ||
      state.articles.length >= state.total;
    ui.loadMoreButton.disabled = state.listLoading;
    if (state.listLoading) {
      ui.listFooterText.textContent = "加载中…";
      return;
    }
    if (!state.total) {
      ui.listFooterText.textContent = "暂无文章";
      return;
    }
    ui.listFooterText.textContent =
      "已显示 " + state.articles.length + " / " + state.total;
  }

  function updateArticleCardRead(articleId) {
    var article = state.articles.find(function (item) {
      return Number(item.id) === Number(articleId);
    });
    var card = ui.articleList.querySelector(
      '[data-article-id="' + articleId + '"]'
    );
    if (!article || !card) {
      return;
    }
    card.classList.toggle("is-unread", !article.is_read);
    card.classList.toggle("is-read", Boolean(article.is_read));
  }

  function updateArticleCardStar(articleId, starred) {
    var card = ui.articleList.querySelector(
      '[data-article-id="' + articleId + '"]'
    );
    if (!card) {
      return;
    }
    var star = card.querySelector(".article-star");
    if (star) {
      star.hidden = !starred;
    }
    var article = state.articles.find(function (item) {
      return Number(item.id) === Number(articleId);
    });
    if (article) {
      article.is_starred = starred;
    }
  }

  function updateCardSelection() {
    ui.articleList.querySelectorAll(".article-card").forEach(function (card) {
      card.classList.toggle(
        "is-selected",
        Number(card.dataset.articleId) === Number(state.selectedId)
      );
    });
  }

  async function loadArticles(reset) {
    if (!reset && state.listLoading) {
      return;
    }
    var requestId = ++state.listRequestId;
    state.listLoading = true;
    updateListFooter();

    var params = {
      limit: 30,
      offset: reset ? 0 : state.articles.length,
    };
    var type = state.filter.type;
    if (type === "feed") {
      params.feed_id = state.filter.id;
    } else if (type === "category") {
      params.category_id = state.filter.id;
    } else if (type === "saved") {
      params.saved = 1;
    } else if (type === "starred") {
      params.starred = 1;
    }
    if (type === "unread" || state.unreadOnly) {
      params.unread = 1;
    }
    if (state.search) {
      params.q = state.search;
    }

    var succeeded = false;
    try {
      var data = await Api.get("articles?" + Tools.buildQuery(params));
      if (requestId !== state.listRequestId) {
        return;
      }
      var items = Array.isArray(data.items) ? data.items : [];
      if (reset) {
        state.articles = items;
      } else {
        var index = new Map();
        state.articles.forEach(function (article) {
          index.set(Number(article.id), true);
        });
        items.forEach(function (article) {
          if (!index.has(Number(article.id))) {
            state.articles.push(article);
          }
        });
      }
      state.total = Number(data.total) || 0;
      hideListError();
      renderArticles(reset ? "top" : "keep");
      succeeded = true;
    } catch (error) {
      if (requestId !== state.listRequestId) {
        return;
      }
      if (reset) {
        state.articles = [];
        state.total = 0;
        renderArticles("top");
      }
      showListError(errorMessage(error));
      state.retryHandler = function () {
        loadArticles(true);
      };
      ui.listFooterText.textContent = "加载失败";
      ui.loadMoreButton.hidden = true;
    } finally {
      if (requestId === state.listRequestId) {
        state.listLoading = false;
        if (succeeded) {
          updateListFooter();
        }
      }
    }
  }

  // ---------------------------------------------------------------------- reader

  function findArticle(articleId) {
    return state.articles.find(function (item) {
      return Number(item.id) === Number(articleId);
    });
  }

  var FONT_STORAGE_KEY = "shiye-reader-font";
  var FONT_MIN = 15;
  var FONT_MAX = 22;
  var FONT_DEFAULT = 17;

  function loadReaderFontSize() {
    var stored = null;
    try {
      stored = window.localStorage.getItem(FONT_STORAGE_KEY);
    } catch (error) {
      stored = null;
    }
    return Tools.adjustFontSize(stored, 0, FONT_MIN, FONT_MAX);
  }

  function applyReaderFontSize(value, options) {
    options = options || {};
    var size = Tools.adjustFontSize(value, 0, FONT_MIN, FONT_MAX);
    state.readerFontSize = size;
    document.documentElement.style.setProperty(
      "--reader-font-size",
      size + "px"
    );
    if (ui.readerFontSize) {
      ui.readerFontSize.value = String(size);
    }
    try {
      window.localStorage.setItem(FONT_STORAGE_KEY, String(size));
    } catch (error) {
      /* Font preference simply won't persist. */
    }
    if (options.toast) {
      showToast("正文字号：" + size + " px");
    }
    scheduleReaderProgress();
  }

  function resetReaderProgress() {
    ui.readerProgress.hidden = true;
    ui.readerProgressBar.style.width = "0%";
    ui.readerProgress.removeAttribute("data-value");
  }

  function updateReaderProgress() {
    if (!state.readerArticle || ui.reader.hidden) {
      resetReaderProgress();
      return;
    }
    var percent = Tools.readingProgress(
      ui.readerPane.scrollTop,
      ui.readerPane.scrollHeight,
      ui.readerPane.clientHeight
    );
    var scrollable =
      ui.readerPane.scrollHeight > ui.readerPane.clientHeight + 1;
    ui.readerProgress.hidden = !scrollable;
    ui.readerProgressBar.style.width = percent + "%";
    ui.readerProgress.dataset.value = String(percent);
  }

  function scheduleReaderProgress() {
    if (state.progressFrame) {
      return;
    }
    state.progressFrame = window.requestAnimationFrame(function () {
      state.progressFrame = 0;
      updateReaderProgress();
    });
  }

  function hideReaderNotice() {
    ui.readerNotice.hidden = true;
    ui.readerNoticeTitle.textContent = "";
    ui.readerNoticeText.textContent = "";
    ui.readerExtractButton.hidden = true;
    ui.readerExtractButton.disabled = false;
    ui.readerExtractButton.textContent = "提取正文";
    ui.readerRetryButton.hidden = true;
    ui.readerNoticeOriginal.hidden = true;
    ui.readerNoticeOriginal.removeAttribute("href");
  }

  function showReaderNotice(options) {
    options = options || {};
    ui.readerNoticeTitle.textContent = options.title || "";
    ui.readerNoticeText.textContent = options.text || "";
    ui.readerExtractButton.hidden = !options.extract;
    ui.readerRetryButton.hidden = !options.retry;
    if (options.original) {
      ui.readerNoticeOriginal.href = options.original;
      ui.readerNoticeOriginal.hidden = false;
    } else {
      ui.readerNoticeOriginal.removeAttribute("href");
      ui.readerNoticeOriginal.hidden = true;
    }
    ui.readerNotice.hidden = false;
  }

  function showReaderEmptyState(title, text, actionLabel, action) {
    state.readerArticle = null;
    ui.reader.hidden = true;
    ui.readerEmpty.hidden = false;
    ui.readerEmptyTitle.textContent = title;
    ui.readerEmptyText.textContent = text;
    if (actionLabel && action) {
      ui.readerEmptyAction.textContent = actionLabel;
      ui.readerEmptyAction.onclick = action;
      ui.readerEmptyAction.hidden = false;
    } else {
      ui.readerEmptyAction.hidden = true;
      ui.readerEmptyAction.onclick = null;
    }
    resetReaderProgress();
    hideReaderNotice();
    document.title = "拾页";
  }

  function resetReaderFor(article) {
    ui.readerEmpty.hidden = true;
    ui.reader.hidden = false;
    ui.readerFeed.textContent = article.feed_title || "订阅";
    ui.readerFeed.classList.toggle(
      "is-saved",
      article.feed_kind === "saved"
    );
    ui.readerReading.textContent = "";
    ui.readerReading.hidden = true;
    ui.readerTime.textContent = Tools.formatFullDate(article.published_at);
    ui.readerTime.dateTime = article.published_at || "";
    ui.readerTitle.textContent = article.title || "(无标题)";
    ui.readerAuthor.hidden = true;
    ui.readerSite.hidden = true;
    if (ui.bylineDot) {
      ui.bylineDot.hidden = true;
    }
    if (ui.readerSiteDot) {
      ui.readerSiteDot.hidden = true;
    }
    ui.readerOriginalTop.hidden = true;
    ui.readerOriginalTop.removeAttribute("href");
    ui.readerOriginalBottom.hidden = true;
    ui.readerOriginalBottom.removeAttribute("href");
    ui.copyLinkButton.hidden = true;
    ui.deleteSavedButton.hidden = true;
    hideReaderNotice();
    resetReaderProgress();
    ui.starButton.setAttribute("aria-pressed", "false");
    ui.readToggleButton.textContent = "…";
    ui.readerBody.classList.remove("is-fallback");
    ui.readerBody.textContent = "正在载入正文…";
    document.title = (article.title || "拾页") + " · 拾页";
  }

  function fixMediaUrls(root) {
    root.querySelectorAll("img").forEach(function (image) {
      var src = image.getAttribute("src");
      if (src && src.indexOf("api/media?") === 0) {
        image.setAttribute("src", Api.base + src.slice(4));
      }
      var srcset = image.getAttribute("srcset");
      if (srcset) {
        var fixed = srcset.split(",").map(function (part) {
          var trimmed = part.trim();
          if (trimmed.indexOf("api/media?") === 0) {
            return Api.base + trimmed.slice(4);
          }
          return trimmed;
        });
        image.setAttribute("srcset", fixed.join(", "));
      }
      image.setAttribute("loading", "lazy");
      image.setAttribute("decoding", "async");
      image.setAttribute("referrerpolicy", "no-referrer");
    });
  }

  function renderReaderMeta(article) {
    var isSaved = article.feed_kind === "saved";
    var hasAuthor = Boolean(article.author);
    var hasUrl = Boolean(article.url);
    var site = siteLabel(article);

    ui.readerFeed.textContent = isSaved
      ? "稍后读"
      : article.feed_title || "订阅";
    ui.readerFeed.classList.toggle("is-saved", isSaved);

    var reading = Tools.readingMeta(
      article.reading_minutes,
      article.word_count
    );
    ui.readerReading.textContent = reading;
    ui.readerReading.hidden = !reading;

    ui.readerTime.textContent = Tools.formatFullDate(article.published_at);
    ui.readerTime.dateTime = article.published_at || "";
    ui.readerTitle.textContent = article.title || "(无标题)";

    ui.readerAuthor.textContent = article.author || "";
    ui.readerAuthor.hidden = !hasAuthor;
    ui.readerSite.textContent = site;
    ui.readerSite.hidden = !site;
    if (ui.bylineDot) {
      ui.bylineDot.hidden = !(hasAuthor && (site || hasUrl));
    }
    if (ui.readerSiteDot) {
      ui.readerSiteDot.hidden = !(site && hasUrl);
    }

    if (hasUrl) {
      ui.readerOriginalTop.href = article.url;
      ui.readerOriginalTop.hidden = false;
      ui.readerOriginalBottom.href = article.url;
      ui.readerOriginalBottom.hidden = !article.content;
      ui.copyLinkButton.hidden = false;
    } else {
      ui.readerOriginalTop.removeAttribute("href");
      ui.readerOriginalTop.hidden = true;
      ui.readerOriginalBottom.removeAttribute("href");
      ui.readerOriginalBottom.hidden = true;
      ui.copyLinkButton.hidden = true;
    }
    ui.deleteSavedButton.hidden = !isSaved;
    document.title = (article.title || "拾页") + " · 拾页";
  }

  function renderReaderArticle(article) {
    renderReaderMeta(article);

    var content = String(article.content || "");
    ui.readerFooterHint.hidden = !content;
    if (content) {
      ui.readerBody.classList.remove("is-fallback");
      ui.readerBody.innerHTML = content;
      fixMediaUrls(ui.readerBody);
    } else {
      ui.readerBody.replaceChildren();
      ui.readerBody.classList.add("is-fallback");
      if (article.feed_kind !== "saved") {
        ui.readerBody.appendChild(
          el(
            "p",
            "reader-summary-fallback",
            article.summary || "这里没有随文章保存的正文。"
          )
        );
      }
    }

    hideReaderNotice();
    if (article.feed_kind === "saved" && !content) {
      showReaderNotice({
        title: "这里只保存了链接",
        text:
          "保存时还没有抓到正文。现在可以再试一次，提取到之后会留在稍后读里。",
        extract: true,
        original: article.url || "",
      });
    } else if (!content && article.url) {
      showReaderNotice({
        title: "这篇只有摘要",
        text: "订阅源没有提供完整正文。可以打开原文，继续阅读整篇文章。",
        original: article.url,
      });
    }

    updateReaderControls();
    updateArticleCardMeta(article);
    scheduleReaderProgress();
  }

  function updateReaderControls() {
    var article = state.readerArticle;
    if (!article) {
      return;
    }
    ui.starButton.setAttribute(
      "aria-pressed",
      article.is_starred ? "true" : "false"
    );
    var label = ui.starButton.querySelector("span");
    if (label) {
      label.textContent = article.is_starred ? "已加星标" : "星标";
    }
    ui.readToggleButton.textContent = article.is_read ? "标为未读" : "标为已读";
    ui.deleteSavedButton.hidden = article.feed_kind !== "saved";
  }

  function updateArticleCardMeta(article) {
    var index = -1;
    for (var i = 0; i < state.articles.length; i += 1) {
      if (Number(state.articles[i].id) === Number(article.id)) {
        index = i;
        break;
      }
    }
    if (index === -1) {
      return;
    }
    var merged = Object.assign({}, state.articles[index], article);
    state.articles[index] = merged;
    var card = ui.articleList.querySelector(
      '[data-article-id="' + article.id + '"]'
    );
    if (card && card.parentElement) {
      card.parentElement.replaceWith(createArticleCard(merged));
      updateCardSelection();
    }
  }

  async function openArticle(articleId) {
    var article = findArticle(articleId);
    if (!article) {
      return;
    }
    state.selectedId = Number(articleId);
    updateCardSelection();
    if (isMobile()) {
      ui.workspace.classList.add("reader-open");
    }
    resetReaderFor(article);
    ui.readerPane.scrollTop = 0;

    var requestId = ++state.readerRequestId;
    try {
      var data = await Api.get("articles/" + articleId);
      if (requestId !== state.readerRequestId) {
        return;
      }
      state.readerArticle = data.article;
      renderReaderArticle(data.article);
      ui.readerPane.scrollTop = 0;
      if (!data.article.is_read) {
        await setArticleRead(articleId, true, { silent: true });
      }
    } catch (error) {
      if (requestId !== state.readerRequestId) {
        return;
      }
      if (error && error.status === 404) {
        handleMissingArticle(articleId);
        return;
      }
      state.readerArticle = Object.assign({}, article);
      renderReaderMeta(article);
      ui.readerBody.replaceChildren();
      ui.readerBody.classList.add("is-fallback");
      ui.readerBody.appendChild(
        el(
          "p",
          "reader-summary-fallback",
          article.summary ||
            "正文暂时没有加载出来。可以重试，或打开原文继续阅读。"
        )
      );
      updateReaderControls();
      showReaderNotice({
        title: "正文没有加载出来",
        text: errorMessage(error),
        retry: true,
        original: article.url || "",
      });
      resetReaderProgress();
    }
  }

  function removeArticleFromList(articleId) {
    var index = -1;
    for (var i = 0; i < state.articles.length; i += 1) {
      if (Number(state.articles[i].id) === Number(articleId)) {
        index = i;
        break;
      }
    }
    if (index === -1) {
      return null;
    }
    var removed = state.articles.splice(index, 1)[0];
    state.total = Math.max(0, state.total - 1);
    if (!removed.is_read) {
      adjustUnread(removed.feed_id, -1);
    }
    if (removed.feed_kind === "saved") {
      state.stats.saved = Math.max(0, Number(state.stats.saved || 0) - 1);
      renderNavCounts();
    }
    var card = ui.articleList.querySelector(
      '[data-article-id="' + articleId + '"]'
    );
    if (card) {
      var item = card.closest('[role="listitem"]');
      (item || card).remove();
    }
    if (!state.articles.length) {
      renderArticles("keep");
    } else {
      renderFilterHeader();
      updateListFooter();
    }
    updateFooter();
    return removed;
  }

  function handleMissingArticle(articleId) {
    removeArticleFromList(articleId);
    if (Number(state.selectedId) === Number(articleId)) {
      state.selectedId = null;
    }
    closeMobileReader();
    showReaderEmptyState(
      "这篇文章已不在收件箱里",
      "它可能已经被清理、移出稍后读，或来自一个已删除的订阅。列表已经更新，可以继续读其他内容。",
      "回到全部文章",
      showAllArticles
    );
    showToast("这篇文章已不在收件箱里，列表已更新。");
    reloadCollections().catch(function () {
      /* The list already reflects the removal. */
    });
  }

  function closeMobileReader() {
    ui.workspace.classList.remove("reader-open");
  }

  // ------------------------------------------------------------ read/star actions

  function adjustUnread(feedId, delta) {
    if (!delta) {
      return;
    }
    state.stats.unread = Math.max(0, Number(state.stats.unread || 0) + delta);
    var feed = state.feeds.find(function (item) {
      return Number(item.id) === Number(feedId);
    });
    if (feed) {
      feed.unread_count = Math.max(
        0,
        Number(feed.unread_count || 0) + delta
      );
      var row = ui.feedTree.querySelector(
        '.feed-row[data-feed-id="' + feed.id + '"]'
      );
      if (row) {
        var count = row.querySelector(".feed-count");
        if (count) {
          count.textContent =
            feed.unread_count > 0 ? formatCount(feed.unread_count) : "";
        }
      }
      if (feed.category_id !== null && feed.category_id !== undefined) {
        var category = state.categories.find(function (item) {
          return Number(item.id) === Number(feed.category_id);
        });
        if (category) {
          category.unread_count = Math.max(
            0,
            Number(category.unread_count || 0) + delta
          );
          updateCategoryCount(category.id);
        }
      }
    }
    renderNavCounts();
    updateFooter();
  }

  async function setArticleRead(articleId, read, options) {
    options = options || {};
    var listArticle = findArticle(articleId);
    var readerArticle =
      state.readerArticle && Number(state.readerArticle.id) === Number(articleId)
        ? state.readerArticle
        : null;
    var target = listArticle || readerArticle;
    if (!target) {
      return;
    }
    var previous = Boolean(target.is_read);
    if (previous === Boolean(read)) {
      updateReaderControls();
      return;
    }

    target.is_read = Boolean(read);
    if (readerArticle) {
      readerArticle.is_read = Boolean(read);
    }
    adjustUnread(target.feed_id, read ? -1 : 1);
    updateArticleCardRead(articleId);
    updateReaderControls();

    try {
      var data = await Api.post("articles/" + articleId + "/read", { read: read });
      if (data && typeof data.unread === "number") {
        state.stats.unread = data.unread;
        renderNavCounts();
        updateFooter();
      }
      if (!options.silent) {
        showToast(read ? "已标为已读" : "已标为未读");
      }
    } catch (error) {
      target.is_read = previous;
      if (readerArticle) {
        readerArticle.is_read = previous;
      }
      adjustUnread(target.feed_id, read ? 1 : -1);
      updateArticleCardRead(articleId);
      updateReaderControls();
      handleActionError(error, "更新已读状态失败");
    }
  }

  async function toggleStar() {
    var article = state.readerArticle;
    if (!article) {
      return;
    }
    var desired = !article.is_starred;
    ui.starButton.disabled = true;
    try {
      var data = await Api.post("articles/" + article.id + "/star", {
        starred: desired,
      });
      state.readerArticle = data.article;
      var listArticle = findArticle(article.id);
      if (listArticle) {
        listArticle.is_starred = Boolean(data.article.is_starred);
      }
      state.stats.starred = Number(data.starred) || 0;
      renderNavCounts();
      updateFooter();
      updateReaderControls();
      updateArticleCardStar(article.id, Boolean(data.article.is_starred));
      showToast(data.article.is_starred ? "已加星标" : "已取消星标");
      if (state.filter.type === "starred" && !data.article.is_starred) {
        await loadArticles(true);
      }
    } catch (error) {
      handleActionError(error, "更新星标失败");
    } finally {
      ui.starButton.disabled = false;
    }
  }

  async function markAllRead() {
    var scope = state.filter;
    var label = Tools.scopeLabel(scope, state.feeds, state.categories);
    var useLoadedIds =
      scope.type === "starred" || Boolean(state.search) || isUnreadFilter();
    var ids = state.articles
      .filter(function (article) {
        return !article.is_read;
      })
      .map(function (article) {
        return article.id;
      });

    if (!state.total || (useLoadedIds && !ids.length)) {
      showToast("当前范围内没有未读文章。");
      return;
    }

    var text;
    if (useLoadedIds) {
      text =
        "当前列表中还有 " +
        ids.length +
        " 篇未读文章，将它们全部标为已读？";
    } else {
      text =
        "将「" +
        label +
        "」范围内的所有文章标为已读？未读标记无法批量撤销。";
    }

    askConfirm({
      title: "全部标为已读",
      text: text,
      okLabel: "全部已读",
      action: async function () {
        if (useLoadedIds) {
          await Api.post("articles/mark-read", { ids: ids, read: true });
        } else {
          var body = {};
          if (scope.type === "feed") {
            body.feed_id = scope.id;
          } else if (scope.type === "category") {
            body.category_id = scope.id;
          } else if (scope.type === "saved") {
            body.saved = true;
          }
          await Api.post("articles/mark-all-read", body);
        }
        if (state.readerArticle) {
          state.readerArticle.is_read = true;
          updateReaderControls();
          updateArticleCardRead(state.readerArticle.id);
        }
        await reloadCollections();
        await loadArticles(true);
        showToast("已把当前范围内的文章标为已读。");
      },
    });
  }

  async function copyArticleLink() {
    var article = state.readerArticle;
    if (!article || !article.url) {
      return;
    }
    try {
      await navigator.clipboard.writeText(article.url);
      showToast("原文链接已复制。");
    } catch (error) {
      var area = document.createElement("textarea");
      area.value = article.url;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      var copied = false;
      try {
        copied = document.execCommand("copy");
      } catch (copyError) {
        copied = false;
      }
      area.remove();
      showToast(copied ? "原文链接已复制。" : "复制失败，请手动打开原文。", !copied);
    }
  }

  // ------------------------------------------------------------- collections sync

  async function reloadCollections() {
    var results = await Promise.all([
      Api.get("feeds"),
      Api.get("categories"),
      Api.get("stats"),
    ]);
    state.feeds = results[0].items || [];
    state.categories = results[1].items || [];
    state.stats = Object.assign({}, state.stats, results[2]);
    if (results[2].version) {
      applyVersion(results[2].version);
    }
    renderSidebar();
    renderFilterHeader();
    updateFooter();
    updateRefreshIndicator();
    if (ui.settingsDialog.open) {
      renderSettingsFeeds();
      renderAbout();
    }
  }

  function applyVersion(version) {
    state.version = version;
    if (version) {
      ui.appVersion.textContent = "v" + version;
      ui.aboutVersion.textContent = "v" + version;
    }
  }

  function updateFooter() {
    var stats = state.stats || {};
    if (!state.booted && !state.feeds.length) {
      ui.statusText.textContent = "正在连接服务…";
    } else if (!stats.feeds) {
      ui.statusText.textContent = "还没有订阅";
    } else {
      var last = stats.last_refresh_at
        ? Tools.formatRelativeTime(stats.last_refresh_at)
        : "尚未刷新";
      ui.statusText.textContent =
        "共 " + stats.feeds + " 个订阅 · 上次刷新 " + last;
    }
    ui.footerStats.textContent =
      formatCount(stats.unread) +
      " 未读 · " +
      formatCount(stats.starred) +
      " 星标" +
      (Number(stats.saved) ? " · " + formatCount(stats.saved) + " 稍后读" : "");
  }

  function updateRefreshIndicator() {
    var running = state.refreshing || Boolean(state.stats.refresh_running);
    ui.refreshButton.classList.toggle("is-spinning", running);
  }

  // -------------------------------------------------------------------- refresh

  async function refreshAll() {
    if (state.refreshing) {
      return;
    }
    if (!state.feeds.length) {
      showToast("还没有订阅。先添加一个订阅源，或保存一个链接。");
      openAddFeedDialog();
      return;
    }
    state.refreshing = true;
    ui.refreshButton.disabled = true;
    updateRefreshIndicator();
    try {
      var data = await Api.post("refresh", {}, 30000);
      showToast(
        data && data.started
          ? "已开始刷新全部订阅…"
          : "刷新已在进行中，请稍候。"
      );
      var deadline = Date.now() + 150000;
      var stats = null;
      while (Date.now() < deadline) {
        await sleep(2500);
        stats = await Api.get("stats");
        state.stats = Object.assign({}, state.stats, stats);
        updateRefreshIndicator();
        if (!stats.refresh_running) {
          break;
        }
      }
      await reloadCollections();
      await loadArticles(true);
      if (stats && !stats.refresh_running) {
        showToast("订阅刷新完成。");
      } else {
        showToast("刷新仍在后台进行，完成后会自动更新。");
      }
    } catch (error) {
      handleActionError(error, "刷新订阅失败");
    } finally {
      state.refreshing = false;
      ui.refreshButton.disabled = false;
      updateRefreshIndicator();
    }
  }

  function startPolling() {
    if (state.pollTimer) {
      return;
    }
    state.pollTimer = setInterval(pollStats, 60000);
  }

  async function pollStats() {
    if (!state.booted || document.hidden) {
      return;
    }
    try {
      var stats = await Api.get("stats");
      var previous = state.stats;
      var changed =
        stats.articles !== previous.articles ||
        stats.feeds !== previous.feeds ||
        stats.unread !== previous.unread ||
        stats.starred !== previous.starred ||
        stats.saved !== previous.saved;
      state.stats = Object.assign({}, previous, stats);
      updateRefreshIndicator();
      renderNavCounts();
      updateFooter();
      if (changed) {
        await reloadCollections();
        if (document.activeElement !== ui.searchInput) {
          await loadArticles(true);
        }
      }
    } catch (error) {
      /* Polling failures should stay quiet; the connection pill already reports. */
    }
  }

  // ------------------------------------------------------------------- settings

  function setSelectValue(select, value) {
    var stringValue = String(value);
    var exists = Array.prototype.some.call(select.options, function (option) {
      return option.value === stringValue;
    });
    if (!exists) {
      var option = el("option", null, stringValue);
      option.value = stringValue;
      select.appendChild(option);
    }
    select.value = stringValue;
  }

  function populateSettingsForm() {
    var settings = state.settings || DEFAULT_SETTINGS;
    ui.themeSelect.value = Theme ? Theme.get() : "auto";
    ui.readerFontSize.value = String(state.readerFontSize);
    ui.autoRefresh.checked = Boolean(settings.auto_refresh);
    setSelectValue(ui.refreshInterval, settings.refresh_interval);
    setSelectValue(ui.retentionDays, settings.retention_days);
    setSelectValue(ui.maxPerFeed, settings.max_per_feed);
  }

  function renderAbout() {
    var stats = state.stats || {};
    ui.aboutVersion.textContent = state.version ? "v" + state.version : "—";
    ui.aboutDatabase.textContent = Tools.formatBytes(stats.database_bytes || 0);
    ui.aboutCounts.textContent =
      (stats.feeds || 0) +
      " 个订阅 · " +
      (stats.articles || 0) +
      " 篇文章" +
      (Number(stats.saved) ? " · " + stats.saved + " 篇稍后读" : "");
  }

  function renderSettingsFeeds() {
    ui.settingsFeedList.replaceChildren();
    if (!state.feeds.length) {
      ui.settingsFeedList.appendChild(
        el("p", "settings-empty", "还没有订阅。可以在左侧添加 RSS 地址。")
      );
      return;
    }
    var fragment = document.createDocumentFragment();
    state.feeds.forEach(function (feed) {
      var row = el("div", "settings-feed-row");
      var info = el("div", "settings-feed-info");
      info.appendChild(
        el("div", "settings-feed-title", feed.title || "未命名订阅")
      );
      var metaParts = [feed.url];
      if (feed.disabled) {
        metaParts.push("已暂停");
      }
      if (feed.last_error) {
        metaParts.push("上次抓取失败：" + feed.last_error);
      }
      info.appendChild(el("div", "settings-feed-meta", metaParts.join(" · ")));
      row.appendChild(info);

      var categorySelect = el("select");
      categorySelect.setAttribute("aria-label", "修改分类");
      fillCategoryOptions(categorySelect, feed.category_id, false);
      categorySelect.addEventListener("change", function () {
        changeFeedCategory(feed.id, categorySelect.value).catch(function () {});
      });
      row.appendChild(categorySelect);

      var refreshButton = el("button", "icon-button", "↻");
      refreshButton.type = "button";
      refreshButton.title = "立即刷新这个订阅";
      refreshButton.setAttribute("aria-label", "刷新订阅");
      refreshButton.addEventListener("click", function () {
        refreshSingleFeed(feed.id).catch(function () {});
      });
      row.appendChild(refreshButton);

      var pauseButton = el("button", "icon-button", feed.disabled ? "▶" : "Ⅱ");
      pauseButton.type = "button";
      pauseButton.title = feed.disabled ? "恢复抓取" : "暂停抓取";
      pauseButton.setAttribute(
        "aria-label",
        feed.disabled ? "恢复抓取" : "暂停抓取"
      );
      pauseButton.addEventListener("click", function () {
        toggleFeedDisabled(feed.id).catch(function () {});
      });
      row.appendChild(pauseButton);

      var deleteButton = el("button", "icon-button danger", "×");
      deleteButton.type = "button";
      deleteButton.title = "删除订阅";
      deleteButton.setAttribute("aria-label", "删除订阅");
      deleteButton.addEventListener("click", function () {
        confirmDeleteFeed(feed.id);
      });
      row.appendChild(deleteButton);

      fragment.appendChild(row);
    });
    ui.settingsFeedList.appendChild(fragment);
  }

  async function changeFeedCategory(feedId, value) {
    try {
      await Api.patch("feeds/" + feedId, {
        category_id: value ? Number(value) : null,
      });
      await reloadCollections();
      renderSettingsFeeds();
      setHint(ui.settingsSavedHint, "分类已更新。", false, "settingsNoticeTimer");
    } catch (error) {
      renderSettingsFeeds();
      setHint(
        ui.settingsSavedHint,
        errorMessage(error),
        true,
        "settingsNoticeTimer"
      );
    }
  }

  async function toggleFeedDisabled(feedId) {
    var feed = state.feeds.find(function (item) {
      return Number(item.id) === Number(feedId);
    });
    if (!feed) {
      return;
    }
    try {
      await Api.patch("feeds/" + feedId, { disabled: !feed.disabled });
      await reloadCollections();
      renderSettingsFeeds();
      setHint(
        ui.settingsSavedHint,
        feed.disabled ? "订阅已恢复抓取。" : "订阅已暂停抓取。",
        false,
        "settingsNoticeTimer"
      );
    } catch (error) {
      setHint(
        ui.settingsSavedHint,
        errorMessage(error),
        true,
        "settingsNoticeTimer"
      );
    }
  }

  async function refreshSingleFeed(feedId) {
    try {
      var data = await Api.post("feeds/" + feedId + "/refresh", {}, 45000);
      await reloadCollections();
      renderSettingsFeeds();
      await loadArticles(true);
      var message =
        data && data.status === "not_modified"
          ? "订阅没有新内容。"
          : "刷新完成" +
            (data && data.new_articles
              ? "，新增 " + data.new_articles + " 篇文章。"
              : "。");
      setHint(ui.settingsSavedHint, message, false, "settingsNoticeTimer");
    } catch (error) {
      setHint(
        ui.settingsSavedHint,
        errorMessage(error),
        true,
        "settingsNoticeTimer"
      );
    }
  }

  function confirmDeleteFeed(feedId) {
    var feed = state.feeds.find(function (item) {
      return Number(item.id) === Number(feedId);
    });
    if (!feed) {
      return;
    }
    askConfirm({
      title: "删除订阅",
      text:
        "删除「" +
        feed.title +
        "」及其 " +
        (feed.article_count || 0) +
        " 篇文章？此操作不可撤销。",
      okLabel: "删除",
      action: async function () {
        await Api.del("feeds/" + feedId);
        if (
          state.filter.type === "feed" &&
          Number(state.filter.id) === Number(feedId)
        ) {
          state.filter = { type: "all" };
        }
        await reloadCollections();
        await loadArticles(true);
        renderSettingsFeeds();
        showToast("订阅已删除。");
      },
    });
  }

  async function saveSettings() {
    ui.saveSettingsButton.disabled = true;
    try {
      var payload = {
        refresh_interval: Number(ui.refreshInterval.value),
        retention_days: Number(ui.retentionDays.value),
        max_per_feed: Number(ui.maxPerFeed.value),
        auto_refresh: ui.autoRefresh.checked,
      };
      var settings = await Api.request("settings", {
        method: "PUT",
        body: payload,
      });
      state.settings = settings;
      populateSettingsForm();
      setHint(ui.settingsSavedHint, "设置已保存。", false, "settingsNoticeTimer");
    } catch (error) {
      setHint(
        ui.settingsSavedHint,
        errorMessage(error),
        true,
        "settingsNoticeTimer"
      );
    } finally {
      ui.saveSettingsButton.disabled = false;
    }
  }

  // ----------------------------------------------------------------------- OPML

  async function exportOpml() {
    setHint(ui.opmlHint, "正在导出…", false);
    try {
      var response = await fetch(Api.url("opml"), {
        credentials: "include",
        cache: "no-store",
      });
      if (!response.ok) {
        var message = "导出失败";
        try {
          var data = await response.json();
          message = data.error || message;
        } catch (error) {
          /* Non-JSON failure body. */
        }
        throw new AppError(message, "export_failed", "rejected", response.status);
      }
      var blob = await response.blob();
      var url = URL.createObjectURL(blob);
      var anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "shiye-subscriptions.opml";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(function () {
        URL.revokeObjectURL(url);
      }, 2000);
      setHint(ui.opmlHint, "已导出 OPML 文件。", false, "opmlNoticeTimer");
    } catch (error) {
      setHint(ui.opmlHint, errorMessage(error), true, "opmlNoticeTimer");
    }
  }

  async function importOpmlFile(file) {
    if (!file) {
      return;
    }
    if (file.size > 4 * 1024 * 1024) {
      setHint(ui.opmlHint, "文件超过 4 MiB，无法导入。", true, "opmlNoticeTimer");
      return;
    }
    ui.importOpmlButton.disabled = true;
    setHint(ui.opmlHint, "正在导入…", false);
    try {
      var text = await file.text();
      var result = await Api.postRaw(
        "opml",
        text,
        "application/xml; charset=utf-8",
        60000
      );
      await reloadCollections();
      await loadArticles(true);
      setHint(
        ui.opmlHint,
        "已导入 " +
          (result.imported || 0) +
          " 个订阅，跳过 " +
          (result.skipped || 0) +
          " 个。文章会在后台抓取。",
        false,
        "opmlNoticeTimer"
      );
      showToast("OPML 导入完成。");
    } catch (error) {
      setHint(ui.opmlHint, errorMessage(error), true, "opmlNoticeTimer");
    } finally {
      ui.importOpmlButton.disabled = false;
      ui.opmlFileInput.value = "";
    }
  }

  // ------------------------------------------------------------------ categories

  function openCategoryMenu(button, category) {
    closeContextMenu();
    var menu = el("div", "context-menu");
    var rename = el("button", null, "重命名分类");
    rename.type = "button";
    rename.addEventListener("click", function () {
      closeContextMenu();
      openCategoryDialog(category);
    });
    var remove = el("button", "is-danger", "删除分类");
    remove.type = "button";
    remove.addEventListener("click", function () {
      closeContextMenu();
      confirmDeleteCategory(category);
    });
    menu.appendChild(rename);
    menu.appendChild(remove);
    document.body.appendChild(menu);

    var rect = button.getBoundingClientRect();
    var left = Math.max(8, Math.min(rect.left, window.innerWidth - 176));
    var top = Math.min(rect.bottom + 4, window.innerHeight - 104);
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    state.contextMenu = menu;

    setTimeout(function () {
      document.addEventListener("pointerdown", onDocumentPointerDown, true);
      window.addEventListener("scroll", closeContextMenu, true);
      window.addEventListener("resize", closeContextMenu);
    }, 0);
  }

  function onDocumentPointerDown(event) {
    if (state.contextMenu && !state.contextMenu.contains(event.target)) {
      closeContextMenu();
    }
  }

  function closeContextMenu() {
    if (state.contextMenu) {
      state.contextMenu.remove();
      state.contextMenu = null;
    }
    document.removeEventListener("pointerdown", onDocumentPointerDown, true);
    window.removeEventListener("scroll", closeContextMenu, true);
    window.removeEventListener("resize", closeContextMenu);
  }

  function confirmDeleteCategory(category) {
    askConfirm({
      title: "删除分类",
      text:
        "删除分类「" +
        category.name +
        "」？其中的订阅会移到未分类，文章不会删除。",
      okLabel: "删除分类",
      action: async function () {
        await Api.del("categories/" + category.id);
        if (
          state.filter.type === "category" &&
          Number(state.filter.id) === Number(category.id)
        ) {
          state.filter = { type: "all" };
        }
        await reloadCollections();
        await loadArticles(true);
        showToast("分类已删除。");
      },
    });
  }

  // ------------------------------------------------------------------- add feed

  async function submitAddFeed(event) {
    event.preventDefault();
    var url = ui.feedUrl.value.trim();
    if (!url) {
      formError(ui.addFeedError, "请填写订阅地址。");
      ui.feedUrl.focus();
      return;
    }
    var categoryValue = ui.feedCategory.value;
    var newCategory = ui.newCategoryName.value.trim();
    if (categoryValue === "__new__" && !newCategory) {
      formError(ui.addFeedError, "请填写新分类名称。");
      ui.newCategoryName.focus();
      return;
    }
    var payload = { url: url };
    if (categoryValue === "__new__") {
      payload.category_name = newCategory;
    } else if (categoryValue) {
      payload.category_id = Number(categoryValue);
    }

    formError(ui.addFeedError, "");
    ui.addFeedSubmit.disabled = true;
    ui.addFeedSubmit.textContent = "正在抓取…";
    try {
      var result = await Api.post("feeds", payload, 45000);
      ui.addFeedDialog.close();
      await reloadCollections();
      state.filter = { type: "feed", id: result.feed.id };
      state.unreadOnly = false;
      renderSidebar();
      await loadArticles(true);
      showToast(
        "已添加「" +
          result.feed.title +
          "」" +
          (result.new_articles
            ? "，收录 " + result.new_articles + " 篇文章。"
            : "。")
      );
    } catch (error) {
      formError(ui.addFeedError, errorMessage(error));
    } finally {
      ui.addFeedSubmit.disabled = false;
      ui.addFeedSubmit.textContent = "添加订阅";
    }
  }

  // ------------------------------------------------------------------ read later

  async function submitSaveLink(event) {
    event.preventDefault();
    await saveLinkRequest(false);
  }

  async function saveLinkRequest(fallback) {
    var url = ui.saveLinkUrl.value.trim();
    if (!url) {
      formError(ui.saveLinkError, "请先粘贴一个文章链接。");
      ui.saveLinkUrl.focus();
      return;
    }
    formError(ui.saveLinkError, "");
    ui.saveLinkSubmit.disabled = true;
    ui.saveLinkFallback.disabled = true;
    if (fallback) {
      ui.saveLinkFallback.textContent = "正在保存…";
    } else {
      ui.saveLinkSubmit.textContent = "正在提取…";
    }

    try {
      var result = await Api.post(
        "saved",
        { url: url, fallback: Boolean(fallback) },
        60000
      );
      ui.saveLinkDialog.close();
      await reloadCollections();
      var openSaved = state.filter.type === "saved" || !state.feeds.length;
      if (openSaved) {
        state.filter = { type: "saved" };
        state.unreadOnly = false;
        renderSidebar();
        renderFilterHeader();
        await loadArticles(true);
        if (result && result.article) {
          await openArticle(result.article.id);
        }
      } else {
        await loadArticles(true);
      }
      if (result && result.created === false) {
        showToast("这个链接已经在稍后读里了。");
      } else if (result && result.extracted === false) {
        showToast("链接已保存。打开后可以再提取正文。");
      } else {
        showToast("已收进稍后读。");
      }
    } catch (error) {
      var message = errorMessage(error);
      if (!fallback && error && error.status === 422) {
        message += " 如果一直抓不到，可以点「仅保存链接」先把链接存下来。";
      }
      formError(ui.saveLinkError, message);
    } finally {
      ui.saveLinkSubmit.disabled = false;
      ui.saveLinkFallback.disabled = false;
      ui.saveLinkSubmit.textContent = "提取并保存";
      ui.saveLinkFallback.textContent = "仅保存链接";
    }
  }

  async function extractSavedArticle() {
    var article = state.readerArticle;
    if (!article) {
      return;
    }
    ui.readerExtractButton.disabled = true;
    ui.readerExtractButton.textContent = "正在提取…";
    try {
      var data = await Api.post(
        "articles/" + article.id + "/extract",
        {},
        60000
      );
      state.readerArticle = data.article;
      renderReaderArticle(data.article);
      ui.readerPane.scrollTop = 0;
      updateReaderControls();
      await reloadCollections();
      showToast("正文已提取。");
    } catch (error) {
      showReaderNotice({
        title: "这次还是没能提取到正文",
        text: errorMessage(error) + " 可以稍后再试，或直接打开原文。",
        extract: true,
        original: article.url || "",
      });
      ui.readerExtractButton.disabled = false;
      ui.readerExtractButton.textContent = "重试提取";
    }
  }

  function confirmDeleteSavedArticle() {
    var article = state.readerArticle;
    if (!article || article.feed_kind !== "saved") {
      return;
    }
    askConfirm({
      title: "移出稍后读",
      text:
        "将「" +
        (article.title || "这篇文章") +
        "」移出稍后读？原文网站上的文章不会被删除。",
      okLabel: "移出",
      action: async function () {
        await Api.del("articles/" + article.id);
        state.selectedId = null;
        closeMobileReader();
        showReaderEmptyState(
          "已移出稍后读",
          "这篇文章已经从稍后读移除。列表已经更新，可以继续读其他内容。",
          "回到全部文章",
          showAllArticles
        );
        await reloadCollections();
        await loadArticles(true);
        showToast("已移出稍后读。");
      },
    });
  }

  async function submitCategory(event) {
    event.preventDefault();
    var name = ui.categoryNameInput.value.trim();
    if (!name) {
      formError(ui.categoryError, "分类名称不能为空。");
      return;
    }
    try {
      if (state.categoryEditingId) {
        await Api.patch("categories/" + state.categoryEditingId, { name: name });
      } else {
        await Api.post("categories", { name: name });
      }
      ui.categoryDialog.close();
      await reloadCollections();
      if (
        state.categoryEditingId &&
        state.filter.type === "category" &&
        Number(state.filter.id) === Number(state.categoryEditingId)
      ) {
        renderFilterHeader();
      }
      showToast(state.categoryEditingId ? "分类已重命名。" : "分类已创建。");
    } catch (error) {
      formError(ui.categoryError, errorMessage(error));
    }
  }

  // ----------------------------------------------------------------------- boot

  function checkHealth(health) {
    if (!health || health.app !== "shiye") {
      throw new AppError(
        "服务响应异常，请确认拾页已正确安装。",
        "invalid_health",
        "gateway",
        200
      );
    }
    return health;
  }

  async function discoverApiBase() {
    var lastError = null;
    for (var i = 0; i < API_BASES.length; i += 1) {
      Api.base = API_BASES[i];
      try {
        return checkHealth(await Api.get("health", { timeout: 7000 }));
      } catch (error) {
        lastError = error;
        if (
          error &&
          (error.kind === "offline" ||
            error.kind === "network" ||
            error.kind === "session")
        ) {
          throw error;
        }
      }
    }
    throw (
      lastError ||
      new AppError(
        "无法连接拾页服务，请确认应用正在运行。",
        "offline",
        "offline",
        0
      )
    );
  }

  async function boot() {
    setConnection("pending", "连接中");
    hideListError();
    ui.listMeta.textContent = "正在连接服务…";
    try {
      var health = await discoverApiBase();
      applyVersion(health.version);
      var results = await Promise.all([
        Api.get("feeds"),
        Api.get("categories"),
        Api.get("settings"),
        Api.get("stats"),
      ]);
      state.feeds = results[0].items || [];
      state.categories = results[1].items || [];
      state.settings = Object.assign({}, DEFAULT_SETTINGS, results[2]);
      state.stats = results[3];
      if (results[3].version) {
        applyVersion(results[3].version);
      }
      state.booted = true;
      renderSidebar();
      renderFilterHeader();
      updateFooter();
      updateRefreshIndicator();
      await loadArticles(true);
      startPolling();
    } catch (error) {
      state.booted = false;
      setConnection(
        "error",
        error && error.kind === "gateway" ? "服务异常" : "服务离线"
      );
      showBootFailure(error);
    }
  }

  function showBootFailure(error) {
    var message = errorMessage(error);
    ui.listTitle.textContent = "全部文章";
    ui.listMeta.textContent = "服务未连接";
    ui.articleList.replaceChildren();
    ui.listEmpty.hidden = false;
    ui.listEmptyTitle.textContent = "无法连接服务";
    ui.listEmptyText.textContent = message;
    ui.listEmptyAction.textContent = "重新连接";
    ui.listEmptyAction.onclick = function () {
      boot();
    };
    ui.listEmptySecondary.hidden = true;
    ui.loadMoreButton.hidden = true;
    ui.listFooterText.textContent = "未连接";
    ui.markAllButton.disabled = true;
    showListError(message);
    state.retryHandler = boot;
    renderSidebar();
    updateFooter();
    ui.statusText.textContent = "服务未连接";
    ui.footerStats.textContent = "";
  }

  // --------------------------------------------------------------- mobile drawer

  function openSidebar() {
    document.body.classList.add("sidebar-open");
    ui.sidebarScrim.hidden = false;
    ui.sidebarToggle.setAttribute("aria-expanded", "true");
  }

  function closeSidebar() {
    document.body.classList.remove("sidebar-open");
    ui.sidebarScrim.hidden = true;
    ui.sidebarToggle.setAttribute("aria-expanded", "false");
  }

  function toggleSidebar() {
    if (document.body.classList.contains("sidebar-open")) {
      closeSidebar();
    } else {
      openSidebar();
    }
  }

  // --------------------------------------------------------------------- events

  function isTypingTarget(target) {
    if (!target) {
      return false;
    }
    return (
      target.isContentEditable ||
      target.tagName === "INPUT" ||
      target.tagName === "TEXTAREA" ||
      target.tagName === "SELECT"
    );
  }

  function onKeyDown(event) {
    if (event.defaultPrevented) {
      return;
    }
    if (document.querySelector("dialog[open]")) {
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) {
      return;
    }
    var target = event.target;
    if (isTypingTarget(target)) {
      if (event.key === "Escape" && target === ui.searchInput) {
        event.preventDefault();
        clearSearch();
        ui.searchInput.blur();
      }
      return;
    }

    var key = event.key;
    if (key === "/") {
      event.preventDefault();
      ui.searchInput.focus();
      ui.searchInput.select();
      return;
    }
    if (key === "?") {
      event.preventDefault();
      showDialog(ui.shortcutsDialog);
      return;
    }
    if (key === "Escape") {
      if (ui.searchInput.value) {
        clearSearch();
      } else if (ui.workspace.classList.contains("reader-open")) {
        closeMobileReader();
      } else {
        closeSidebar();
      }
      return;
    }
    if (key === "j" || key === "J") {
      event.preventDefault();
      navigateArticle(1);
      return;
    }
    if (key === "k" || key === "K") {
      event.preventDefault();
      navigateArticle(-1);
      return;
    }
    if (key === "m" || key === "M") {
      event.preventDefault();
      if (state.readerArticle) {
        setArticleRead(
          state.readerArticle.id,
          !state.readerArticle.is_read,
          {}
        );
      } else {
        showToast("先用 J / K 选择一篇文章。");
      }
      return;
    }
    if (key === "s" || key === "S") {
      event.preventDefault();
      if (state.readerArticle) {
        toggleStar();
      } else {
        showToast("先用 J / K 选择一篇文章。");
      }
      return;
    }
    if (key === "u" || key === "U") {
      event.preventDefault();
      toggleUnreadOnly();
      return;
    }
    if (key === "l" || key === "L") {
      event.preventDefault();
      openSaveLinkDialog("");
      return;
    }
    if (key === "r" || key === "R") {
      event.preventDefault();
      refreshAll();
    }
  }

  function navigateArticle(direction) {
    if (!state.articles.length) {
      return;
    }
    var nextId = Tools.nextArticleId(
      state.articles,
      state.selectedId,
      direction
    );
    if (nextId === null) {
      return;
    }
    openArticle(Number(nextId));
    var card = ui.articleList.querySelector(
      '[data-article-id="' + nextId + '"]'
    );
    if (card) {
      card.scrollIntoView({ block: "nearest" });
    }
  }

  function syncThemeToggle() {
    var resolved = Theme.resolve(Theme.get());
    var label =
      resolved === "dark" ? "切换到浅色主题" : "切换到深色主题";
    ui.themeToggle.setAttribute("aria-label", label);
    ui.themeToggle.title = label;
    if (ui.settingsDialog.open) {
      ui.themeSelect.value = Theme.get();
    }
  }

  function bindEvents() {
    ui.sidebarToggle.addEventListener("click", toggleSidebar);
    ui.sidebarScrim.addEventListener("click", closeSidebar);

    document.querySelectorAll("[data-nav]").forEach(function (button) {
      button.addEventListener("click", function () {
        var type = button.dataset.nav;
        setFilter({ type: type });
      });
    });

    ui.addCategoryButton.addEventListener("click", function () {
      openCategoryDialog(null);
    });
    [ui.addFeedButton, ui.sidebarAddFeed].forEach(function (button) {
      button.addEventListener("click", openAddFeedDialog);
    });
    [ui.saveLinkButton, ui.sidebarSaveLink].forEach(function (button) {
      button.addEventListener("click", function () {
        openSaveLinkDialog("");
      });
    });

    ui.searchInput.addEventListener("input", function () {
      syncSearchUi();
      debouncedSearch();
    });
    ui.searchInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        state.search = ui.searchInput.value.trim();
        loadArticles(true);
      }
    });
    ui.searchClear.addEventListener("click", function () {
      clearSearch();
      ui.searchInput.focus();
    });

    ui.unreadToggle.addEventListener("click", toggleUnreadOnly);
    ui.markAllButton.addEventListener("click", markAllRead);
    ui.loadMoreButton.addEventListener("click", function () {
      loadArticles(false);
    });
    ui.listRetry.addEventListener("click", function () {
      var handler = state.retryHandler;
      if (handler) {
        handler();
      } else {
        loadArticles(true);
      }
    });

    ui.refreshButton.addEventListener("click", refreshAll);
    ui.settingsButton.addEventListener("click", openSettingsDialog);
    ui.themeToggle.addEventListener("click", function () {
      Theme.toggle();
      var preference = Theme.get();
      var labels = { auto: "跟随系统", light: "浅色", dark: "深色" };
      showToast("主题：" + (labels[preference] || preference));
      syncThemeToggle();
    });
    window.addEventListener("shiye-theme-change", syncThemeToggle);
    syncThemeToggle();
    ui.themeSelect.addEventListener("change", function () {
      Theme.set(ui.themeSelect.value);
    });
    ui.shortcutsButton.addEventListener("click", function () {
      showDialog(ui.shortcutsDialog);
    });

    ui.feedCategory.addEventListener("change", function () {
      var isNew = ui.feedCategory.value === "__new__";
      ui.newCategoryField.hidden = !isNew;
      ui.newCategoryName.required = isNew;
      if (isNew) {
        ui.newCategoryName.focus();
      }
    });
    ui.addFeedForm.addEventListener("submit", submitAddFeed);
    ui.saveLinkForm.addEventListener("submit", submitSaveLink);
    ui.saveLinkFallback.addEventListener("click", function () {
      saveLinkRequest(true);
    });
    ui.categoryForm.addEventListener("submit", submitCategory);
    ui.saveSettingsButton.addEventListener("click", saveSettings);
    ui.importOpmlButton.addEventListener("click", function () {
      ui.opmlFileInput.click();
    });
    ui.exportOpmlButton.addEventListener("click", exportOpml);
    ui.opmlFileInput.addEventListener("change", function () {
      var file = ui.opmlFileInput.files && ui.opmlFileInput.files[0];
      importOpmlFile(file).catch(function (error) {
        setHint(ui.opmlHint, errorMessage(error), true, "opmlNoticeTimer");
      });
    });

    ui.starButton.addEventListener("click", toggleStar);
    ui.readToggleButton.addEventListener("click", function () {
      if (!state.readerArticle) {
        return;
      }
      setArticleRead(
        state.readerArticle.id,
        !state.readerArticle.is_read,
        {}
      );
    });
    ui.copyLinkButton.addEventListener("click", copyArticleLink);
    ui.deleteSavedButton.addEventListener("click", confirmDeleteSavedArticle);
    ui.readerExtractButton.addEventListener("click", extractSavedArticle);
    ui.readerRetryButton.addEventListener("click", function () {
      if (state.selectedId) {
        openArticle(Number(state.selectedId));
      }
    });
    ui.readerBack.addEventListener("click", closeMobileReader);

    ui.fontSmaller.addEventListener("click", function () {
      applyReaderFontSize(state.readerFontSize - 1, { toast: true });
    });
    ui.fontReset.addEventListener("click", function () {
      applyReaderFontSize(FONT_DEFAULT, { toast: true });
    });
    ui.fontLarger.addEventListener("click", function () {
      applyReaderFontSize(state.readerFontSize + 1, { toast: true });
    });
    ui.readerFontSize.addEventListener("change", function () {
      applyReaderFontSize(ui.readerFontSize.value);
    });
    ui.readerPane.addEventListener("scroll", scheduleReaderProgress, {
      passive: true,
    });
    window.addEventListener("resize", scheduleReaderProgress);

    ui.confirmCancel.addEventListener("click", function () {
      state.confirmAction = null;
      ui.confirmDialog.close();
    });
    ui.confirmOk.addEventListener("click", async function () {
      var action = state.confirmAction;
      if (!action) {
        ui.confirmDialog.close();
        return;
      }
      ui.confirmOk.disabled = true;
      try {
        await action();
        ui.confirmDialog.close();
      } catch (error) {
        ui.confirmDialog.close();
        handleActionError(error, "操作失败");
      } finally {
        ui.confirmOk.disabled = false;
        state.confirmAction = null;
      }
    });

    document.querySelectorAll("[data-close-dialog]").forEach(function (button) {
      button.addEventListener("click", function () {
        var dialog = button.closest("dialog");
        if (dialog) {
          dialog.close();
        }
      });
    });
    document.querySelectorAll("dialog.app-dialog").forEach(function (dialog) {
      dialog.addEventListener("click", function (event) {
        if (event.target === dialog) {
          dialog.close();
        }
      });
    });

    document.addEventListener("keydown", onKeyDown);

    window.addEventListener("online", function () {
      if (!state.booted) {
        boot();
      } else {
        pollStats();
      }
    });
    window.addEventListener("offline", function () {
      setConnection("error", "网络离线");
    });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && state.booted) {
        pollStats();
      }
    });
    window.addEventListener("resize", function () {
      if (!isMobile()) {
        closeSidebar();
      }
    });
  }

  var debouncedSearch = Tools.debounce(function () {
    state.search = ui.searchInput.value.trim();
    loadArticles(true);
  }, 280);

  function init() {
    bindEvents();
    syncSearchUi();
    applyReaderFontSize(loadReaderFontSize());
    if (Theme) {
      ui.themeSelect.value = Theme.get();
    }
    updateFooter();
    boot();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
