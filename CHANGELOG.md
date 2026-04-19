# Changelog

All notable changes to this project will be documented in this file.
This project adheres to Semantic Versioning (https://semver.org/) and the
Keep a Changelog format.

## [Unreleased]



## [1.10.1] - 2026-04-19
### 🔒 Security

- **CORS restricted to localhost:** `allow_origins` changed from `["*"]` to explicit localhost origins, preventing cross-origin requests from arbitrary websites.
- **Exit endpoint CSRF token:** `/api/exit` now requires a per-session token generated at startup (`secrets.token_hex(32)`), blocking DNS-rebinding attacks that could terminate the app remotely.
- **Audio path bounds check:** `/api/audio/{job_id}` validates the served file is inside `STORAGE_ROOT` before responding, preventing potential path traversal.
- **Zip-slip guard:** ffmpeg extraction now verifies the extracted binary resolves inside the target directory after extraction.
- **Frontend XSS fix:** `showFolderMenu` and `showTagMenu` rebuilt using DOM API (`createElement` + `addEventListener`) instead of `innerHTML` with embedded JSON, eliminating injection via folder/tag names containing `'` or `</script>`.
- **HF token removed from localStorage:** Hugging Face token no longer written to `localStorage` (readable by browser extensions); loaded from server only.

### 🐛 Fixes

- **Chunked file upload:** `/api/transcribe` now streams uploads in 1 MB chunks instead of buffering the entire file in RAM — prevents OOM crashes on large audio files.
- **Session lifecycle:** `get_session` and `new_session` now commit on success and rollback on exception; routes that omit an explicit `commit()` no longer silently drop writes.
- **Atomic settings write:** `_save_settings` writes to a `.tmp` file then renames atomically via `os.replace`, preventing corrupt/truncated settings on crash.
- **Settings portable mode:** `settings.py` now derives its storage path from `AMICOSCRIPT_PORTABLE` env var, matching `config.py` behavior — settings no longer leak to `~/.amicoscript` in portable mode.
- **Config mkdir deferred:** `STORAGE_ROOT` and `RECORDINGS_DIR` directories are no longer created at import time; creation moved to `ensure_storage_dirs()` called during startup.
- **ffmpeg raises on failure:** `get_ffmpeg_path` now raises `RuntimeError` instead of returning `None` when the binary cannot be found or downloaded, preventing `TypeError` crashes in callers.
- **asyncio.Queue deferred init:** `JOB_QUEUE` created in `_init_queue()` called at startup rather than at module import, fixing silent breakage on Python 3.9.
- **Whisper model cache thread-safety:** `_get_whisper_model` is now wrapped in `state._model_lock` to prevent concurrent access from the worker and translation threads.
- **Translation chunk no collision:** `_translate_audio_chunk` uses `tempfile.mkstemp()` instead of a timestamp-based filename — concurrent translations can no longer overwrite each other's temp files.
- **Delete order fixed:** `delete_recording` now deletes DB rows and commits before unlinking the audio file — a crash between the two no longer leaves orphaned DB records pointing to missing files.
- **Delete blocked during active job:** `DELETE /api/recordings/{id}` returns 409 if the recording is currently being transcribed or translated.
- **Cleanup loop skips running jobs:** The hourly cleanup loop no longer deletes temp files for jobs still in active states (`queued`, `transcribing`, `diarizing`, etc.).
- **Speaker rename persisted:** `/api/jobs/{id}/rename-speaker` now calls `_sync_job_to_db` after updating in-memory state — renames survive server restarts.
- **Export job guards None result:** `export_job` returns 404 instead of crashing if job is marked done but `result` was never set.
- **LIKE wildcard escaping:** Search query is now escaped (`%` → `\%`, `_` → `\_`) with `ESCAPE '\\'` before embedding in SQL LIKE patterns — search for filenames containing `_` or `%` now works correctly.
- **Library limit clamped:** `GET /api/library?limit=-1` no longer bypasses the row cap; limit is clamped with `max(1, min(limit, 200))`.
- **Export json_data validated:** `export_recording` wraps `json.loads(tr.json_data)` in a try/except and returns a 500 with a clear message instead of a raw `KeyError` traceback.
- **Folder delete cleans Analysis rows:** `delete_folder` with `delete_recordings=True` now also deletes associated `Analysis` rows, preventing orphaned records.
- **Negative int params rejected:** `num_speakers`, `beam_size`, `best_of`, and related int fields now use `try: int(v)` with a positivity check instead of `.isdigit()`, which silently ignored negative values.
- **Normalized audio written to tempdir:** `_normalize_audio` now creates the intermediate WAV via `tempfile.mkstemp()` instead of writing beside the source file, fixing failures on read-only mounts.
- **Export formatters safe on missing segments:** All export formatters (`_format_srt`, `_format_txt`, `_format_md`) use `.get("segments", [])` and no longer crash on missing or empty segments.

### 🧪 Tests

- Added `test_exports.py`: format functions with empty/missing segments, speaker prefix, JSON roundtrip.
- Added `test_settings.py`: atomic write, corruption guard, portable mode path, standard mode path.
- Added `test_search_escaping.py`: LIKE wildcard escaping logic, negative/overlarge limit clamping.
- Added `test_job_logs_deque.py`: log cap at 1000, deque type, insertion order.
- Added `test_config_lazy_mkdir.py`: no mkdir on import, `ensure_storage_dirs()` creates dirs.
- Added `test_ffmpeg_helper.py`: zip-slip detection, raises on unsupported OS, returns existing binary.
- Added `test_translation_chunk.py`: `mkstemp` used, temp file cleaned up on error.
- Added `test_db_session.py`: session commits on success, rolls back on exception.
- Added `test_transcription_options.py`: valid ints, negative → default, non-numeric → default, zero → default.
- No change details provided.

## [1.10.0] - 2026-04-19
### ✨ Improvements
- **Microphone recording:** Added "Record mic" button to the upload area. Opens a dialog to record directly from your microphone, with pause/resume support and a live timer. On stop, the recording is queued into the normal batch transcription flow — no backend changes required.
- No change details provided.

## [1.9.0] - 2026-04-19

### ✨ Improvements
- **README:** Added badges (stars, release, license, Python version), competitor comparison table, Telegram community link, and roadmap section.
- **Community:** Added `CONTRIBUTING.md` with contribution guide and AI-code disclosure note.
- **Community:** Added GitHub issue templates for Bug Report, Feature Request, and Documentation.
- **Roadmap:** Simplified `docs/ROADMAP.md` — stripped implementation details, now points to the [GitHub Project board](https://github.com/users/sim186/projects/1) as source of truth.
- **UI:** Added Feedback link in sidebar footer — opens GitHub issue template chooser directly.

## [1.8.0] - 2026-04-18

### ✨ Improvements
- URL source support in the downloader flow to include YouTube, TikTok, Instagram, Facebook, X, Vimeo, and Twitch (through `yt-dlp` resolution).
- Automatic platform tagging: recordings imported from URLs now receive a source tag (for example `youtube`, `tiktok`, `instagram`) for easier filtering in the library.

## [1.7.0] - 2026-04-15
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
- No change details provided.

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
