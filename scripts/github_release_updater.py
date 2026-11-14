#!/usr/bin/env python3
"""Check and install GitHub Release updates from an explicit app manifest."""

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import quote, urlsplit


API = "https://api.github.com/repos/{repo}/releases/latest"
RELEASES_API = "https://api.github.com/repos/{repo}/releases?per_page=100"
DEFAULT_MANIFEST = Path.home() / ".config/omarchy/github-release-updater/apps.json"
SETTINGS_FILE = Path.home() / ".config/omarchy/github-release-updater/settings.json"
SECRET_SERVICE = "com.aywuyan.github-release-updater"
USER_AGENT = "omarchy-github-release-updater"

# Bounds for data that comes from the network. GitHub release metadata is small;
# an asset may be large, but never larger than the size the API reported for it.
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ASSET_BYTES = 8 * 1024 * 1024 * 1024
DOWNLOAD_CHUNK = 1024 * 1024

# App names double as directory names under the download directory, so they are
# deliberately narrow: no slash, no leading dot or dash, no "..".
APP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+@-]{0,63}$")
PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@-]{0,127}$")
REPO_PART_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})$")
INTERVAL_HOURS = (0, 1, 3, 6, 12, 24, 48, 168)

STATUS_TEXT = {
    "update_available": "update available",
    "up_to_date": "up to date",
    "installed_newer": "installed version is newer",
    "compare_manually": "compare manually",
    "check_failed": "check failed",
}

_token_cache = {"loaded": False, "value": ""}
_settings_cache = {"key": None, "value": None}


class UpdaterError(Exception):
    """A failure with a stable machine code the UI can translate."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise UpdaterError(code, message)


def error_code(exc):
    if isinstance(exc, UpdaterError):
        return exc.code
    if isinstance(exc, urllib.error.HTTPError):
        return http_error_code(exc)
    if isinstance(exc, urllib.error.URLError):
        return "network"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(exc, json.JSONDecodeError):
        return "bad_response"
    if isinstance(exc, FileNotFoundError):
        return "file_not_found"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, subprocess.CalledProcessError):
        return "command_failed"
    if isinstance(exc, OSError):
        return "os_error"
    return "internal"


def http_error_code(exc):
    if exc.code == 401:
        return "unauthorized"
    if exc.code == 404:
        return "not_found"
    if exc.code == 403:
        remaining = (exc.headers or {}).get("x-ratelimit-remaining")
        if str(remaining) == "0":
            return "rate_limited"
        return "forbidden"
    if exc.code == 407:
        return "proxy_auth_required"
    return "http_error"


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects without handing the GitHub token to another host.

    urllib copies every request header except content-length/content-type onto
    the redirected request, Authorization included. A release asset redirects
    to a storage host, which has no business seeing the token.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        if (urlsplit(newurl).hostname or "").lower() != (urlsplit(req.full_url).hostname or "").lower():
            new.remove_header("Authorization")
            new.remove_header("authorization")
        return new


def open_url(request, timeout):
    apply_proxy_settings()
    settings = read_settings()
    address = settings.get("proxy_address", "")
    handlers = [SafeRedirectHandler()]
    if settings.get("proxy_enabled") and address:
        proxy = f"http://{address}"
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(request, timeout=timeout)


def github_json(url):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with open_url(request, timeout=30) as response:
        raw = response.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        fail("response_too_large", "GitHub returned more data than this plugin accepts")
    return json.loads(raw)


def load_apps(manifest):
    # A fresh install has no manifest yet. That is "nothing configured", not a
    # failure: the panel offers the Add form and the bar stays calm.
    if not manifest.exists():
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail("manifest_invalid", f"app manifest is not valid JSON: {exc}")
    apps = data.get("apps", [])
    if not isinstance(apps, list):
        fail("manifest_invalid", "manifest field 'apps' must be a list")
    for app in apps:
        if not isinstance(app, dict):
            fail("manifest_invalid", "every manifest entry must be an object")
        for field in ("name", "repo", "installed_version", "install_method"):
            if not app.get(field):
                fail("app_missing_field", f"application entry is missing {field!r}")
        app.setdefault("asset_regex", "")
        app.setdefault("release_channel", "stable")
        app.setdefault("installed_build_id", "")
        app.setdefault("package", "")
        app.setdefault("target", "")
        if not isinstance(app["asset_regex"], str):
            fail("app_bad_regex", "application field 'asset_regex' must be a string")
        if app["asset_regex"]:
            try:
                re.compile(app["asset_regex"])
            except re.error as exc:
                fail("app_bad_regex", f"invalid asset regular expression: {exc}")
        if app["release_channel"] not in ("stable", "development"):
            fail("app_bad_channel", "application field 'release_channel' must be stable or development")
        if app["install_method"] not in ("pacman", "appimage"):
            fail("app_bad_method", "application field 'install_method' must be pacman or appimage")
        validate_app_name(app["name"])
        validate_package_name(app["package"], required=False)
        if not isinstance(app["installed_build_id"], str):
            fail("manifest_invalid", "application field 'installed_build_id' must be a string")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", app["repo"]):
            fail("app_bad_repo", f"invalid GitHub repository: {app['repo']}")
        validate_repo_parts(*app["repo"].split("/", 1))
        if app["install_method"] == "appimage":
            target = Path(str(app.get("target") or "")).expanduser()
            if not str(app.get("target") or "").strip() or not target.is_absolute():
                fail("app_missing_target", f"{app['name']}: AppImage entries need an absolute target path")
    return apps


def validate_app_name(name):
    text = str(name or "")
    if text != text.strip() or not APP_NAME_RE.fullmatch(text) or text in (".", ".."):
        fail(
            "app_bad_name",
            "app names must start with a letter or digit and use only letters, digits, spaces, . _ + @ -",
        )
    return text


def validate_package_name(name, required):
    text = str(name or "").strip()
    if not text:
        if required:
            fail("app_missing_package", "a Pacman package name is required")
        return ""
    if not PACKAGE_RE.fullmatch(text):
        fail("app_bad_package", "Pacman package names use only letters, digits, . _ + @ -")
    return text


def validate_repo_parts(owner, repository):
    for part in (owner, repository):
        if part in (".", "..") or not REPO_PART_RE.fullmatch(part):
            fail("app_bad_repo", "repository names use only letters, digits, . _ -")


def github_token():
    if _token_cache["loaded"]:
        return _token_cache["value"]
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        token = secret_lookup("github-token")
    if not token and shutil.which("gh"):
        try:
            result = subprocess.run(
                ["gh", "auth", "token"], check=True, capture_output=True,
                text=True, timeout=5,
            )
            token = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            token = ""
    _token_cache["value"] = token or ""
    _token_cache["loaded"] = True
    return _token_cache["value"]


def secret_lookup(key):
    if not shutil.which("secret-tool"):
        return ""
    try:
        result = subprocess.run(
            ["secret-tool", "lookup", "service", SECRET_SERVICE, "key", key],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.rstrip("\r\n") if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def secret_store(key, value):
    if not shutil.which("secret-tool"):
        fail("secret_tool_missing", "secret-tool is required to store credentials in the desktop keyring")
    if any(ord(char) < 32 for char in value):
        fail("secret_control_chars", "value contains unsupported control characters")
    subprocess.run(
        ["secret-tool", "store", "--label=GitHub Release Updater", "service", SECRET_SERVICE, "key", key],
        input=value + "\n", capture_output=True, text=True, timeout=20, check=True,
    )


def secret_clear(key):
    if not shutil.which("secret-tool"):
        fail("secret_tool_missing", "secret-tool is required to manage credentials in the desktop keyring")
    subprocess.run(
        ["secret-tool", "clear", "service", SECRET_SERVICE, "key", key],
        capture_output=True, text=True, timeout=10, check=True,
    )


def default_settings():
    return {
        "language": "auto",
        "proxy_address": "",
        "proxy_enabled": False,
        "no_proxy": "",
        "check_interval_hours": 6,
    }


def read_settings():
    try:
        stat = SETTINGS_FILE.stat()
        cache_key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        cache_key = None
    if cache_key is not None and _settings_cache["key"] == cache_key:
        return _settings_cache["value"]

    data = default_settings()
    legacy_proxy = False
    if cache_key is not None:
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            data.update({key: raw[key] for key in data if key in raw})
    try:
        data["check_interval_hours"] = clamp_interval(data.get("check_interval_hours"))
    except (TypeError, ValueError):
        data["check_interval_hours"] = 6
    data["proxy_address"] = str(data.get("proxy_address") or "").strip()
    data["proxy_enabled"] = data.get("proxy_enabled") is True and bool(data["proxy_address"])
    data["no_proxy"] = str(data.get("no_proxy") or "")
    if not data["proxy_address"]:
        address = legacy_proxy_address()
        if address:
            data["proxy_address"] = address
            data["proxy_enabled"] = True
            legacy_proxy = True
    data["legacy_proxy"] = legacy_proxy
    _settings_cache["key"] = cache_key
    _settings_cache["value"] = data
    return data


def legacy_proxy_address():
    for key in ("https-proxy", "http-proxy"):
        raw = secret_lookup(key)
        if not raw:
            continue
        parsed = urlsplit(raw)
        if parsed.scheme in ("http", "https") and parsed.hostname in ("127.0.0.1", "0", "0.0.0.0") and parsed.port:
            host = "0" if parsed.hostname in ("0", "0.0.0.0") else "127.0.0.1"
            return f"{host}:{parsed.port}"
    return ""


def clamp_interval(value):
    hours = int(value)
    if hours not in INTERVAL_HOURS:
        hours = 6
    return hours


def write_settings(data):
    invalidate_settings_cache()
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(SETTINGS_FILE.parent, 0o700)
    temp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump({
            "no_proxy": data.get("no_proxy", ""),
            "proxy_address": data.get("proxy_address", ""),
            "proxy_enabled": data.get("proxy_enabled", False),
            "check_interval_hours": clamp_interval(data.get("check_interval_hours", 6)),
        }, out, ensure_ascii=False, indent=2)
        out.write("\n")
    os.chmod(temp, 0o600)
    temp.replace(SETTINGS_FILE)


def invalidate_settings_cache():
    _settings_cache["key"] = None
    _settings_cache["value"] = None


def valid_proxy_url(value):
    value = value.strip()
    if not value:
        fail("proxy_invalid", "enter a local proxy address")
    if any(ord(char) < 32 for char in value):
        fail("proxy_invalid", "proxy address contains control characters")
    match = re.fullmatch(r"(127\.0\.0\.1|0):([0-9]{1,5})", value)
    if not match:
        fail("proxy_invalid", "enter a local proxy as 127.0.0.1:PORT or 0:PORT")
    port = int(match.group(2))
    if not 1 <= port <= 65535:
        fail("proxy_port", "proxy port must be between 1 and 65535")
    return value


def test_proxy_address(value):
    address = valid_proxy_url(value)
    proxy = f"http://{address}"
    opener = urllib.request.build_opener(
        SafeRedirectHandler(),
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
    )
    request = urllib.request.Request("https://api.github.com/zen", headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=12) as response:
            return {"ok": True, "http_status": response.status, "reached": True}
    except urllib.error.HTTPError as exc:
        if exc.code == 407:
            fail("proxy_auth_required", "the local proxy requires authentication")
        if 400 <= exc.code < 500:
            # Reaching GitHub through the proxy is what the test proves; a 4xx
            # means the proxy works but GitHub refused this client.
            return {"ok": True, "http_status": exc.code, "reached": True}
        fail("proxy_unreachable", f"proxy connection failed with HTTP {exc.code}")
    except urllib.error.URLError as exc:
        fail("proxy_unreachable", f"proxy connection failed: {exc.reason}")
    except OSError as exc:
        fail("proxy_unreachable", f"proxy connection failed: {exc}")


def apply_proxy_settings():
    settings = read_settings()
    no_proxy = settings.get("no_proxy", "")
    if no_proxy:
        os.environ["NO_PROXY"] = no_proxy
        os.environ["no_proxy"] = no_proxy


def plugin_version():
    try:
        manifest = Path(__file__).resolve().parent.parent / "manifest.json"
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", ""))
    except (OSError, json.JSONDecodeError):
        return ""


def get_settings_status():
    settings = read_settings()
    if settings.get("legacy_proxy"):
        write_settings(settings)
        for key in ("http-proxy", "https-proxy"):
            try:
                secret_clear(key)
            except (OSError, ValueError, UpdaterError, subprocess.SubprocessError):
                pass
        settings = read_settings()
    http_proxy = secret_lookup("http-proxy")
    https_proxy = secret_lookup("https-proxy")
    gh_available = shutil.which("gh") is not None
    gh_authenticated = False
    if gh_available:
        try:
            result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
            gh_authenticated = result.returncode == 0 and bool(result.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        "token_saved": bool(secret_lookup("github-token")),
        "environment_token_available": bool(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")),
        "gh_available": gh_available,
        "gh_authenticated": gh_authenticated,
        "proxy_saved": bool(settings.get("proxy_address")),
        "proxy_address": settings.get("proxy_address", ""),
        "proxy_enabled": settings.get("proxy_enabled", False),
        "no_proxy": settings.get("no_proxy", ""),
        "uses_environment_proxy": not bool(http_proxy or https_proxy),
        "check_interval_hours": settings.get("check_interval_hours", 6),
        "manifest_path": str(DEFAULT_MANIFEST),
        "settings_path": str(SETTINGS_FILE),
        "plugin_version": plugin_version(),
    }


def process_settings_action(payload):
    if not isinstance(payload, dict):
        fail("bad_request", "settings requests must be JSON objects")
    action = payload.get("action")
    if action == "get":
        return {"ok": True, **get_settings_status()}
    if action == "save-token":
        token = str(payload.get("token", ""))
        if not token.strip():
            fail("token_required", "enter a GitHub token")
        secret_store("github-token", token.strip())
        _token_cache["loaded"] = False
        return {"ok": True, "message": "GitHub token saved in the desktop keyring"}
    if action == "clear-token":
        secret_clear("github-token")
        _token_cache["loaded"] = False
        return {"ok": True, "message": "GitHub token removed from the desktop keyring"}
    if action == "save-proxy":
        proxy_address = valid_proxy_url(str(payload.get("proxy_address", ""))) if str(payload.get("proxy_address", "")).strip() else ""
        proxy_enabled = payload.get("proxy_enabled") is True and bool(proxy_address)
        settings = read_settings()
        settings["proxy_address"] = proxy_address
        settings["proxy_enabled"] = proxy_enabled
        write_settings(settings)
        for key in ("http-proxy", "https-proxy"):
            try:
                secret_clear(key)
            except (OSError, ValueError, UpdaterError, subprocess.SubprocessError):
                pass
        return {"ok": True, "action": action, "proxy_address": proxy_address,
                "proxy_saved": bool(proxy_address), "proxy_enabled": proxy_enabled}
    if action == "test-proxy":
        result = test_proxy_address(str(payload.get("proxy_address", "")))
        return {"ok": True, "action": action, **result}
    if action == "set-proxy-enabled":
        settings = read_settings()
        settings["proxy_enabled"] = payload.get("enabled") is True and bool(settings.get("proxy_address"))
        write_settings(settings)
        return {"ok": True, "action": action, "proxy_enabled": settings["proxy_enabled"]}
    if action == "clear-proxy":
        settings = read_settings()
        settings["proxy_address"] = ""
        settings["proxy_enabled"] = False
        write_settings(settings)
        for key in ("http-proxy", "https-proxy"):
            try:
                secret_clear(key)
            except (OSError, ValueError, UpdaterError, subprocess.SubprocessError):
                pass
        return {"ok": True, "action": action, "proxy_address": "", "proxy_saved": False, "proxy_enabled": False}
    if action == "save-interval":
        try:
            hours = clamp_interval(payload.get("hours"))
        except (TypeError, ValueError):
            fail("interval_invalid", "the check interval must be one of the offered values")
        settings = read_settings()
        settings["check_interval_hours"] = hours
        write_settings(settings)
        return {"ok": True, "action": action, "check_interval_hours": hours}
    fail("unknown_settings_action", "unknown settings action")


def settings_service():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            result = process_settings_action(payload)
            result["action"] = payload.get("action", "") if isinstance(payload, dict) else ""
        except Exception as exc:  # keep serving: one bad request must not kill the panel
            result = {
                "ok": False,
                "code": error_code(exc),
                "error": str(exc) or exc.__class__.__name__,
                "action": "",
            }
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def select_asset(app, release):
    asset_regex = str(app.get("asset_regex", "")).strip()
    if asset_regex:
        pattern = re.compile(asset_regex)
        matches = [a for a in release.get("assets", []) if pattern.fullmatch(a["name"])]
    else:
        matches = auto_detect_assets(release.get("assets", []), app["install_method"])
    if len(matches) != 1:
        choices = ", ".join(a["name"] for a in matches[:8])
        suffix = f": {choices}" if choices else ""
        if asset_regex:
            fail("asset_regex_no_match", f"expected one asset matching {asset_regex!r}; found {len(matches)}{suffix}")
        fail(
            "asset_ambiguous",
            f"could not automatically choose one compatible release asset; found {len(matches)}{suffix}. "
            "Enter an optional filename regex to choose one",
        )
    return matches[0]


def auto_detect_assets(assets, install_method):
    machine = platform.machine().lower()
    arch_groups = {
        "x86_64": {"x86_64", "amd64", "x64"},
        "aarch64": {"aarch64", "arm64"},
        "armv7l": {"armv7", "armv7l", "armhf"},
    }
    expected_arches = arch_groups.get(machine, {machine})
    ignored = ("source", "checksum", "sha256", "sha512", ".sig", ".asc", "debug", "symbols", ".zsync", ".blockmap")
    candidates = []
    explicit_matches = []
    for asset in assets:
        filename = asset.get("name", "")
        lower = filename.lower()
        if any(token in lower for token in ignored):
            continue
        if install_method == "appimage":
            if not lower.endswith(".appimage"):
                continue
        elif install_method == "pacman":
            if not re.search(r"\.pkg\.tar\.(?:zst|xz)$", lower):
                continue
        else:
            continue

        known_arches = {alias for aliases in arch_groups.values() for alias in aliases}
        marked_arches = {
            alias for alias in known_arches
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lower)
        }
        if marked_arches and not (marked_arches & expected_arches):
            continue
        if re.search(r"(?:^|[._-])(windows|win64|win32|macos|darwin|osx)(?:$|[._-])", lower):
            continue
        candidates.append(asset)
        if marked_arches & expected_arches:
            explicit_matches.append(asset)
    return explicit_matches or candidates


def release_for(app):
    if app.get("release_channel", "stable") == "development":
        releases = github_json(RELEASES_API.format(repo=app["repo"]))
        releases = [release for release in releases if not release.get("draft") and release.get("prerelease")]
        if not releases:
            fail("no_prereleases", "GitHub repository has no published developer prereleases")
        return max(releases, key=lambda item: item.get("created_at", ""))
    release = github_json(API.format(repo=app["repo"]))
    if release.get("draft") or release.get("prerelease"):
        fail("latest_is_prerelease", "GitHub latest endpoint returned a draft or prerelease")
    return release


def compare_versions(current_version, latest_version):
    """Compare two release versions. Returns 1, 0, -1, or None if unreadable.

    Handles pacman epochs and pkgrels, a leading "v", semver build metadata, and
    pre-release suffixes (1.2.3-rc1 is older than 1.2.3).
    """
    current = version_parts(current_version)
    latest = version_parts(latest_version)
    if current is None or latest is None:
        return None
    current_release, current_pre = current
    latest_release, latest_pre = latest
    width = max(len(current_release), len(latest_release))
    current_release = current_release + (0,) * (width - len(current_release))
    latest_release = latest_release + (0,) * (width - len(latest_release))
    if latest_release != current_release:
        return 1 if latest_release > current_release else -1
    if current_pre == latest_pre:
        return 0
    if latest_pre == "":
        return 1
    if current_pre == "":
        return -1
    current_key = prerelease_key(current_pre)
    latest_key = prerelease_key(latest_pre)
    if latest_key == current_key:
        return 0
    return 1 if latest_key > current_key else -1


def version_parts(version):
    text = str(version or "").strip()
    if not text:
        return None
    # pacman epoch: "1:2.3.4-1"
    text = re.sub(r"^\d+:", "", text)
    # pkgrel: a numeric trailing -N is a package revision, not a version.
    text = re.sub(r"-(\d+)$", "", text)
    # semver build metadata never affects ordering
    text = text.split("+", 1)[0]
    match = re.match(r"^\s*[vV]?(\d+(?:\.\d+)*)(.*)$", text)
    if not match:
        return None
    release = tuple(map(int, match.group(1).split(".")))
    remainder = match.group(2).strip()
    remainder = remainder.lstrip(".-_~")
    return release, remainder.lower()


def prerelease_key(text):
    key = []
    for token in re.findall(r"[a-z]+|\d+", text.lower()):
        if token.isdigit():
            key.append((1, int(token)))
        else:
            key.append((0, token))
    return key


def installed_version_for(app, fallback=None):
    if app.get("install_method") == "pacman":
        package = app.get("package") or app["name"]
        try:
            result = subprocess.run(
                ["pacman", "-Q", package], check=True, capture_output=True,
                text=True, timeout=8,
            )
            fields = result.stdout.split()
            if len(fields) >= 2:
                return fields[-1]
        except (OSError, subprocess.SubprocessError):
            pass
    return str(fallback if fallback is not None else app["installed_version"])


def release_commit_for(app, release):
    tag = str(release.get("tag_name", ""))
    if not tag:
        return ""
    try:
        commit = github_json(f"https://api.github.com/repos/{app['repo']}/commits/{quote(tag, safe='')}")
        sha = str(commit.get("sha", "")).lower()
        return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else ""
    except (OSError, ValueError, urllib.error.URLError):
        return ""


def infer_installed_channel(app, current_version):
    channel = app.get("installed_release_channel")
    if channel in ("stable", "development"):
        return channel
    if app.get("install_method") == "pacman" and re.search(r"(?:^|[_-])rolling[_-]", str(current_version).lower()):
        return "development"
    return ""


def inspect(app):
    release = release_for(app)
    tag = release.get("tag_name", "")
    asset = select_asset(app, release)
    channel = app.get("release_channel", "stable")
    current_version = installed_version_for(app)
    asset_version_match = re.search(r"(?<!\d)(\d+(?:\.\d+){1,3})(?!\d)", asset["name"])
    latest_version = asset_version_match.group(1) if channel == "development" and asset_version_match else tag
    build_id = asset.get("digest") or asset.get("updated_at") or str(asset.get("id", ""))
    comparison = compare_versions(current_version, latest_version)
    installed_channel = infer_installed_channel(app, current_version)
    if installed_channel and installed_channel != channel:
        status = "update_available"
    elif channel == "development":
        if app.get("installed_build_id"):
            status = "up_to_date" if app["installed_build_id"] == build_id else "update_available"
        else:
            release_commit = release_commit_for(app, release)
            installed_commit = re.search(r"(?:^|[_-])rolling_([0-9a-f]{7,40})(?:[-+.]|$)", current_version.lower())
            same_developer_build = bool(
                release_commit and installed_commit
                and release_commit.startswith(installed_commit.group(1))
            )
            # Pacman developer packages may record the rolling commit in their installed version.
            status = "up_to_date" if same_developer_build else "update_available"
    else:
        status = "compare_manually" if comparison is None else (
            "update_available" if comparison > 0 else
            "up_to_date" if comparison == 0 else "installed_newer"
        )
    return {
        "name": app["name"],
        "repo": app["repo"],
        "current": current_version,
        "latest": latest_version if channel == "development" else tag,
        "release_channel": channel,
        "build_id": build_id,
        "status": status,
        "release_url": release.get("html_url", ""),
        "asset": asset["name"],
        "asset_url": asset["browser_download_url"],
        "asset_size": asset["size"],
        "digest": asset.get("digest", ""),
        "install_method": app["install_method"],
        "error_code": "",
        "error": "",
        "release": release,
        "asset_info": asset,
    }


def print_report(result):
    print(f"{result['name']}: {result['current']} -> {result['latest']} ({STATUS_TEXT.get(result['status'], result['status'])})")
    print(f"  repository: {result['repo']}")
    print(f"  asset: {result['asset']} ({result['asset_size']} bytes)")
    print(f"  SHA-256: {result['digest'] or 'not provided by GitHub API'}")
    print(f"  install method: {result['install_method']}")


def download_asset(asset, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(asset["browser_download_url"], headers=headers)
    temp = destination.with_name(destination.name + ".part")
    declared = int(asset.get("size") or 0)
    limit = declared if declared > 0 else MAX_ASSET_BYTES
    if limit <= 0 or limit > MAX_ASSET_BYTES:
        fail("asset_too_large", "release asset is larger than this plugin will download")
    total = declared
    chunk_size = DOWNLOAD_CHUNK

    def show_progress(downloaded, finished=False):
        fraction = min(downloaded / total, 1.0) if total else 0.0
        bar_width = 24
        filled = round(bar_width * fraction)
        bar = "█" * filled + "░" * (bar_width - filled) if total else ""
        size_text = f"{format_size(downloaded)} / {format_size(total)}" if total else f"{format_size(downloaded)}"
        percent_text = f" {fraction * 100:5.1f}%" if total else ""
        end = "\n" if finished else "\r"
        print(f"  Downloading [{bar}] {size_text}{percent_text}", end=end, file=sys.stderr, flush=True)

    try:
        with open_url(request, timeout=60) as response, temp.open("wb") as out:
            print(f"  Downloading {asset['name']}", file=sys.stderr, flush=True)
            length_header = response.headers.get("Content-Length")
            if length_header and length_header.isdigit() and int(length_header) > limit:
                fail("asset_too_large", "release asset is larger than its release metadata declares")
            downloaded = 0
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > limit:
                    fail("asset_too_large", "release asset is larger than its release metadata declares")
                out.write(chunk)
                show_progress(downloaded)
            show_progress(downloaded, finished=True)
        size = temp.stat().st_size
        if declared and size != declared:
            fail("download_size_mismatch", f"download size mismatch: got {size}, expected {declared}")
        digest = asset.get("digest")
        if digest:
            algorithm, expected = digest.split(":", 1)
            if algorithm.lower() != "sha256":
                fail("digest_unsupported", f"unsupported GitHub digest algorithm: {algorithm}")
            print("  Verifying SHA-256…", file=sys.stderr, flush=True)
            hasher = hashlib.sha256()
            with temp.open("rb") as downloaded:
                for chunk in iter(lambda: downloaded.read(DOWNLOAD_CHUNK), b""):
                    hasher.update(chunk)
            actual = hasher.hexdigest()
            if actual.lower() != expected.lower():
                fail("digest_mismatch", "SHA-256 verification failed")
        temp.replace(destination)
    except Exception:
        print(file=sys.stderr, flush=True)
        temp.unlink(missing_ok=True)
        raise
    return destination


def format_size(size):
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024


def install(app, asset, path):
    method = app["install_method"]
    if method == "pacman":
        if not path.name.endswith((".pkg.tar.zst", ".pkg.tar.xz")):
            fail("install_unsupported_asset", "pacman method requires a .pkg.tar.zst or .pkg.tar.xz asset")
        try:
            subprocess.run(["sudo", "--", "pacman", "-U", str(path)], check=True)
        except subprocess.CalledProcessError as exc:
            fail("install_failed", f"pacman exited with status {exc.returncode}")
    elif method == "appimage":
        target = Path(app.get("target", "")).expanduser()
        if not target.is_absolute() or not target.parent.is_dir():
            fail("appimage_target", "AppImage target must be an absolute path in an existing directory")
        if not path.name.endswith(".AppImage"):
            fail("install_unsupported_asset", "AppImage method requires an asset ending in .AppImage")
        os.chmod(path, 0o755)
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = target.with_name(target.name + ".backup-" + stamp)
            shutil.copy2(target, backup)
            print(f"  backup: {backup}")
        staged = target.with_name(target.name + ".new")
        shutil.copy2(path, staged)
        os.chmod(staged, 0o755)
        os.replace(staged, target)
        print(f"  installed: {target}")
    else:
        fail("install_unsupported_asset", f"unsupported install method: {method}")


def manifest_write(manifest, data):
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temp = manifest.with_name(manifest.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(manifest)


def persist_version(manifest, app, version, build_id=""):
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("manifest_invalid", f"could not update the app manifest: {exc}")
    for item in data.get("apps", []):
        if item.get("name") == app["name"] and item.get("repo") == app["repo"]:
            item["installed_version"] = version
            channel = app.get("release_channel", "stable")
            item["installed_release_channel"] = channel
            if channel == "development":
                item["installed_build_id"] = build_id
            else:
                item.pop("installed_build_id", None)
            break
    manifest_write(manifest, data)


def parse_release_url(url):
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ("github.com", "www.github.com"):
        fail("url_invalid", "enter an https://github.com/owner/repository URL")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        fail("url_invalid", "URL must include both owner and repository")
    owner, repository = parts[0], parts[1]
    repository = repository.removesuffix(".git")
    validate_repo_parts(owner, repository)
    if len(parts) > 2:
        valid_release_path = (
            (len(parts) == 3 and parts[2] == "releases")
            or (len(parts) == 4 and parts[2:] == ["releases", "latest"])
            or (len(parts) == 5 and parts[2] == "releases" and parts[3] == "tag")
        )
        if not valid_release_path:
            fail("url_path", "use a repository URL, /releases/latest, or /releases/tag/<tag>")
    return owner, repository


def add_release_app(manifest, url, version, asset_regex, install_method, name, target,
                    release_channel="stable", package=""):
    owner, repository = parse_release_url(url)
    if not version.strip():
        fail("version_required", "installed version is required")
    if asset_regex.strip():
        try:
            re.compile(asset_regex)
        except re.error as exc:
            fail("app_bad_regex", f"invalid asset regular expression: {exc}")
    if install_method not in ("pacman", "appimage"):
        fail("app_bad_method", "install method must be pacman or appimage")
    if release_channel not in ("stable", "development"):
        fail("app_bad_channel", "release channel must be stable or development")
    app_name = validate_app_name(name.strip() or repository)
    package_name = validate_package_name(package, required=False)
    app = {
        "name": app_name,
        "repo": f"{owner}/{repository}",
        "installed_version": version.strip(),
        "asset_regex": asset_regex,
        "install_method": install_method,
        "release_channel": release_channel,
    }
    if package_name:
        app["package"] = package_name
    if install_method == "appimage":
        target_path = Path(target).expanduser()
        if not target_path.is_absolute():
            fail("appimage_target", "AppImage target must be an absolute path")
        app["target"] = str(target_path)

    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail("manifest_invalid", f"app manifest is not valid JSON: {exc}")
    else:
        data = {"apps": []}
    apps = data.get("apps")
    if not isinstance(apps, list):
        fail("manifest_invalid", "manifest field 'apps' must be a list")
    if any(existing.get("name") == app_name or existing.get("repo", "").lower() == app["repo"].lower() for existing in apps):
        fail("duplicate_app", "an app with this name or repository is already registered")
    apps.append(app)
    manifest_write(manifest, data)
    return app


def detect_installed_version(install_method, name, target, url):
    if install_method == "pacman":
        package = validate_package_name(name)
        if not package:
            parsed = urlsplit(url.strip())
            parts = [part for part in parsed.path.strip("/").split("/") if part]
            package = parts[1].removesuffix(".git") if len(parts) >= 2 else ""
        package = validate_package_name(package, required=False)
        if not package:
            fail("package_required", "enter an app/package name or GitHub repository first")
        try:
            result = subprocess.run(["pacman", "-Q", package], check=True, capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired as exc:
            raise UpdaterError("version_detect_timeout", "pacman did not answer within 10 seconds") from exc
        except subprocess.CalledProcessError as exc:
            raise UpdaterError("package_not_installed", f"package {package!r} is not installed") from exc
        fields = result.stdout.strip().split()
        if len(fields) < 2:
            fail("version_detect_failed", f"could not read the installed version for package {package!r}")
        return {"version": fields[-1], "source": f"pacman package {fields[0]}"}

    if install_method == "appimage":
        appimage = Path(target).expanduser()
        if not appimage.is_absolute() or not appimage.is_file():
            fail("appimage_target", "enter an existing absolute AppImage path first")
        env = os.environ.copy()
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        env["QT_QPA_PLATFORM"] = "offscreen"
        try:
            result = subprocess.run(
                [str(appimage), "--version"], capture_output=True, text=True,
                timeout=8, env=env, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise UpdaterError("version_detect_timeout", "the AppImage did not return a version within 8 seconds") from exc
        output = (result.stdout + "\n" + result.stderr).strip()
        match = re.search(r"(?<![A-Za-z0-9])v?(\d+\.\d+(?:\.\d+){0,3}(?:[-+][A-Za-z0-9.-]+)?)", output)
        if match:
            return {"version": match.group(1), "source": "AppImage --version"}
        fail("version_not_reported", "the AppImage did not report a version with --version; enter it manually")

    fail("app_bad_method", "install method must be pacman or appimage")


def remove_release_app(manifest, name):
    if not manifest.exists():
        fail("manifest_missing", "app manifest does not exist")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail("manifest_invalid", f"app manifest is not valid JSON: {exc}")
    apps = data.get("apps", [])
    if not isinstance(apps, list):
        fail("manifest_invalid", "manifest field 'apps' must be a list")
    remaining = [app for app in apps if app.get("name") != name]
    removed = len(apps) - len(remaining)
    if removed == 0:
        fail("app_not_found", f"no registered app named {name!r}")
    data["apps"] = remaining
    manifest_write(manifest, data)
    return removed


def set_release_channel(manifest, name, release_channel):
    if release_channel not in ("stable", "development"):
        fail("app_bad_channel", "release channel must be stable or development")
    if not manifest.exists():
        fail("manifest_missing", "app manifest does not exist")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail("manifest_invalid", f"app manifest is not valid JSON: {exc}")
    apps = data.get("apps", [])
    if not isinstance(apps, list):
        fail("manifest_invalid", "manifest field 'apps' must be a list")
    app = next((item for item in apps if item.get("name") == name), None)
    if app is None:
        fail("app_not_found", f"no registered app named {name!r}")
    app["release_channel"] = release_channel
    manifest_write(manifest, data)
    return release_channel


def json_result(payload):
    print(json.dumps(payload, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "download", "update", "add", "remove", "set-channel", "detect-version", "settings-service"))
    parser.add_argument("name", nargs="?", help="app name (omit for check)")
    parser.add_argument("--name", dest="app_name", default="", help="display name for add action")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dir", type=Path, default=Path.home() / "Downloads/omarchy-github-release-updater")
    parser.add_argument("--apply", action="store_true", help="install after an interactive confirmation")
    parser.add_argument("--all", action="store_true", help="update every configured app")
    parser.add_argument("--json", action="store_true", help="emit machine-readable check results")
    parser.add_argument("--url", default="", help="GitHub repository or Releases URL (add action)")
    parser.add_argument("--version", default="", help="currently installed version (add action)")
    parser.add_argument("--asset-regex", default="", help="optional regex selecting one release asset (add action)")
    parser.add_argument("--install-method", choices=("pacman", "appimage"), default="pacman")
    parser.add_argument("--target", default="", help="absolute installed path, required for AppImage")
    parser.add_argument("--release-channel", choices=("stable", "development"), default="stable")
    parser.add_argument("--package", default="", help="Pacman package name when it differs from the app name")
    args = parser.parse_args()

    try:
        if args.action == "settings-service":
            settings_service()
            return 0
        if args.action == "add":
            if not args.url or not args.version:
                fail("missing_arguments", "add requires --url and --version")
            app = add_release_app(args.manifest, args.url, args.version, args.asset_regex,
                                  args.install_method, args.app_name or args.name or "", args.target,
                                  args.release_channel, args.package)
            result = {"ok": True, "name": app["name"], "repo": app["repo"]}
            if args.json:
                json_result(result)
            else:
                print(f"Added {app['name']} ({app['repo']})")
            return 0
        if args.action == "remove":
            if not args.name:
                fail("missing_arguments", "remove requires an app name")
            removed = remove_release_app(args.manifest, args.name)
            result = {"ok": True, "name": args.name, "removed": removed}
            if args.json:
                json_result(result)
            else:
                print(f"Removed {args.name} from tracking")
            return 0
        if args.action == "set-channel":
            if not args.name:
                fail("missing_arguments", "set-channel requires an app name")
            channel = set_release_channel(args.manifest, args.name, args.release_channel)
            result = {"ok": True, "name": args.name, "release_channel": channel}
            if args.json:
                json_result(result)
            else:
                print(f"Set {args.name} channel to {channel}")
            return 0
        if args.action == "detect-version":
            detected = detect_installed_version(args.install_method, args.name or "", args.target, args.url)
            result = {"ok": True, **detected}
            if args.json:
                json_result(result)
            else:
                print(detected["version"])
            return 0
        apps = load_apps(args.manifest)
        if args.name and args.all:
            fail("missing_arguments", "choose an app name or --all, not both")
        selected = apps if args.all or args.name is None else [a for a in apps if a["name"] == args.name]
        if args.name and not selected:
            fail("app_not_found", f"no manifest entry named {args.name!r}")
        results = []
        errors = []
        for app in selected:
            try:
                result = inspect(app)
                results.append(result)
                if not args.json:
                    print_report(result)
                if args.action == "check":
                    continue
                if args.action == "update" and result["status"] != "update_available":
                    if not args.json:
                        print("  skipped: no newer version detected")
                    continue
                if args.action == "update" and not args.apply:
                    if not args.json:
                        print("  dry run: pass --apply to confirm and install")
                    continue
                if args.action == "update":
                    answer = input(f"Download and install {app['name']} {result['latest']}? [y/N] ").strip().lower()
                    if answer != "y":
                        print("  skipped update")
                        continue
                asset = result["asset_info"]
                filename = Path(asset["name"]).name
                staged = download_asset(asset, args.dir / app["name"] / filename)
                print(f"  downloaded: {staged}")
                if args.action == "update" and args.apply:
                    if not asset.get("digest"):
                        answer = input("GitHub supplied no digest. Install without API digest verification? [y/N] ").strip().lower()
                        if answer != "y":
                            print("  downloaded only; installation skipped")
                            continue
                        print("  warning: GitHub provided no asset digest; review upstream checksums before proceeding")
                    install(app, asset, staged)
                    installed_version = installed_version_for(app, result["latest"])
                    persist_version(args.manifest, app, installed_version, result.get("build_id", ""))
            except Exception as exc:
                code = error_code(exc)
                message = str(exc) or exc.__class__.__name__
                errors.append({"name": app["name"], "code": code, "error": message})
                results.append({
                    "name": app["name"],
                    "repo": app["repo"],
                    "current": app["installed_version"],
                    "latest": "",
                    "status": "check_failed",
                    "release_url": f"https://github.com/{app['repo']}/releases",
                    "asset": "",
                    "asset_size": 0,
                    "digest": "",
                    "install_method": app["install_method"],
                    "release_channel": app.get("release_channel", "stable"),
                    "error_code": code,
                    "error": message,
                })
                if not args.json:
                    print(f"  error: {message}", file=sys.stderr)
        if args.json:
            json_result({
                "checked": len(results),
                "updates": sum(r["status"] == "update_available" for r in results),
                "apps": [{k: v for k, v in result.items() if k not in ("release", "asset_info")} for result in results],
                "errors": errors,
            })
            return 1 if errors else 0
        if errors:
            return 1
    except (OSError, ValueError, UpdaterError, urllib.error.URLError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        code = error_code(exc)
        message = str(exc) or exc.__class__.__name__
        if args.json:
            if args.action in ("add", "remove", "set-channel", "detect-version"):
                json_result({"ok": False, "code": code, "error": message})
            else:
                json_result({"checked": 0, "updates": 0, "apps": [], "errors": [{"code": code, "error": message}]})
        else:
            print(f"error: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
