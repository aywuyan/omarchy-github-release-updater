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
from urllib.parse import urlsplit


API = "https://api.github.com/repos/{repo}/releases/latest"
DEFAULT_MANIFEST = Path.home() / ".config/omarchy/github-release-updater/apps.json"
SETTINGS_FILE = Path.home() / ".config/omarchy/github-release-updater/settings.json"
SECRET_SERVICE = "com.aywuyan.github-release-updater"


def github_json(url):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "github-release-updater"}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with open_url(request, timeout=30) as response:
        return json.load(response)


def open_url(request, timeout):
    apply_proxy_settings()
    settings = read_settings()
    address = settings.get("proxy_address", "")
    if settings.get("proxy_enabled") and address:
        proxy = f"http://{address}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def load_apps(manifest):
    data = json.loads(manifest.read_text(encoding="utf-8"))
    apps = data.get("apps", [])
    if not isinstance(apps, list):
        raise ValueError("manifest field 'apps' must be a list")
    for app in apps:
        for field in ("name", "repo", "installed_version", "install_method"):
            if not app.get(field):
                raise ValueError(f"application entry is missing {field!r}")
        app.setdefault("asset_regex", "")
        if not isinstance(app["asset_regex"], str):
            raise ValueError("application field 'asset_regex' must be a string")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", app["repo"]):
            raise ValueError(f"invalid GitHub repository: {app['repo']}")
    return apps


def github_token():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    token = secret_lookup("github-token")
    if token:
        return token
    if not shutil.which("gh"):
        return ""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], check=True, capture_output=True,
            text=True, timeout=5,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


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
        raise ValueError("secret-tool is required to store credentials in the desktop keyring")
    if any(ord(char) < 32 and char not in "\t" for char in value):
        raise ValueError("value contains unsupported control characters")
    subprocess.run(
        ["secret-tool", "store", "--label=GitHub Release Updater", "service", SECRET_SERVICE, "key", key],
        input=value + "\n", capture_output=True, text=True, timeout=20, check=True,
    )


def secret_clear(key):
    if not shutil.which("secret-tool"):
        raise ValueError("secret-tool is required to manage credentials in the desktop keyring")
    subprocess.run(
        ["secret-tool", "clear", "service", SECRET_SERVICE, "key", key],
        capture_output=True, text=True, timeout=10, check=True,
    )


def read_settings():
    if not SETTINGS_FILE.exists():
        return {"no_proxy": "", "language": "auto", "proxy_address": "", "proxy_enabled": False}
    data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    language = str(data.get("language", "auto"))
    if language not in ("auto", "zh", "en"):
        language = "auto"
    proxy_address = str(data.get("proxy_address", ""))
    legacy_proxy = False
    if not proxy_address:
        for key in ("https-proxy", "http-proxy"):
            old_value = secret_lookup(key)
            if not old_value:
                continue
            parsed = urlsplit(old_value)
            if parsed.scheme in ("http", "https") and parsed.hostname in ("127.0.0.1", "0", "0.0.0.0") and parsed.port:
                host = "0" if parsed.hostname in ("0", "0.0.0.0") else "127.0.0.1"
                proxy_address = f"{host}:{parsed.port}"
                legacy_proxy = True
                break
    proxy_enabled = data.get("proxy_enabled", bool(proxy_address)) is True
    if legacy_proxy and "proxy_enabled" not in data:
        proxy_enabled = True
    return {
        "no_proxy": str(data.get("no_proxy", "")),
        "language": language,
        "proxy_address": proxy_address,
        "proxy_enabled": proxy_enabled,
    }


def write_settings(data):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(SETTINGS_FILE.parent, 0o700)
    temp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump({
            "no_proxy": data.get("no_proxy", ""),
            "language": data.get("language", "auto"),
            "proxy_address": data.get("proxy_address", ""),
            "proxy_enabled": data.get("proxy_enabled", False),
        }, out, ensure_ascii=False, indent=2)
        out.write("\n")
    os.chmod(temp, 0o600)
    temp.replace(SETTINGS_FILE)


def valid_proxy_url(value):
    value = value.strip()
    if not value:
        return ""
    if any(ord(char) < 32 for char in value):
        raise ValueError("proxy address contains control characters")
    match = re.fullmatch(r"(127\.0\.0\.1|0):([0-9]{1,5})", value)
    if not match:
        raise ValueError("enter a local proxy as 127.0.0.1:PORT or 0:PORT")
    port = int(match.group(2))
    if not 1 <= port <= 65535:
        raise ValueError("proxy port must be between 1 and 65535")
    return value


def test_proxy_address(value):
    address = valid_proxy_url(value)
    if not address:
        raise ValueError("enter a local proxy address before testing")
    proxy = f"http://{address}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    request = urllib.request.Request("https://api.github.com/zen", headers={"User-Agent": "omarchy-github-release-updater"})
    try:
        with opener.open(request, timeout=12) as response:
            return {"ok": True, "http_status": response.status}
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500 and exc.code != 407:
            return {"ok": True, "http_status": exc.code}
        raise ValueError(f"proxy connection failed with HTTP {exc.code}") from exc


def apply_proxy_settings():
    settings = read_settings()
    no_proxy = settings.get("no_proxy", "")
    if no_proxy:
        os.environ["NO_PROXY"] = no_proxy
        os.environ["no_proxy"] = no_proxy


def get_settings_status():
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
        "language": settings.get("language", "auto"),
        "uses_environment_proxy": not bool(http_proxy or https_proxy),
    }


def process_settings_action(payload):
    action = payload.get("action")
    if action == "get":
        return {"ok": True, **get_settings_status()}
    if action == "save-language":
        language = str(payload.get("language", "auto"))
        if language not in ("auto", "zh", "en"):
            raise ValueError("language must be auto, zh, or en")
        settings = read_settings()
        settings["language"] = language
        write_settings(settings)
        return {"ok": True, "action": action, "language": language}
    if action == "save-token":
        token = str(payload.get("token", ""))
        if not token.strip():
            raise ValueError("enter a GitHub token")
        secret_store("github-token", token.strip())
        return {"ok": True, "message": "GitHub token saved in the desktop keyring"}
    if action == "clear-token":
        secret_clear("github-token")
        return {"ok": True, "message": "GitHub token removed from the desktop keyring"}
    if action == "save-proxy":
        proxy_address = valid_proxy_url(str(payload.get("proxy_address", "")))
        proxy_enabled = payload.get("proxy_enabled") is True and bool(proxy_address)
        settings = read_settings()
        settings["proxy_address"] = proxy_address
        settings["proxy_enabled"] = proxy_enabled
        write_settings(settings)
        for key in ("http-proxy", "https-proxy"):
            try:
                secret_clear(key)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        return {"ok": True, "action": action, "proxy_address": proxy_address, "proxy_saved": bool(proxy_address), "proxy_enabled": proxy_enabled}
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
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        return {"ok": True, "action": action, "proxy_address": "", "proxy_saved": False, "proxy_enabled": False}
    raise ValueError("unknown settings action")


def settings_service():
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            result = process_settings_action(payload)
            result["action"] = payload.get("action", "")
        except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            result = {"ok": False, "error": str(exc)}
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
            raise ValueError(f"expected one asset matching {asset_regex!r}; found {len(matches)}{suffix}")
        raise ValueError(f"could not automatically choose one compatible release asset; found {len(matches)}{suffix}. Enter an optional filename regex to choose one")
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
    release = github_json(API.format(repo=app["repo"]))
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("GitHub latest endpoint returned a draft or prerelease")
    return release


def compare_versions(current_version, latest_version):
    current_parts = re.findall(r"\d+", current_version)
    latest_parts = re.findall(r"\d+", latest_version)
    if not current_parts or not latest_parts:
        return None
    current = tuple(map(int, current_parts))
    latest = tuple(map(int, latest_parts))
    return (latest > current) - (latest < current)


def inspect(app):
    release = release_for(app)
    tag = release.get("tag_name", "")
    asset = select_asset(app, release)
    comparison = compare_versions(app["installed_version"], tag)
    status = "compare manually" if comparison is None else (
        "update available" if comparison > 0 else
        "up to date" if comparison == 0 else "installed version is newer"
    )
    return {
        "name": app["name"],
        "repo": app["repo"],
        "current": app["installed_version"],
        "latest": tag,
        "status": status,
        "release_url": release.get("html_url", ""),
        "asset": asset["name"],
        "asset_url": asset["browser_download_url"],
        "asset_size": asset["size"],
        "digest": asset.get("digest", ""),
        "install_method": app["install_method"],
        "release": release,
        "asset_info": asset,
    }


def print_report(result):
    print(f"{result['name']}: {result['current']} -> {result['latest']} ({result['status']})")
    print(f"  repository: {result['repo']}")
    print(f"  asset: {result['asset']} ({result['asset_size']} bytes)")
    print(f"  SHA-256: {result['digest'] or 'not provided by GitHub API'}")
    print(f"  install method: {result['install_method']}")


def download_asset(asset, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "omarchy-github-release-updater"}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(asset["browser_download_url"], headers=headers)
    temp = destination.with_name(destination.name + ".part")
    try:
        with open_url(request, timeout=60) as response, temp.open("wb") as out:
            shutil.copyfileobj(response, out)
        if temp.stat().st_size != asset["size"]:
            raise ValueError(f"download size mismatch: got {temp.stat().st_size}, expected {asset['size']}")
        digest = asset.get("digest")
        if digest:
            algorithm, expected = digest.split(":", 1)
            if algorithm.lower() != "sha256":
                raise ValueError(f"unsupported GitHub digest algorithm: {algorithm}")
            hasher = hashlib.sha256()
            with temp.open("rb") as downloaded:
                for chunk in iter(lambda: downloaded.read(1024 * 1024), b""):
                    hasher.update(chunk)
            actual = hasher.hexdigest()
            if actual.lower() != expected.lower():
                raise ValueError("SHA-256 verification failed")
        temp.replace(destination)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return destination


def install(app, asset, path):
    method = app["install_method"]
    if method == "pacman":
        if not path.name.endswith((".pkg.tar.zst", ".pkg.tar.xz")):
            raise ValueError("pacman method requires a .pkg.tar.zst or .pkg.tar.xz asset")
        subprocess.run(["sudo", "pacman", "-U", str(path)], check=True)
    elif method == "appimage":
        target = Path(app.get("target", "")).expanduser()
        if not target.is_absolute() or not target.parent.is_dir():
            raise ValueError("AppImage target must be an absolute path in an existing directory")
        if not path.name.endswith(".AppImage"):
            raise ValueError("AppImage method requires an asset ending in .AppImage")
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
        raise ValueError(f"unsupported install method: {method}")


def persist_version(manifest, app, version):
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for item in data.get("apps", []):
        if item.get("name") == app["name"] and item.get("repo") == app["repo"]:
            item["installed_version"] = version
            break
    temp = manifest.with_name(manifest.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(manifest)


def add_release_app(manifest, url, version, asset_regex, install_method, name, target):
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ("github.com", "www.github.com"):
        raise ValueError("enter an https://github.com/owner/repository URL")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise ValueError("URL must include both owner and repository")
    owner, repository = parts[0], parts[1]
    repository = repository.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository URL contains unsupported characters")
    if len(parts) > 2:
        valid_release_path = (
            (len(parts) == 3 and parts[2] == "releases")
            or (len(parts) == 4 and parts[2:] == ["releases", "latest"])
            or (len(parts) == 5 and parts[2] == "releases" and parts[3] == "tag")
        )
        if not valid_release_path:
            raise ValueError("use a repository URL, /releases/latest, or /releases/tag/<tag>")
    if not version.strip():
        raise ValueError("installed version is required")
    if asset_regex.strip():
        try:
            re.compile(asset_regex)
        except re.error as exc:
            raise ValueError(f"invalid asset regular expression: {exc}") from exc
    if install_method not in ("pacman", "appimage"):
        raise ValueError("install method must be pacman or appimage")
    app_name = name.strip() or repository
    app = {
        "name": app_name,
        "repo": f"{owner}/{repository}",
        "installed_version": version.strip(),
        "asset_regex": asset_regex,
        "install_method": install_method,
    }
    if install_method == "appimage":
        target_path = Path(target).expanduser()
        if not target_path.is_absolute():
            raise ValueError("AppImage target must be an absolute path")
        app["target"] = str(target_path)

    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
    else:
        data = {"apps": []}
    apps = data.get("apps")
    if not isinstance(apps, list):
        raise ValueError("manifest field 'apps' must be a list")
    if any(existing.get("name") == app_name or existing.get("repo", "").lower() == app["repo"].lower() for existing in apps):
        raise ValueError("an app with this name or repository is already registered")
    apps.append(app)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temp = manifest.with_name(manifest.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(manifest)
    return app


def detect_installed_version(install_method, name, target, url):
    if install_method == "pacman":
        package = name.strip()
        if not package:
            parsed = urlsplit(url.strip())
            parts = [part for part in parsed.path.strip("/").split("/") if part]
            package = parts[1].removesuffix(".git") if len(parts) >= 2 else ""
        if not package:
            raise ValueError("enter an app/package name or GitHub repository first")
        result = subprocess.run(["pacman", "-Q", package], check=True, capture_output=True, text=True, timeout=10)
        fields = result.stdout.strip().split()
        if len(fields) < 2:
            raise ValueError(f"could not read the installed version for package {package!r}")
        return {"version": fields[-1], "source": f"pacman package {fields[0]}"}

    if install_method == "appimage":
        appimage = Path(target).expanduser()
        if not appimage.is_absolute() or not appimage.is_file():
            raise ValueError("enter an existing absolute AppImage path first")
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
            raise ValueError("the AppImage did not return a version within 8 seconds") from exc
        output = (result.stdout + "\n" + result.stderr).strip()
        match = re.search(r"(?<![A-Za-z0-9])v?(\d+\.\d+(?:\.\d+){0,3}(?:[-+][A-Za-z0-9.-]+)?)", output)
        if match:
            return {"version": match.group(1), "source": "AppImage --version"}
        raise ValueError("the AppImage did not report a version with --version; enter it manually")

    raise ValueError("install method must be pacman or appimage")


def remove_release_app(manifest, name):
    if not manifest.exists():
        raise ValueError("app manifest does not exist")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    apps = data.get("apps", [])
    if not isinstance(apps, list):
        raise ValueError("manifest field 'apps' must be a list")
    remaining = [app for app in apps if app.get("name") != name]
    removed = len(apps) - len(remaining)
    if removed == 0:
        raise ValueError(f"no registered app named {name!r}")
    data["apps"] = remaining
    temp = manifest.with_name(manifest.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(manifest)
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "download", "update", "add", "remove", "detect-version", "settings-service"))
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
    args = parser.parse_args()

    try:
        if args.action == "settings-service":
            settings_service()
            return 0
        if args.action == "add":
            if not args.url or not args.version:
                raise ValueError("add requires --url and --version")
            app = add_release_app(args.manifest, args.url, args.version, args.asset_regex,
                                  args.install_method, args.app_name or args.name or "", args.target)
            result = {"ok": True, "name": app["name"], "repo": app["repo"]}
            print(json.dumps(result, ensure_ascii=False) if args.json else f"Added {app['name']} ({app['repo']})")
            return 0
        if args.action == "remove":
            if not args.name:
                raise ValueError("remove requires an app name")
            removed = remove_release_app(args.manifest, args.name)
            result = {"ok": True, "name": args.name, "removed": removed}
            print(json.dumps(result, ensure_ascii=False) if args.json else f"Removed {args.name} from tracking")
            return 0
        if args.action == "detect-version":
            detected = detect_installed_version(args.install_method, args.name or "", args.target, args.url)
            result = {"ok": True, **detected}
            print(json.dumps(result, ensure_ascii=False) if args.json else detected["version"])
            return 0
        apps = load_apps(args.manifest)
        if args.name and args.all:
            raise ValueError("choose an app name or --all, not both")
        selected = apps if args.all or args.name is None else [a for a in apps if a["name"] == args.name]
        if args.name and not selected:
            raise ValueError(f"no manifest entry named {args.name!r}")
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
                if args.action == "update" and result["status"] != "update available":
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
                    persist_version(args.manifest, app, result["latest"])
            except (OSError, ValueError, urllib.error.URLError, subprocess.CalledProcessError) as exc:
                errors.append({"name": app["name"], "error": str(exc)})
                results.append({
                    "name": app["name"],
                    "repo": app["repo"],
                    "current": app["installed_version"],
                    "latest": "?",
                    "status": "check failed",
                    "release_url": f"https://github.com/{app['repo']}/releases",
                    "asset": "",
                    "asset_size": 0,
                    "digest": "",
                    "install_method": app["install_method"],
                })
                if not args.json:
                    print(f"  error: {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({
                "checked": len(results),
                "updates": sum(r["status"] == "update available" for r in results),
                "apps": [{k: v for k, v in result.items() if k not in ("release", "asset_info")} for result in results],
                "errors": errors,
            }, ensure_ascii=False))
            return 0
        if errors:
            return 1
    except (OSError, ValueError, urllib.error.URLError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        if args.json:
            if args.action in ("add", "remove", "detect-version"):
                print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
                return 0
            print(json.dumps({"checked": 0, "updates": 0, "apps": [], "errors": [{"error": str(exc)}]}))
            return 0
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
