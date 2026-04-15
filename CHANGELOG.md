# Changelog

All notable changes to this project will be documented in this file.
This project adheres to Semantic Versioning (https://semver.org/) and the
Keep a Changelog format.

## [Unreleased]

### ✨ Improvements

- Backend API modularization: split the monolithic FastAPI routes into dedicated router modules under `backend/api/routes/` (`settings`, `llm`, `analyses`, `releases`, `transcription`, `library`, `folders_tags`) and reduced `backend/main.py` to startup, worker orchestration, and static mounts.
- Worker/message cleanup: introduced `backend/core/messages.py` to centralize repeated status strings used across transcription and Colab proxy flows.
- Resilience cleanup: narrowed several broad exception handlers in core modules to more specific expected failure types while preserving retry and fallback behavior.

### 🧪 Tests

- Added unit tests for diarization speaker assignment overlap/fallback logic.
- Added unit tests for audio normalization helpers and ffmpeg-missing fallback paths.
- Added unit tests for Whisper model cache key behavior (`compute_type`, `device`, `device_index`).
- Added unit tests for CUDA/VAD error classifiers.
- Added mocked integration tests for transcription flow orchestration and cancellation path.
- Added mocked integration tests for Colab proxy success/error forwarding.
- Added retry-behavior test coverage for `_sync_job_to_db`.
- Added `tests/conftest.py` bootstrap to support backend-style imports in test runtime.

### 🐛 Fixes

- Fixed DB sync retry handling regression by allowing transient `RuntimeError` to be retried in `_sync_job_to_db`.


## [1.6.0] - 2026-04-14
### ✨ Improvements

- Backend: Refactored the monolithic transcription pipeline into focused modules under `backend/core/` (`transcription`, `diarization`, `analysis`, `translation`, `audio_utils`, `job_helpers`, `colab_proxy`) and kept `backend/pipeline.py` as a compatibility shim.
- Backend: Split job processing into explicit phases (`_run_transcription_phase`, `_run_diarization_phase`, `_finalize_transcription_result`, `_handle_colab_job`) with clearer type hints and docstrings.
- Worker architecture: Replaced thread queue worker startup with a single asyncio background worker task using `asyncio.Queue` for sequential processing.
- Logging: Added structured JSON logging utilities and centralized job error handling/DB sync helpers.
- Transcription options: Added configurable `compute_type`, `device`, `device_index`, `vad_filter`, `word_timestamps`, `beam_size`, `best_of`, and `force_normalize_audio` via a new `TranscriptionConfig` model.
- Audio processing: Unified normalization paths with `_normalize_audio` and kept explicit wrappers for transcription/diarization.
- Database: Added indexes for frequently queried fields (`recording.status`, `recording.created_at`, `transcript.recording_id`, `transcript.created_at`) and moved models to a package layout under `backend/models/`.
- No change details provided.

## [1.5.1] - 2026-04-13
- **Update check**: Added a new feature to check for updates by querying GitHub Releases. The frontend will display a banner if a newer release is available, with a link to view the release notes..

## [1.5.0] - 2026-04-12

### ✨ Improvements

- **Optional Google Collab Integration:** Added the ability to connect to Google Collab for enhanced AI analysis capabilities, this is especially useful for users without local GPU resources. To use this feature instruction in the README.md are provided.
- **Bulk Actions**:: Added the ability to select multiple recordings in the library and apply bulk actions such as moving to a folder, adding/removing tags, or deleting.
- **Load or Drop Directory:** Added the ability to load or drop a directory of audio files for batch transcription.

### 🐛 Fixes
- **Clean batched file list** before processing to avoid issues with empty or invalid entries.
- **UI minor improvements** console log being shown over the transcript content and some mobile layout issues.

## [1.4.1] - 2026-04-11

### ✨ Improvements

- **Mobile UI:** Sidebar is now an off-canvas overlay on small screens — tap the hamburger to open it, tap the backdrop to dismiss. Segment action buttons are always visible on touch devices (no hover required).
- **Mobile UI:** Reduced padding throughout (transcribe tab, transcript segments, AI panel, library toolbar) so content is readable on phone-width viewports.
- **Mobile UI:** Global search input and "Export" label are hidden on small screens to prevent tab-bar overflow.
- **Docker:** Compose setup split into three files for clean dev/prod separation:
  - `docker-compose.yml` — base service definition, no network-specific config.
  - `docker-compose.override.yml` — local development, auto-loaded by Compose, exposes port 8002.
  - `docker-compose.prod.yml` — production overlay, adds Traefik labels and joins the Traefik Docker network.
- **Docker:** Production deployment now supports Traefik reverse proxy with automatic Let's Encrypt HTTPS via TLS-ALPN-01 challenge. Configure via `.env` (see `.env.example`).

### 🐛 Fixes
- **Docker build:** Fixed an issue where the `backend/` directory was copied into the image with an extra nesting level, causing import errors. The `COPY` instruction now correctly places the backend files at the root of the image filesystem.
- **Versioning:** Updated the `VERSION` file to `1.4.1` to reflect the latest patch release.


---

## [1.4.0] - 2026-04-06

### ✨ New Features

- **AI Analysis Engine:** Add per-recording LLM-powered analyses (summary, action items, translation, custom prompts) with streaming results.
- **LLM Settings & Model Management:** Configure LLM base URL, model name and API key from the UI. List available models and trigger model pulls (Ollama-style `/api/pull`).
- **Frontend: AI Analysis Panel:** New inner tab in the transcript view for running analyses, viewing streaming output, and inspecting past analysis results.

### ✨ Improvements

- **Job processing:** Background worker now supports `analysis` jobs and streams incremental output to the client; improved job logging and cancel handling.
- **Frontend UX:** Drawer-style sidebar, inner tab panels (Transcript / AI Analysis), client-side action logs, and a Help modal with Docker LLM tips.
- **File format support:** Added `.opus` to the allowed upload extensions.

### 🐛 Fixes

- **Cascade deletes:** Deleting a recording now also removes associated Analysis rows from the database.
- **Robustness:** Better error handling for LLM calls and safer cleanup of analysis job state on failure or cancellation.
- **Visual polish:** Improved styling

---

## [1.3.1] - 2026-04-04
- UI: Remove the inline folder/tag creation in favor of dialog (similar to edit)
- Re-enabled MacOs release workflow

## [1.3.0] - 2026-04-04
- UI: Added `waveform` player with interactive seeking and segment highlighting.
- UI: Moved the console log to a collapsible bottom panel with timestamps (hidden by default).
- Backend: Added the possibility to upload multiple files at once.
- Backend: Added support for video files by extracting audio with `ffmpeg` before transcription.
- Release: Added support for MacOS (make sure to disable Gatekeeper for the app on first launch: `xattr -d com.apple.quarantine /path/to/app`).

## [1.2.0] - 2026-04-01

- UI: Global search with live filtering (folder and tag matches).
- UI: Fixed keyboard shortcut overlay persistence on page refresh.
- UI: Robust background translation job status tracking and cancellation.
- Backend: Server-side Hugging Face token persistence for diarization models.
- Backend: Switched to `torchaudio` pre-loading for speaker identification to avoid `torchcodec` compatibility issues.
- Feature: Automated platform-specific FFmpeg download upon first application startup.

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

## [1.5.2] - 2026-04-13
- Changelog entry
