# AmicoScript Documentation

## Overview

AmicoScript is a local-first audio transcription tool powered by Whisper.

It provides:

- audio transcription
- optional speaker diarization
- transcript management and search
- export in multiple formats

---

## Getting Started (Flow)

Typical usage:

1. Upload an audio file (or batch of files)
2. Start a transcription job
3. Monitor progress
4. Retrieve the result
5. Export or edit the transcript

---

## API Reference

### Models

**GET /api/models**

Returns available Whisper models.

---

### Settings

**GET /api/settings**  
Retrieve saved settings. Secrets are never echoed back: the Hugging Face token
is reported as `hf_token_set` plus a masked `hf_token_preview`, and
`GET /api/llm/settings` reports `llm_api_key_set` rather than the key.

**POST /api/settings**  
Save settings. Post the sentinel `__unchanged__` for a secret field the user did
not edit, so saving an unrelated setting cannot overwrite a stored credential.
Also accepts `auto_summarize_meetings` (see below).

---

### Transcription

**POST /api/transcribe**

Upload an audio file and start a transcription job.

Response:

```json
{
  "job_id": "string"
}
```

---

### Job Progress

**GET /api/jobs/{id}/stream**

Server-Sent Events (SSE) stream for real-time progress updates.

---

### Cancel Job

**POST /api/jobs/{id}/cancel**

Cancels a running transcription job.

---

### Job Result

**GET /api/jobs/{id}/result**

Returns the full transcription result in JSON format.

---

### Export

**GET /api/jobs/{id}/export/{fmt}**
**GET /api/recordings/{id}/export/{fmt}**

Download a transcript in one of the following formats:

| Format | Notes |
|--------|-------|
| `json` | The full pipeline result, including word timings when enabled |
| `srt`  | Subtitles; speaker names are prefixed in the caption text |
| `vtt`  | WebVTT subtitles; speakers become `<v Name>` voice spans, which is what `<track>` expects |
| `txt`  | Plain text grouped by speaker with timestamps |
| `md`   | Markdown with a metadata header and speaker runs |
| `csv`  | One row per segment (index, start, end, speaker, text, translation, edited) — for spreadsheets and pandas |

**POST /api/recordings/bulk-export/md** — combine several transcripts into a
single Markdown document with a table of contents. Body: `{"ids": [...]}`.

---

### Library portability

**GET /api/library/export?include_audio=true&ids=**

Download the whole library as a zip bundle:

```
manifest.json          format, version, counts
data.json              folders, tags, recordings, transcripts, analyses
audio/<rec-id>/<file>  the recordings themselves (when include_audio)
```

`ids` takes a comma-separated list to export a subset. Settings are
deliberately **not** included — the bundle would otherwise carry your Hugging
Face token, LLM API key and password hash.

**POST /api/library/import** — multipart upload of a bundle.

- `mode=skip` (default) keeps rows that already exist; `mode=overwrite` replaces them.
- Rows are matched by primary key, so importing the same bundle twice is a no-op.
- Entries with absolute paths or `..` are refused (zip-slip), as are implausible
  compression ratios.

---

### Authentication

Local use is unchanged: requests from the host machine need no credentials.
Requests from anywhere else are refused until a password is set.

**GET /api/auth/status** — `{enabled, mode, password_set, authenticated, local, login_required}`
**POST /api/auth/login** — form field `password`; sets an HttpOnly session cookie
**POST /api/auth/logout**
**POST /api/auth/password** — form fields `new_password`, `current_password`.
The first password can only be set from the host machine.
**DELETE /api/auth/password?current_password=** — host machine only
**GET /api/auth/api-token** — bearer token for headless clients

Modes are chosen with `AMICOSCRIPT_AUTH`:

| Value | Behaviour |
|-------|-----------|
| `auto` (default) | Loopback is trusted. The network needs a session, and is refused outright while no password exists. |
| `always` | Every request needs a session, including from this machine. Headless clients use `AMICOSCRIPT_API_TOKEN`. |
| `off` | No authentication. Only use this when something else (SSO proxy, Traefik basic-auth) guards the app. |

The classification reads the direct peer address, never `X-Forwarded-For`, so a
request cannot claim to be local by sending a header.

---

## Speaker Diarization Setup

Speaker diarization uses `pyannote` and requires:

1. A Hugging Face account
2. Acceptance of model licenses
3. A valid `hf_` token

Add your token via the settings endpoint or UI.

---

## Architecture

- Backend: Python + FastAPI
- Frontend: Static HTML served by FastAPI
- Processing: Background threads for transcription

### Storage

- In-memory job state
- Temporary audio files (auto-deleted after ~1 hour)

---

## GPU Support

To enable GPU acceleration:

1. Use a CUDA-enabled PyTorch base image
2. Update the Dockerfile accordingly
3. Enable GPU support in docker-compose

---

## Notes

- All processing is local
- No audio data is uploaded externally
- Performance depends on hardware and selected model

## AI Analysis & LLM Integration (New in 1.4)

AmicoScript can now call a locally hosted LLM (e.g. Ollama or any service implementing a compatible /v1/chat/completions API) to produce higher-level analyses from transcripts. This includes:

- Summaries: concise meeting summaries highlighting topics and decisions.
- Action items: extracted tasks, owners, and deadlines where present.
- Translations: translate the full transcript into a target language using the LLM.
- Custom prompts: run arbitrary instructions against the transcript.

### Setting Up Ollama (LLM Runtime)

To use the AI analysis features, you need a compatible LLM service running locally. **Ollama** is the easiest option and is free.

#### Installation

**macOS:**
1. Download from [ollama.com](https://ollama.com)
2. Move **Ollama.app** to `/Applications` and run it
3. The Ollama service will start automatically in the background

**Windows:**
1. Download the installer from [ollama.com](https://ollama.com)
2. Run the installer and follow the prompts
3. Ollama runs as a background service automatically

**Linux:**
```bash
curl -fsSL https://ollama.ai/install.sh | sh
```
Then start the service:
```bash
ollama serve
```

#### Getting a Model

Once Ollama is running, pull a model to download it locally:

```bash
ollama pull mistral      # Fast, good for summaries
ollama pull neural-chat  # Smaller, lighter weight
ollama pull llama2       # More capable, larger (~4GB)
```

First pull takes time (model download), but subsequent loads are instant.

#### Confirming Ollama Works

Check that Ollama is running at `http://localhost:11434`:

```bash
curl http://localhost:11434/api/tags
```

You should see a JSON list of your downloaded models.

#### Configuring AmicoScript

1. Open AmicoScript and go to **LLM Settings** (sidebar)
2. Set:
   - **Base URL:** `http://localhost:11434` (default)
   - **Model Name:** your chosen model (e.g., `mistral`)
   - **API Key:** leave blank (Ollama doesn't require one)
3. Click **Test Connection** to verify

Done! You can now use AI analysis features.

#### Docker Note

If running AmicoScript in Docker and Ollama on your host machine, use `http://host.docker.internal:11434` as the base URL instead.

---

Key implementation notes

- Settings: LLM configuration is persisted to the same settings store used for HF tokens. The UI exposes a `LLM Settings` panel (base URL, model name, API key).
- Backend endpoints:
  - `GET /api/llm/settings` — returns the current LLM configuration.
  - `POST /api/llm/settings` — save LLM settings (`llm_base_url`, `llm_model_name`, `llm_api_key`).
  - `POST /api/llm/test-connection` — quick connectivity test to the configured LLM.
  - `GET /api/llm/models` — list models exposed by the LLM server (if supported).
  - `POST /api/llm/models/pull` — fire-and-forget model pull (useful for Ollama's `/api/pull`).
  - `POST /api/recordings/{recording_id}/analyses` — create a new analysis job for a recording.
  - `GET /api/recordings/{recording_id}/analyses` — list past analyses for a recording.
  - `GET /api/recordings/{recording_id}/analyses/{analysis_id}` — fetch a specific analysis result.

Streaming and SSE

Analyses execute as background jobs and stream incremental results to the client via the existing SSE job stream: `GET /api/jobs/{job_id}/stream`. The frontend subscribes to that stream and appends partial deltas as they arrive.

Example: start an analysis (curl)

```bash
curl -X POST "http://localhost:8002/api/recordings/<RECORDING_ID>/analyses" \
  -F analysis_type=summary \
  -F output_language=English
```

Example: test LLM connection (curl)

```bash
curl -X POST "http://localhost:8002/api/llm/test-connection"
```

Notes & references

- Ollama HTTP API (example server): https://docs.ollama.com/
- SSE (EventSource) streaming pattern: https://developer.mozilla.org/en-US/docs/Web/API/EventSource
- Settings location on disk: `~/.amicoscript/settings.json` (contains `llm_base_url`, `llm_model_name`, `llm_api_key`, `hf_token`, etc.)

Docker tip: when running the app in Docker and your LLM server runs on the host, use `http://host.docker.internal:11434` as the base URL.

### Long transcripts and the context window

A one-hour meeting is roughly 12k tokens, and local LLM servers commonly run a
4k–8k context window (Ollama defaults to 4096). Sending more than fits makes the
model silently drop the *oldest* part of the input, which used to produce
confident summaries covering only the end of a recording.

AmicoScript now measures the transcript against the configured budget
(`llm_context_tokens`, default 8192, editable under AI Analysis) and, when it
does not fit, processes it map-reduce style: each chunk is summarised on its
own, then the partial results are merged. Translation is the exception — its
chunks are concatenated in order, because merging translated passages would
rewrite them. Progress events report "Processing part *n* of *m*".

If the merged partials are themselves too large, they are folded down in pairs
until they fit.

### Automatic meeting summaries

Turn on **Summarise automatically** under Meeting auto-capture and every
finished call is summarised without being asked. It only fires for recordings
whose `source` is `meeting` (the watcher sets this when it uploads), only when
an LLM is configured, and only once per recording. The resulting analysis is
flagged `auto_generated: true` so the UI can tell it apart from one you
requested. A failure here is logged and dropped — it never fails the
transcription that just succeeded.

---

## Interrupted jobs

Restarting the app used to flip every in-flight recording to `error` with no
explanation, losing a partly-transcribed two-hour meeting. Now:

- anything whose audio is still on disk goes back onto the queue with
  `status_detail = "Requeued after the app restarted"`;
- anything that cannot be resumed (interrupted mid-download, so no audio was
  ever saved) is marked `interrupted` with a reason the UI shows on hover.

Set `AMICOSCRIPT_RESUME_JOBS=0` for the old fail-fast behaviour.

Jobs are also evicted from memory an hour after they finish. A tombstone is
kept, so `/api/jobs/{id}/…` answers **410** with the recording id rather than a
404 that looks like the job never existed — the transcript is in the library.

---

## Database migrations

Schema changes are numbered steps in `backend/migrations.py`, recorded in a
`schema_version` table so each runs exactly once. A failing step raises
`MigrationError` and leaves the database on the previous version instead of
being swallowed. Opening a database written by a newer build is refused rather
than guessed at.

---

Running tests

Install `pytest` (if not already installed):

```bash
python -m pip install pytest httpx
```

Run the test suite:

```bash
pytest -q
```
