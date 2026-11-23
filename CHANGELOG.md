# Changelog

## 0.11.1

- Refuse to install an asset GitHub published no digest for; the update stops
  before downloading instead of offering an unverified `pacman -U`.
- Unload the panel content when the card closes, so no writable Qt text fields
  stay in the long-lived shell. Fields are capped in length and are writable
  only while focused or hovered, which bounds the Qt 6.12 clipboard surface.

## 0.11.0

- Automatic add: paste a repository URL and the plugin chooses the install
  format for this system, reuses an installed AppImage (file or extracted
  folder), and decides where a new AppImage goes. Version and path are optional.
- Detect and update an AppImage extracted into a folder (AppDir, e.g.
  `~/.local/opt/ds-bot`), with the old folder kept as a backup.
- A first add can omit the installed version; it is recorded after the first
  install.
- The add/remove/update messages live in the row that was clicked and clear on
  their own; a refused or partial matching asset is reported with the names it
  found.
- GitHub text is always rendered as plain text, downloads are capped, the token
  is dropped on cross-host redirects, and app names cannot escape the download
  directory.
- The interface follows the Omarchy language and ships English and Simplified
  Chinese catalogs.
- 33 local tests cover version comparison, asset selection, AppDir extraction,
  redirects, size caps, settings-service recovery, and the QML plain-text rule.

## 0.10.0

- Bar panel rebuilt on the Omarchy `KeyboardPanel` with theme colors and kit
  controls.
