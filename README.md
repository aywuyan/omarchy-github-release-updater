# GitHub app updates for Omarchy

An Omarchy bar widget and popup that compares configured applications with GitHub
releases. Each app can follow the latest stable release or the latest published
developer prerelease. Developer builds are tracked by the selected asset's GitHub
digest, so a rebuilt package with the same version number still appears as an
update.

The popup accepts a repository URL, `/releases/latest` URL, or
`/releases/tag/...` URL and can update Linux AppImages and Arch package archives
published as GitHub assets. If the installed version is left blank, **Add app**
tries to detect it from Pacman or the AppImage's `--version` output; if that
fails, enter the version manually.

## Configure

For a fresh install, copy the example into user configuration and adjust the
installed version and asset expression for each app:

```sh
mkdir -p ~/.config/omarchy/github-release-updater
cp apps.example.json ~/.config/omarchy/github-release-updater/apps.json
```

Each entry stores `name`, `repo` (`owner/repository`), `installed_version`,
`asset_regex` (optional), `install_method`, and `release_channel` (`stable` or
`development`; defaults to `stable`). Two optional keys:

- `package` — the Pacman package name when it differs from the app's display
  name (`name` is also used for the download directory, so it must not contain
  `/` or start with a dot).
- `target` — the absolute installed path, required when `install_method` is
  `appimage`.

The panel's **Add app** form writes all of this for you. Registration is
explicit; the plugin does not guess the identity of arbitrary files on disk. A
supplied Release URL identifies the repository; the checker follows the selected
release channel. The checker automatically filters release assets by install type
and system architecture, and chooses one when there is a single clear candidate.
If a release has multiple matching assets, enter an optional filename regex to
select the right one.

The helper supports two install methods:

- `pacman` for `.pkg.tar.zst` and `.pkg.tar.xz` assets. It invokes
  `sudo pacman -U` interactively.
- `appimage` for an AppImage asset. Set `target` to its absolute installed path;
  the old file is backed up before replacement.

### Panel settings

The panel keeps its own preferences in
`~/.config/omarchy/github-release-updater/settings.json`:

- **GitHub token** — saved in the desktop keyring (via `secret-tool`), or taken
  from `GH_TOKEN`/`GITHUB_TOKEN`, or from a signed-in `gh` CLI. Tokens are never
  written to `settings.json` and never passed on a command line.
- **Local proxy** — accepts `127.0.0.1:PORT` or `0:PORT` and is used for HTTP and
  HTTPS. **Test proxy** makes an HTTPS request to GitHub through the address.
  When disabled, the session's `HTTP(S)_PROXY` settings apply instead. An
  advanced `no_proxy` string can be added to `settings.json` by hand.
- **Automatic check interval** — every hour, 3, 6, 12, 24, 48 hours, weekly, or
  off. The default is every 6 hours; the check also runs when the bar starts and
  when the panel is opened (at most once a minute).

The interface follows the language chosen under **Setup > Language** and updates
without a restart. English and Simplified Chinese (`zh-CN`) catalogs ship in
`i18n/`; other languages fall back to English.

## Install locally

Install from the public repository with Omarchy:

```sh
omarchy plugin add https://github.com/aywuyan/omarchy-github-release-updater --enable
```

To remove the plugin, run `omarchy plugin remove com.aywuyan.github-release-updater`.
This removes the installed plugin; it does not uninstall any applications you
track. Per-user app settings and credentials are stored under
`~/.config/omarchy/github-release-updater/` and in the desktop keyring,
respectively.

The plugin uses Python 3 and Omarchy Shell. Updating `.pkg.tar.zst` or
`.pkg.tar.xz` assets requires `sudo`/`pacman`; AppImage updates use standard
Python libraries. Saving a token requires `secret-tool` and a desktop keyring.
`gh` is optional and enables GitHub CLI sign-in.

The bar stays visible so the management panel is always accessible, including
when all apps are up to date. Clicking the indicator opens the management panel;
middle-click checks immediately. Each update opens a floating terminal and asks
before downloading and installing; the terminal shows live byte and percentage
progress while downloading, then reports digest verification and installation.
The **Remove** action removes an app from the tracking manifest after
confirmation; it does not uninstall the app. `pacman` can ask its own package
replacement questions as well.

## Safety notes

- Everything that comes from GitHub is rendered as plain text; tag names and
  asset names cannot inject markup or load remote resources.
- Downloads are capped at the size the GitHub API reports for the asset, and the
  GitHub token is dropped when a redirect leaves `github.com`.
- Digest verification uses the asset digest GitHub publishes. If GitHub provides
  no digest, the updater asks again before installing anything.
- `sudo pacman -U` installs the downloaded archive; it is not independently
  signature-verified by this plugin.

## Tests

The helper's version comparison, validation, size caps, redirect handling, and
the QML plain-text rule have local tests:

```sh
python3 -m unittest discover -s tests -v
```

For higher GitHub API limits or private repositories, enter a token in the
panel, log in with `gh auth login`, or set `GH_TOKEN`/`GITHUB_TOKEN`. The helper
reads saved credentials from the keyring on demand. If the local proxy is
disabled, requests can still use the normal `HTTPS_PROXY`/`HTTP_PROXY`
environment settings.
