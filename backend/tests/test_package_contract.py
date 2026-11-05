"""Static package-contract checks mirroring the TOS review gate."""

from __future__ import annotations

import configparser
import json
import re
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANGUAGES = {
    "zh-cn",
    "zh-hk",
    "en-us",
    "fr-fr",
    "de-de",
    "it-it",
    "es-es",
    "hu-hu",
    "ja-jp",
    "ko-kr",
    "pl-pl",
    "ru-ru",
    "tr-tr",
    "pt-pt",
}
REQUIRED_FRONTEND = {
    "index.html",
    "styles.css",
    "app.js",
    "theme.js",
    "tools.js",
    "favicon.svg",
}
REQUIRED_BACKEND = {
    "__init__.py",
    "feeds.py",
    "readable.py",
    "sanitize.py",
    "server.py",
    "service.py",
    "store.py",
    "util.py",
}


class PackageContractTests(unittest.TestCase):
    def test_versions_match(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        config = json.loads((ROOT / "config.ini").read_text(encoding="utf-8"))
        control = (ROOT / "DEBIAN" / "control").read_text(encoding="utf-8")
        backend = (ROOT / "backend" / "shiye" / "__init__.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(config["version"], version)
        self.assertIn(f"Version: {version}", control)
        self.assertIn(f'__version__ = "{version}"', backend)
        self.assertRegex(version, r"^\d+(?:\.\d+){0,2}$")

    def test_config_contract(self) -> None:
        config = json.loads((ROOT / "config.ini").read_text(encoding="utf-8"))
        self.assertEqual(config["id"], "shiye")
        self.assertEqual(config["publisher"], "Yuii")
        self.assertEqual(config["user"], "shiye")
        self.assertEqual(config["package"], "shiye")
        self.assertEqual(config["system_id"], "shiye-system")
        self.assertEqual(config["icon"], "/images/icons/shiye.svg")
        self.assertEqual(config["path"], "/shiye/")
        self.assertEqual(config["type"], "iframe")
        self.assertEqual(config["application_type"], "deb")
        self.assertIn("Utilities", config["category"])
        self.assertNotIn("open_path", config)
        self.assertEqual(config["platform"], "x86_64")
        self.assertEqual(config["low_version"], "TOS7.0")

    def test_service_contract(self) -> None:
        unit = (ROOT / "init.d" / "shiye-system.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("User=shiye", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ProtectHome=true", unit)
        self.assertNotRegex(unit, r"(?m)^\s*Group\s*=")
        self.assertNotRegex(unit, r"(?mi)^\s*PrivateTmp\s*=\s*(true|yes|1)\s*$")
        self.assertIn(
            "ExecStart=/usr/bin/python3 "
            "/usr/local/shiye/bin/shiye-service",
            unit,
        )
        self.assertIn(
            "ExecStopPost=-/bin/rm -f /var/api/shiye.sock",
            unit,
        )
        self.assertIn(
            "EnvironmentFile=-/usr/local/shiye/shiye.env", unit
        )
        self.assertIn("TimeoutStopSec=10", unit)
        self.assertIn("KillSignal=SIGTERM", unit)
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("MemoryMax=512M", unit)
        self.assertIn("CPUQuota=50%", unit)
        self.assertIn(
            "ReadWritePaths=/usr/local/shiye/data "
            "/usr/local/shiye/logs /var/api",
            unit,
        )

    def test_language_contract(self) -> None:
        raw = (ROOT / "shiye.lang").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", raw)
        self.assertNotIn(b"\r", raw)
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(raw.decode("utf-8"))
        self.assertEqual(set(parser.sections()), LANGUAGES)
        for section in LANGUAGES:
            for field in ("name", "auth", "descript", "release_note", "important"):
                self.assertTrue(parser[section][field].strip(), f"{section}.{field}")
            self.assertEqual(parser[section]["auth"].strip().strip('"'), "Yuii")

    def test_language_release_notes_match_version(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string((ROOT / "shiye.lang").read_text(encoding="utf-8"))
        for section in parser.sections():
            self.assertTrue(
                parser[section]["release_note"].strip().strip('"').startswith(version),
                section,
            )
            self.assertTrue(parser[section]["important"].strip(), section)

    def test_lifecycle_safety_contract(self) -> None:
        for name in ("postinst", "prerm", "postrm"):
            path = ROOT / "DEBIAN" / name
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"#!/bin/sh\n"), name)
            self.assertNotIn(b"\r\n", raw, name)
            self.assertNotIn(b"\r", raw, name)
            text = raw.decode("utf-8")
            self.assertNotIn("apt install", text)
            self.assertNotIn("chmod 0770 /var/api", text)
            self.assertNotIn("[[", text)
            self.assertNotIn("function ", text)
            self.assertNotIn("source ", text)
        postinst = (ROOT / "DEBIAN" / "postinst").read_text(encoding="utf-8")
        prerm = (ROOT / "DEBIAN" / "prerm").read_text(encoding="utf-8")
        postrm = (ROOT / "DEBIAN" / "postrm").read_text(encoding="utf-8")
        self.assertIn("systemctl reset-failed", postinst)
        self.assertIn("systemctl is-active --quiet", postinst)
        self.assertIn('[ -S "${SOCKET_PATH}" ]', postinst)
        self.assertIn("--gid allusers", postinst)
        self.assertIn("postinst-diagnostic.txt", postinst)
        self.assertIn("systemctl stop", prerm)
        self.assertIn("systemctl disable", prerm)
        self.assertIn('[ -S "${SOCKET_PATH}" ]', prerm)
        self.assertIn('rm -rf "${APP_DIR}"', postrm)
        self.assertIn('if [ "$1" = "purge" ]', postrm)
        self.assertIn("userdel", postrm)
        self.assertNotIn("rm -rf /var/api", postrm)
        self.assertNotIn("0777", postinst.replace("1777", ""))

    @unittest.skipUnless(shutil.which("sh"), "sh is not available on this host")
    def test_lifecycle_scripts_parse_with_sh(self) -> None:
        for name in ("postinst", "prerm", "postrm"):
            result = subprocess.run(
                ["sh", "-n", str(ROOT / "DEBIAN" / name)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")

    def test_icon_contract(self) -> None:
        config = json.loads((ROOT / "config.ini").read_text(encoding="utf-8"))
        icon = ROOT / config["icon"].lstrip("/")
        self.assertTrue(icon.is_file())
        self.assertLessEqual(icon.stat().st_size, 50 * 1024)
        raw = icon.read_bytes()
        root = ET.fromstring(raw)
        self.assertEqual(root.attrib.get("viewBox"), "0 0 64 64")
        self.assertIsNone(re.search(r"<script\b", raw.decode("utf-8"), re.I))
        self.assertTrue((ROOT / "frontend" / "favicon.svg").is_file())

    def test_backend_layout(self) -> None:
        backend = ROOT / "backend" / "shiye"
        for name in REQUIRED_BACKEND:
            self.assertTrue((backend / name).is_file(), name)
        entry = ROOT / "backend" / "shiye-service"
        self.assertTrue(entry.is_file())
        text = entry.read_text(encoding="utf-8")
        self.assertIn("from shiye.server import main", text)
        self.assertIn("sys.path", text)

    def test_frontend_contract(self) -> None:
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
        theme = (ROOT / "frontend" / "theme.js").read_text(encoding="utf-8")
        server = (ROOT / "backend" / "shiye" / "server.py").read_text(
            encoding="utf-8"
        )
        service = (ROOT / "backend" / "shiye" / "service.py").read_text(
            encoding="utf-8"
        )
        for element_id in (
            "main",
            "sidebar",
            "sidebarToggle",
            "sidebarScrim",
            "searchInput",
            "searchClear",
            "connectionPill",
            "connectionText",
            "saveLinkButton",
            "refreshButton",
            "themeToggle",
            "settingsButton",
            "appVersion",
            "addCategoryButton",
            "addFeedButton",
            "sidebarAddFeed",
            "sidebarSaveLink",
            "feedTree",
            "listTitle",
            "listMeta",
            "unreadToggle",
            "markAllButton",
            "listNotice",
            "listNoticeText",
            "listRetry",
            "articleList",
            "listEmpty",
            "listEmptyTitle",
            "listEmptyText",
            "listEmptyAction",
            "listEmptySecondary",
            "loadMoreButton",
            "listFooterText",
            "readerPane",
            "readerProgress",
            "readerProgressBar",
            "readerEmpty",
            "readerEmptyTitle",
            "readerEmptyText",
            "readerEmptyAction",
            "reader",
            "readerBack",
            "readerFeed",
            "readerReading",
            "readerTime",
            "readerTitle",
            "readerAuthor",
            "readerBylineDot",
            "readerSite",
            "readerSiteDot",
            "readerOriginalTop",
            "readerNotice",
            "readerNoticeTitle",
            "readerNoticeText",
            "readerExtractButton",
            "readerRetryButton",
            "readerNoticeOriginal",
            "readerBody",
            "readerOriginalBottom",
            "readerFooterHint",
            "starButton",
            "readToggleButton",
            "copyLinkButton",
            "deleteSavedButton",
            "fontSmaller",
            "fontReset",
            "fontLarger",
            "statusText",
            "footerStats",
            "shortcutsButton",
            "addFeedDialog",
            "addFeedForm",
            "feedUrl",
            "feedCategory",
            "newCategoryField",
            "newCategoryName",
            "addFeedError",
            "addFeedSubmit",
            "saveLinkDialog",
            "saveLinkForm",
            "saveLinkUrl",
            "saveLinkError",
            "saveLinkSubmit",
            "saveLinkFallback",
            "settingsDialog",
            "themeSelect",
            "readerFontSize",
            "autoRefresh",
            "refreshInterval",
            "retentionDays",
            "maxPerFeed",
            "saveSettingsButton",
            "settingsSavedHint",
            "settingsFeedList",
            "importOpmlButton",
            "exportOpmlButton",
            "opmlHint",
            "opmlFileInput",
            "aboutVersion",
            "aboutDatabase",
            "aboutCounts",
            "categoryDialog",
            "categoryForm",
            "categoryDialogTitle",
            "categoryNameInput",
            "categoryError",
            "confirmDialog",
            "confirmTitle",
            "confirmText",
            "confirmCancel",
            "confirmOk",
            "shortcutsDialog",
            "toast",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertNotIn("内容不存在", html)
        self.assertNotIn("内容不存在", script)
        self.assertIn('data-theme="light"', html)
        self.assertIn("__APP_VERSION__", html)
        self.assertIn("app.js?v=__APP_VERSION__", html)
        self.assertIn("styles.css?v=__APP_VERSION__", html)
        self.assertIn("theme.js?v=__APP_VERSION__", html)
        self.assertIn("tools.js?v=__APP_VERSION__", html)
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(html, r"\son[a-z]+\s*=")
        self.assertIn('credentials: "include"', script)
        self.assertIn('cache: "no-store"', script)
        self.assertIn('health.app !== "shiye"', script)
        self.assertIn('"/v2/proxy/shiye"', script)
        self.assertIn('"/shiye"', script)
        self.assertIn("fixMediaUrls", script)
        self.assertIn("media?url=", script)
        self.assertIn("localStorage", theme)
        for name in REQUIRED_FRONTEND:
            self.assertIn(f'"{name}"', server)
        self.assertIn("Content-Security-Policy", server)
        self.assertIn("X-Content-Type-Options", server)
        self.assertIn('"app": APP_ID', service)
        self.assertIn("STATIC_FILES", server)

    def test_readme_attribution(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Miniflux", readme)
        self.assertIn("FreshRSS", readme)
        self.assertIn("Yuii", readme)
        self.assertIn("MIT", readme)

    def test_docs_and_license_exist(self) -> None:
        for relative in (
            "README.md",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "CLAUDE.md",
            "ROADMAP.md",
            "LICENSE",
            "docs/ARCHITECTURE.md",
            "docs/PROJECT_LAYOUT.md",
            "docs/TOS7_SPEC_COMPLIANCE_CN.md",
            ".github/workflows/build.yml",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".github/ISSUE_TEMPLATE/feature_request.yml",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Copyright (c) 2026 Yuii", license_text)
        self.assertIn("MIT License", license_text)


if __name__ == "__main__":
    unittest.main()
