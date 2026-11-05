(function () {
  "use strict";

  var STORAGE_KEY = "shiye-theme";
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var current = "auto";

  function readStored() {
    try {
      var value = window.localStorage.getItem(STORAGE_KEY);
      if (value === "light" || value === "dark" || value === "auto") {
        return value;
      }
    } catch (error) {
      /* localStorage can be unavailable in private windows. */
    }
    return "auto";
  }

  function resolve(value) {
    if (value === "light" || value === "dark") {
      return value;
    }
    return media && media.matches ? "dark" : "light";
  }

  function apply(value) {
    current = value === "light" || value === "dark" ? value : "auto";
    var resolved = resolve(current);
    document.documentElement.setAttribute("data-theme", resolved);
    document.documentElement.setAttribute("data-theme-preference", current);
    try {
      window.dispatchEvent(
        new CustomEvent("shiye-theme-change", {
          detail: { preference: current, resolved: resolved },
        })
      );
    } catch (error) {
      /* CustomEvent is available in every supported browser. */
    }
    return resolved;
  }

  function set(value) {
    var preference = value === "light" || value === "dark" ? value : "auto";
    try {
      window.localStorage.setItem(STORAGE_KEY, preference);
    } catch (error) {
      /* Keep the in-memory preference when storage is blocked. */
    }
    return apply(preference);
  }

  function cycle() {
    var order = ["auto", "light", "dark"];
    var index = order.indexOf(current);
    return set(order[(index + 1) % order.length]);
  }

  if (media) {
    var onChange = function () {
      if (current === "auto") {
        apply("auto");
      }
    };
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", onChange);
    } else if (typeof media.addListener === "function") {
      media.addListener(onChange);
    }
  }

  window.ShiyeTheme = {
    get: function () {
      return current;
    },
    resolve: resolve,
    apply: apply,
    set: set,
    cycle: cycle,
  };

  apply(readStored());
})();
