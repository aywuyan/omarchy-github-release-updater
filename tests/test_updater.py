#!/usr/bin/env python3
"""Local tests for the GitHub release updater helper and its QML text sinks.

Run with:  python3 -m unittest discover -s tests -v
"""

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

PLUGIN_DIR = Path(__file__).resolve().parent.parent
SCRIPT = PLUGIN_DIR / "scripts" / "github_release_updater.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("github_release_updater", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_helper()

    def check(self, current, latest, expected):
        self.assertEqual(self.helper.compare_versions(current, latest), expected,
                         f"{current} vs {latest}")

    def test_numeric_release(self):
        self.check("1.2.3", "1.2.10", 1)
        self.check("2024.10", "v2024.9", -1)
        self.check("v2.0", "2.0.0", 0)

    def test_pacman_epoch_and_pkgrel(self):
        self.check("1:1.2.3", "1.2.4", 1)
        self.check("1.2.3-2", "1.2.3-1", 0)

    def test_prerelease_is_older_than_release(self):
        self.check("1.2.3-rc1", "1.2.3", 1)
        self.check("1.2.3", "1.2.3-rc1", -1)
        self.check("1.0.0-beta.2", "1.0.0-beta.10", 1)

    def test_build_metadata_is_ignored(self):
        self.check("v1.2.3+build", "1.2.3", 0)

    def test_unreadable_versions_compare_manually(self):
        self.check("rolling-x", "rolling-y", None)
        self.check("nightly", "1.2.3", None)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_helper()

    def test_app_names_cannot_escape_the_download_directory(self):
        for name in ("../../escape", "a/b", "..", ".hidden", "-flag", ""):
            with self.assertRaises(self.helper.UpdaterError, msg=name):
                self.helper.validate_app_name(name)
        for name in ("sparkle", "My App 2", "app_name+git@x"):
            self.assertEqual(self.helper.validate_app_name(name), name)

    def test_repository_parts_reject_dot_segments(self):
        for part in ("..", ".", ""):
            with self.assertRaises(self.helper.UpdaterError, msg=part):
                self.helper.validate_repo_parts("owner", part)
        self.helper.validate_repo_parts("owner", "repo.name-1")

    def test_missing_manifest_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.helper.load_apps(Path(folder) / "apps.json"), [])

    def test_appimage_entry_needs_absolute_target(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "apps.json"
            manifest.write_text(json.dumps({"apps": [{
                "name": "app",
                "repo": "owner/repo",
                "installed_version": "1.0",
                "install_method": "appimage",
            }]}), encoding="utf-8")
            with self.assertRaises(self.helper.UpdaterError) as caught:
                self.helper.load_apps(manifest)
            self.assertEqual(caught.exception.code, "app_missing_target")

    def test_add_rejects_escaped_name_and_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "apps.json"
            with self.assertRaises(self.helper.UpdaterError) as caught:
                self.helper.add_release_app(
                    manifest, "https://github.com/owner/repo", "1.0", "", "pacman",
                    "../../escape", "", "stable")
            self.assertEqual(caught.exception.code, "app_bad_name")
            app = self.helper.add_release_app(
                manifest, "https://github.com/owner/repo", "1.0", "", "pacman",
                "repo", "", "stable")
            self.assertEqual(app["name"], "repo")
            with self.assertRaises(self.helper.UpdaterError) as caught:
                self.helper.add_release_app(
                    manifest, "https://github.com/owner/other", "1.0", "", "pacman",
                    "repo", "", "stable")
            self.assertEqual(caught.exception.code, "duplicate_app")


class FirstInstallTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_helper()

    def app(self, method="appimage"):
        return {
            "name": "ds-bot",
            "repo": "Fei2411/DS-Bot",
            "installed_version": "",
            "asset_regex": "",
            "install_method": method,
            "release_channel": "stable",
            "installed_build_id": "",
            "target": "/tmp/DS-Bot.AppImage" if method == "appimage" else "",
        }

    def test_add_accepts_a_blank_version(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = Path(folder) / "apps.json"
            app = self.helper.add_release_app(
                manifest, "https://github.com/Fei2411/DS-Bot/releases", "",
                "", "appimage", "DS-Bot", "/tmp/DS-Bot.AppImage", "stable")
            self.assertEqual(app["installed_version"], "")
            loaded = self.helper.load_apps(manifest)
            self.assertEqual(loaded[0]["installed_version"], "")

    def test_appimage_version_is_detected_after_install(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "DS-Bot.AppImage"
            path.write_text("#!/bin/sh\necho 'DS-Bot 0.3.6'\n")
            path.chmod(0o755)
            result = self.helper.detect_installed_version("appimage", "", str(path), "")
            self.assertEqual(result["version"], "0.3.6")

    def test_blank_current_version_is_not_installed(self):
        app = self.app()
        release = {
            "tag_name": "v0.3.6",
            "html_url": "https://github.com/Fei2411/DS-Bot/releases/tag/v0.3.6",
            "assets": [{
                "name": "DS-Bot-0.3.6-linux-x86_64.AppImage",
                "size": 100,
                "digest": "sha256:bbb",
                "updated_at": "2026-01-01T00:00:00Z",
                "browser_download_url": "https://example.invalid/app",
            }],
        }
        original_release = self.helper.release_for
        original_version = self.helper.installed_version_for
        try:
            self.helper.release_for = lambda app: release
            self.helper.installed_version_for = lambda app, fallback=None: ""
            result = self.helper.inspect(app)
        finally:
            self.helper.release_for = original_release
            self.helper.installed_version_for = original_version
        self.assertEqual(result["status"], "not_installed")
        self.assertEqual(result["latest"], "v0.3.6")


class AutoProbeTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_helper()
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.release = {
            "tag_name": "v0.3.6",
            "html_url": "https://github.com/Fei2411/DS-Bot/releases/tag/v0.3.6",
            "assets": [],
        }
        self.helper.release_for = lambda app: self.release
        self.helper.default_appimage_target = (
            lambda name: self.home / "Applications" / (name + ".AppImage"))
        self.helper.find_existing_appimage = lambda *args: None
        self.helper.installed_version_for = lambda app, fallback=None: ""
        self.helper.detect_installed_version = (
            lambda *args: {"version": "0.3.5", "source": "test"})

    def tearDown(self):
        self.temp.cleanup()

    def asset(self, name):
        return {"name": name, "size": 1, "digest": "sha256:" + name, "updated_at": "",
                "browser_download_url": "https://example.invalid/" + name}

    def test_appimage_release_gets_a_default_path(self):
        self.release["assets"] = [self.asset("DS-Bot-0.3.6-linux-x86_64.AppImage")]
        result = self.helper.probe_release(
            "https://github.com/Fei2411/DS-Bot/releases", "stable", "DS-Bot")
        self.assertEqual(result["install_method"], "appimage")
        self.assertEqual(result["target"], str(self.home / "Applications" / "DS-Bot.AppImage"))
        self.assertTrue(result["target_is_default"])

    def test_pacman_is_preferred_when_available(self):
        self.release["assets"] = [
            self.asset("DS-Bot-0.3.6-linux-x86_64.AppImage"),
            self.asset("DS-Bot-0.3.6-x86_64.pkg.tar.zst"),
        ]
        result = self.helper.probe_release(
            "https://github.com/Fei2411/DS-Bot/releases", "stable", "DS-Bot")
        self.assertEqual(result["install_method"], "pacman")
        self.assertEqual(result["target"], "")

    def test_an_existing_appimage_is_reused(self):
        self.release["assets"] = [self.asset("DS-Bot-0.3.6-linux-x86_64.AppImage")]
        existing = self.home / "Applications" / "DS-Bot.AppImage"
        existing.parent.mkdir(parents=True)
        existing.write_text("x")
        self.helper.find_existing_appimage = lambda *args: existing
        result = self.helper.probe_release(
            "https://github.com/Fei2411/DS-Bot/releases", "stable", "DS-Bot")
        self.assertEqual(result["install_method"], "appimage")
        self.assertEqual(result["target"], str(existing))
        self.assertFalse(result["target_is_default"])
        self.assertEqual(result["installed_version"], "0.3.5")

    def test_release_without_an_installable_asset(self):
        self.release["assets"] = [self.asset("DS-Bot-0.3.6-linux-amd64.deb"),
                                  self.asset("DS-Bot-0.3.6-win-x64.exe")]
        with self.assertRaises(self.helper.UpdaterError) as caught:
            self.helper.probe_release(
                "https://github.com/Fei2411/DS-Bot/releases", "stable", "DS-Bot")
        self.assertEqual(caught.exception.code, "no_supported_asset")


class AssetSelectionTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_helper()

    def asset(self, name, digest):
        return {
            "name": name,
            "digest": digest,
            "size": 100,
            "updated_at": "2026-01-01T00:00:00Z",
            "browser_download_url": "https://example.invalid/" + name,
        }

    def release(self, assets):
        return {"assets": assets, "tag_name": "v0.3.6"}

    def app(self, method="appimage", regex=""):
        return {"name": "ds-bot", "repo": "owner/repo", "install_method": method, "asset_regex": regex}

    def test_format_without_a_matching_asset(self):
        release = self.release([self.asset("DS-Bot-0.3.6-linux-amd64.deb", "sha256:aaa")])
        with self.assertRaises(self.helper.UpdaterError) as caught:
            self.helper.select_asset(self.app("pacman"), release)
        self.assertEqual(caught.exception.code, "asset_none")

    def test_same_digest_under_two_names_is_one_asset(self):
        digest = "sha256:bbb"
        release = self.release([
            self.asset("DS-Bot-0.3.6-linux-x86_64.AppImage", digest),
            self.asset("DS-Bot-linux-x86_64.AppImage", digest),
        ])
        chosen = self.helper.select_asset(self.app("appimage"), release)
        self.assertEqual(chosen["name"], "DS-Bot-0.3.6-linux-x86_64.AppImage")

    def test_different_digests_stay_ambiguous(self):
        release = self.release([
            self.asset("DS-Bot-0.3.6-linux-x86_64.AppImage", "sha256:bbb"),
            self.asset("DS-Bot-linux-x86_64.AppImage", "sha256:ccc"),
        ])
        with self.assertRaises(self.helper.UpdaterError) as caught:
            self.helper.select_asset(self.app("appimage"), release)
        self.assertEqual(caught.exception.code, "asset_ambiguous")

    def test_version_detection_reports_a_missing_package(self):
        with self.assertRaises(self.helper.UpdaterError) as caught:
            self.helper.detect_installed_version(
                "pacman", "", "", "https://github.com/owner/definitely-not-installed-xyz")
        self.assertEqual(caught.exception.code, "package_not_installed")


class FakeServer:
    """A one-shot HTTP server whose reply is produced by `handler`."""

    def __init__(self, handler):
        self.requests = []
        handler_fn = handler

        class RequestHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.server.requests.append(dict(self.headers))
                handler_fn(self)
        self.httpd = HTTPServer(("127.0.0.1", 0), RequestHandler)
        self.httpd.requests = self.requests
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.httpd.shutdown()
        self.httpd.server_close()


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_helper()
        self.temp = tempfile.TemporaryDirectory()
        self.helper.SETTINGS_FILE = Path(self.temp.name) / "settings.json"
        self.helper._settings_cache = {"key": None, "value": None}
        self.helper.secret_lookup = lambda key: ""
        os.environ["GH_TOKEN"] = "test-token"

    def tearDown(self):
        os.environ.pop("GH_TOKEN", None)
        self.temp.cleanup()

    def asset(self, url, size, digest=""):
        return {"name": "app.pkg.tar.zst", "size": size,
                "browser_download_url": url, "digest": digest}

    def test_cross_host_redirect_drops_the_token(self):
        seen = {}

        def target(handler):
            seen.update({k.lower(): v for k, v in handler.headers.items()})
            body = b"payload"
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)

        with FakeServer(target) as backend:
            def redirect(handler):
                handler.send_response(302)
                # A different host name, so the redirect counts as cross-host
                # even though it lands on this machine.
                handler.send_header("Location", backend.url.replace("127.0.0.1", "localhost") + "/asset")
                handler.end_headers()

            with FakeServer(redirect) as front:
                destination = Path(self.temp.name) / "out" / "app.pkg.tar.zst"
                self.helper.download_asset(self.asset(front.url, 7), destination)
                self.assertTrue(destination.exists())
                self.assertNotIn("authorization", seen)

    def test_oversized_stream_is_cut_off(self):
        def handler(request):
            request.send_response(200)
            request.send_header("Content-Length", "50")
            request.end_headers()
            request.wfile.write(b"x" * 50)

        with FakeServer(handler) as server:
            destination = Path(self.temp.name) / "out" / "app.pkg.tar.zst"
            with self.assertRaises(self.helper.UpdaterError) as caught:
                self.helper.download_asset(self.asset(server.url, 10), destination)
            self.assertEqual(caught.exception.code, "asset_too_large")
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name(destination.name + ".part").exists())

    def test_digest_mismatch_is_rejected(self):
        body = b"payload"

        def handler(request):
            request.send_response(200)
            request.send_header("Content-Length", str(len(body)))
            request.end_headers()
            request.wfile.write(body)

        with FakeServer(handler) as server:
            destination = Path(self.temp.name) / "out" / "app.pkg.tar.zst"
            with self.assertRaises(self.helper.UpdaterError) as caught:
                self.helper.download_asset(self.asset(server.url, len(body), "sha256:" + "0" * 64), destination)
            self.assertEqual(caught.exception.code, "digest_mismatch")


class SettingsServiceTests(unittest.TestCase):
    def test_service_keeps_serving_after_bad_requests(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ)
            env["HOME"] = home
            env["PATH"] = home  # no secret-tool, no gh: both are optional
            payload = "\n".join([
                "not json",
                "[]",
                json.dumps({"action": "save-interval", "hours": 3}),
                json.dumps({"action": "get"}),
                "",
            ])
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "settings-service"],
                input=payload, capture_output=True, text=True, timeout=60, env=env,
            )
            lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(lines), 4)
            self.assertFalse(lines[0]["ok"])
            self.assertFalse(lines[1]["ok"])
            self.assertTrue(lines[2]["ok"])
            self.assertEqual(lines[2]["check_interval_hours"], 3)
            self.assertTrue(lines[3]["ok"])
            self.assertEqual(lines[3]["check_interval_hours"], 3)

            settings = json.loads((Path(home) / ".config/omarchy/github-release-updater/settings.json").read_text())
            self.assertEqual(settings["check_interval_hours"], 3)


class QmlSafetyTests(unittest.TestCase):
    """Every Text element the plugin owns must render untrusted data as text."""

    def test_every_text_element_is_plain_text(self):
        failures = []
        for qml in sorted(PLUGIN_DIR.glob("*.qml")):
            source = qml.read_text(encoding="utf-8")
            for match in re.finditer(r"\bText\s*\{", source):
                start = match.end()
                depth = 1
                index = start
                while index < len(source) and depth > 0:
                    if source[index] == "{":
                        depth += 1
                    elif source[index] == "}":
                        depth -= 1
                    index += 1
                if "textFormat" not in source[start:index]:
                    line = source.count("\n", 0, match.start()) + 1
                    failures.append(f"{qml.name}:{line}")
        self.assertEqual(failures, [], "Text elements without textFormat: Text.PlainText")

    def test_manifest_is_bar_widget_only(self):
        manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["kinds"], ["bar-widget"])
        self.assertEqual(manifest["entryPoints"], {"barWidget": "BarWidget.qml"})
        for path in manifest["entryPoints"].values():
            self.assertTrue((PLUGIN_DIR / path).is_file())

    def test_translation_catalogs_parse(self):
        for catalog in sorted((PLUGIN_DIR / "i18n").glob("*.json")):
            data = json.loads(catalog.read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict)
            self.assertIn("panel.title", data)


if __name__ == "__main__":
    unittest.main()
