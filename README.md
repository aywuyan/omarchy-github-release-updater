# GitHub app updates for Omarchy

An Omarchy Shell bar widget and panel that compares configured applications with GitHub releases. Each app can follow the latest stable release or the latest published developer prerelease. Developer builds are tracked by the selected asset's GitHub digest, so a rebuilt package with the same version number still appears as an update. The panel accepts a repository URL, `/releases/latest` URL, or `/releases/tag/...` URL and can update Linux AppImages and Arch package archives published as GitHub assets. If the installed version is left blank, **Add app** tries to detect it from Pacman or the AppImage's `--version` output; if that fails, enter the version manually. The UI supports automatic locale detection, English, and Simplified Chinese.

## Configure

The example tracks Sparkle's developer prerelease. For Pacman packages whose installed version includes the rolling commit, the checker compares that commit with the current GitHub rolling tag, so it recognizes an already-installed daily build even if the updater did not install it. Other developer packages establish a local asset-digest baseline after the first update. For a fresh install, copy it into user configuration and adjust the installed version and asset expression for each app:

```sh
mkdir -p ~/.config/omarchy/github-release-updater
cp apps.example.json ~/.config/omarchy/github-release-updater/apps.json
```

The helper supports two install methods:

- `pacman` for `.pkg.tar.zst` and `.pkg.tar.xz` assets. It invokes `sudo pacman -U` interactively.
- `appimage` for an AppImage asset. Set `target` to its absolute installed path; the old file is backed up before replacement.

Use the panel's **Add app** form to register an app. It asks for the current version, install format, and release channel. Each registered app has an explicit **Switch to stable** or **Switch to dev** action; after switching, choose **Update** to install from that channel. The checker automatically filters release assets by install type and system architecture, and chooses one when there is a single clear candidate. If a release has multiple matching assets, enter an optional filename regex to select the right one. Each entry stores `name`, `repo` (`owner/repository`), `installed_version`, `asset_regex` (optional), `install_method`, and `release_channel` (`stable` or `development`; defaults to `stable`). A supplied Release URL identifies the repository; the checker follows the selected release channel. Registration is explicit; the plugin does not guess the identity of arbitrary files on disk.

Open **Settings** in the panel to choose the display language, save a GitHub token, sign in through `gh auth login`, and configure a local proxy. Proxy addresses are shown in the field and accept `127.0.0.1:PORT` or `0:PORT`; the same proxy is used for HTTP and HTTPS requests. **Test proxy** makes an HTTPS request to GitHub through the entered address. The proxy can be enabled or disabled independently, and a disabled local proxy leaves the system's environment proxy settings in effect. The address is stored in the user's settings file; GitHub tokens remain in the desktop keyring or `gh` credential store.

## Install locally

Install from the public repository with Omarchy:

```sh
omarchy plugin add https://github.com/aywuyan/omarchy-github-release-updater --enable
```

To remove the plugin, run `omarchy plugin remove com.aywuyan.github-release-updater`. This removes the installed plugin; it does not uninstall any applications you track. Per-user app settings and credentials are stored under `~/.config/omarchy/github-release-updater/` and in the desktop keyring, respectively.

The plugin uses Python 3 and Omarchy Shell. Updating `.pkg.tar.zst` or `.pkg.tar.xz` assets requires `sudo`/`pacman`; AppImage updates use standard Python libraries. Saving a token requires `secret-tool` and a desktop keyring. `gh` is optional and enables GitHub CLI sign-in.

The bar checks every six hours and stays visible so the management panel is always accessible, including when all apps are up to date. Clicking the indicator opens the management panel. Each update opens a floating terminal and asks before downloading and installing; the terminal shows live byte and percentage progress while downloading, then reports digest verification and installation. The **Remove** action removes an app from the tracking manifest after confirmation; it does not uninstall the app. `pacman` can ask its own package replacement questions as well.

For higher GitHub API limits or private repositories, enter a token in Settings, log in with `gh auth login`, or set `GH_TOKEN`/`GITHUB_TOKEN`. The helper reads saved credentials from the keyring on demand. If the local proxy is disabled, requests can still use the normal `HTTPS_PROXY`/`HTTP_PROXY` environment settings.
