# Changelog

All notable changes to this project will be documented in this file.
This project adheres to Semantic Versioning (https://semver.org/) and the
Keep a Changelog format.


## [1.1.1] - 2026-04-01
- Improve library color dropbox

## [1.1.0] - 2026-03-31

- UI: Introduced a fixed 10-color palette for tags and folders and server-side
	validation to ensure consistent colors across clients.
- UI: Folder tree and tag sidebar now show per-folder and per-tag counts.
- UI: Replaced free-form color pickers with compact palette popovers (rendered
	as top-level overlays to avoid clipping) and added a folder rename popover to
	avoid expanding the sidebar during edits.
- UI: Tag-click filtering is now scoped to the selected folder; tags absent in
	the current folder render as disabled with counts.
- UI: Live accent preview applied when editing a folder color so changes appear
	immediately before saving.
- Backend: Added `ALLOWED_COLORS` palette, color validation for tag/folder
	create/update, and endpoints return aggregated counts for folders and tags.

## [1.0.0] - 2026-03-30

- Fixed PyInstaller packaging for speaker diarization by bundling `pyannote.audio` data files (including `telemetry/config.yaml`) in standalone builds.
- Fixed windowed (`--noconsole`) runtime crash during diarization (`'NoneType' object has no attribute 'write'`) by providing safe stdio fallbacks for libraries that write to `stdout`/`stderr`.
- Fixed GitHub Actions release workflow: corrected `artifacts` parameter and added `allowUpdates` to support multi-OS parallel builds.
- Initial stable release.

