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

The complete, always-current list of endpoints is the generated reference at
**<https://sim186.github.io/AmicoScript/api.html>**, built from the FastAPI routes
themselves (`python scripts/generate_openapi.py` → `website/openapi.json`). A running
install serves the same schema at `http://localhost:8002/openapi.json`, with Swagger UI
at `/docs`.

What follows is the hand-written tour of the endpoints worth explaining in prose —
the request/response shapes, the export formats, and the auth rules. Reach for the
generated reference when you want the whole surface.

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
| `md`   | Markdown with YAML frontmatter and speaker runs |
| `csv`  | One row per segment (index, start, end, speaker, text, translation, edited) — for spreadsheets and pandas |

Add `?wikilinks=true` to write speaker names as `[[Name]]`. It applies to `md`
only and is off by default.

**POST /api/recordings/bulk-export/md** — combine several transcripts into a
single Markdown document with a table of contents. Body:
`{"ids": [...], "wikilinks": false}`.

#### Markdown frontmatter

The `md` export opens with a YAML block that Obsidian, Hugo, Jekyll and Quartz
read as note properties:

```yaml
---
title: "Q3 Review"
date: 2026-08-05
duration: "31:15"
duration_seconds: 1875.4
language: "en"
speakers:
  - "Ada"
  - "Grace"
tags:
  - "quarterly-review"
folder: "Work/Reviews"
source: "upload"
model: "small"
---
```

Keys with no value are omitted. Tag names are normalised to Obsidian's rules —
whitespace becomes `-`, a leading `#` is dropped. A bulk export of several
recordings gets one block for the collection (`recordings`, `date_from`, every
speaker and tag) rather than one per transcript, since only the block at the
top of a file is frontmatter.

---

### Global search & the command palette

**GET /api/search?q=&limit=20&offset=0**

One query, answered from everything the library knows about a recording:

| `kind`       | where the match was found                                |
| ------------ | -------------------------------------------------------- |
| `transcript` | the spoken words — FTS5 over `transcript.full_text`       |
| `summary`    | LLM output — FTS5 over `analysis.result_text`             |
| `title`      | the file name, or the alias it was renamed to             |
| `tag`        | the name of a tag on the recording                        |
| `folder`     | the name of the folder holding it                         |

```json
[
  {
    "recording_id": "…", "filename": "standup.mp3", "alias": "Monday standup",
    "duration": 743.0, "status": "done",
    "kind": "summary", "matched_in": ["transcript", "summary"],
    "snippet": "The team agreed to postpone the <mark>Helsinki</mark> launch."
  }
]
```

A recording is returned **once** no matter how many places matched; `kind` is
the strongest of them and supplies the snippet, and `matched_in` lists them
all. Ranking is by kind in the order of the table above — a match in a better
place always outranks one in a worse place, and matching in several lifts a
result only within its own band. The ranking itself lives in
`backend/core/search.py`.

`snippet` is library text with `<mark>` around the match. It is **not** escaped
— a transcript can contain anything — so a caller putting it in a page must
escape it and restore only those two tags, as `frontend/js/command-palette.js`
does.

The query is never handed to FTS5 raw (see `backend/search_query.py`); a query
FTS5 cannot express at all, like `:-)`, falls back to a substring scan rather
than returning nothing.

In the browser this is the **command palette**, opened with `Ctrl`/`⌘` + `K`
from anywhere, including from inside a text field. It searches as you type and
also runs any command in the app. A leading character narrows it, the same
prefixes the terminal UI uses:

| prefix | shows                                      |
| ------ | ------------------------------------------ |
| `/`    | commands only                              |
| `@`    | recordings only                            |
| `#`    | folders and tags only                      |

Commands, folders and tags are matched in the browser and appear instantly;
recordings come from `/api/search`, debounced. `Enter` on the last row opens
every result in the library rather than just the first few.

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

### Speed, and which device it runs on

Diarization follows the same `device` and `device_index` the transcription was
given, so a job running Whisper on the GPU diarizes there too. `auto` picks a
GPU when torch reports one and the CPU otherwise; an explicit `cuda` on a
machine without one falls back rather than failing the job.

This matters more than it sounds. pyannote's `Pipeline.from_pretrained` returns
a pipeline **on the CPU** — moving it takes an explicit `.to(device)` — so it
is easy to end up diarizing on the CPU on a machine whose GPU is otherwise
busy transcribing. If diarization feels disproportionately slow, that is the
first thing to check: the job log records `Diarization running on <device>` for
every run, and a CPU run also says so in the progress message.

The pipeline is cached for the life of the process and reloaded only when the
device changes, so only the first diarized job pays the model load.

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

### Which device a job uses

`device` and `compute_type` come from the saved `whisper_device` /
`whisper_compute` settings, falling back to `auto`. They are settings rather
than per-request arguments: the transcription endpoints used to accept both as
form fields, no client sent either, and the request-level override was removed
along with six other engine knobs nothing used. Change them on the settings
page, through `POST /api/settings`, or in the TUI.

`auto` picks a GPU when torch reports one and the CPU otherwise; an explicit
`cuda` on a machine without one falls back rather than failing the job.

`compute_type` defaults to `auto` too, which resolves to `float16` on a GPU and
`int8` on a CPU — the two are each wrong on the other's hardware. Save a
specific precision (`int8`, `float16`, `int8_float16`, `float32`) to pin it.

Both phases record what they got, in the job log:

```
Transcribing on cuda:0 (float16)
Diarization running on cuda:0
```

If a GPU was expected and the log says `cpu`, that is the answer — the app also
says so in the progress message rather than just running slowly.

### Releases

Release builds come in CPU and GPU variants: CPU for Linux, Windows and macOS,
GPU for Linux and Windows. There is no macOS GPU build because there is no CUDA
on macOS.

The GPU bundle needs more than CUDA torch. faster-whisper transcribes through
CTranslate2, which loads cuBLAS and cuDNN by soname at runtime; those arrive as
`nvidia-*` packages that nothing imports, so PyInstaller has to be told to
collect them (`--collect-binaries`) and they have to be loaded from inside the
bundle before CTranslate2 starts — see `backend/cuda_runtime.py`. Without both
halves the GPU build runs torch (so diarization) on the GPU while Whisper
quietly falls back to the CPU.

### Docker

The default image is CPU-only, and pins the CPU torch index deliberately: on
Linux the default PyPI wheel is the CUDA build, which adds gigabytes of nvidia
libraries to an image that cannot use them.

For a GPU, build the CUDA image — it needs an NVIDIA GPU on the host plus the
NVIDIA Container Toolkit:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

`Dockerfile.gpu` uses a `cudnn`-flavoured base image rather than a bare
`cuda-runtime` one, because CTranslate2 needs cuDNN as well as cuBLAS and a
missing cuDNN fails at the first transcription rather than at build time.

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

### Which backends work

Anything that speaks `POST /v1/chat/completions` — which is all of these:

| Provider | Default address | API key | Notes |
|----------|-----------------|---------|-------|
| **Ollama** | `http://localhost:11434` | none | The only one AmicoScript can download models for. Set `OLLAMA_HOST=0.0.0.0` to reach it from Docker. |
| **LM Studio** | `http://localhost:1234` | none | Start the server from the Developer tab. Binds to loopback; enable "Serve on Local Network" for Docker. |
| **Unsloth Studio** | `http://localhost:8888` | **required** | Serves GGUF/safetensors via llama-server. Copy the key from Settings → API; it starts with `sk-unsloth-`. |
| **llama.cpp** (`llama-server`) | `http://localhost:8080` | optional | Only if you started it with `--api-key`. |
| **vLLM** | `http://localhost:8000` | optional | Only if you started it with `--api-key`. |
| **Jan** | `http://localhost:1337` | none | Enable the local API server in Jan's settings. |
| **LocalAI** | `http://localhost:8080` | optional | |
| **OpenRouter** | `https://openrouter.ai/api` | **required** | Hosted. Hundreds of models including free ones — but your transcripts leave the machine, so it is behind an explicit opt-in. |
| **Other** | — | optional | OpenAI, Groq, Together, Mistral, a company gateway, anything OpenAI-compatible. |

Pick one under **LLM Settings** in the sidebar and the address, key requirement
and setup link fill themselves in.

### Finding a server automatically

**Find running servers** probes the ports above and reports what answered, which
models it has loaded, and whether it wants a key. One click adopts it — provider,
address and a model are filled in for you. `GET /api/llm/detect` exposes the same
scan.

A server that answers `401` still shows up, marked as needing a key. That is the
normal state for Unsloth Studio.

### Addresses: what you can paste

Every one of these tools displays a base URL ending in `/v1`, but AmicoScript
appends `/v1/chat/completions` itself, so pasting it verbatim used to produce
`/v1/v1/…` and a 404 that looked like the server was broken. All of the
following now resolve to the same thing:

```
http://localhost:1234
http://localhost:1234/v1
http://localhost:1234/v1/chat/completions
localhost:1234
```

The cleaned-up address is shown back to you with a note explaining what changed.

### Docker

Inside a container `localhost` is the container, so it can never reach an LLM
running on your machine. Two things handle this:

- `docker-compose.yml` maps `host.docker.internal` to the host gateway, which
  Docker Desktop provides automatically but Linux does not.
- AmicoScript detects that it is containerised and rewrites `localhost` /
  `127.0.0.1` to `host.docker.internal` when you save an address, telling you it
  did. Server scanning probes the host rather than the container.

The LLM itself still has to listen beyond loopback — `OLLAMA_HOST=0.0.0.0` for
Ollama, "Serve on Local Network" for LM Studio. Override the alias with
`AMICOSCRIPT_DOCKER_HOST` if your runtime uses a different name.

### Hosted providers and your transcripts

AmicoScript keeps audio local, always. A hosted provider (currently OpenRouter,
or any remote address you enter yourself) receives the **text** of whatever it
analyses, which is the whole transcript. That is a real departure from the
local-first promise, so it is gated: tick the confirmation in LLM Settings, and
until you do, both manual analyses and automatic meeting summaries refuse to run
against it and say why.

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

### LLM API endpoints

**GET /api/llm/providers** — the preset catalog, plus whether AmicoScript is
running in a container and under what host alias.
**GET /api/llm/detect** — scan the well-known ports for a running server.
**GET /api/llm/models?base_url=** — list a server's models; `base_url` previews
one before saving it.
**POST /api/llm/settings** — accepts `llm_provider`, `llm_base_url`,
`llm_model_name`, `llm_api_key`, `llm_context_tokens`, `llm_max_output_tokens`
and `llm_allow_cloud`. Returns the normalized address and a note describing any
change.
**POST /api/llm/test-connection** — performs a real completion and returns an
actionable message on failure (which tool is not running, whether the key is
wrong, whether the address has a stray `/v1`).
**POST /api/llm/models/pull** — Ollama only; other providers return 400 with an
explanation.

Notes & references

- Ollama HTTP API (example server): https://docs.ollama.com/
- SSE (EventSource) streaming pattern: https://developer.mozilla.org/en-US/docs/Web/API/EventSource
- Settings location on disk: `~/.amicoscript/settings.json` (contains `llm_provider`, `llm_base_url`, `llm_model_name`, `llm_api_key`, `hf_token`, etc.)

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

### Chat with your library

**POST /api/library/chat** — body `{"question": "..."}`. Answers a question
from every transcript at once, and says where the answer came from:

```json
{
  "answer": "You agreed on forty a seat, with annual billing [1].",
  "sources": [
    {"recording_id": "…", "title": "pricing-call.mp3", "start": 41.0, "end": 95.0,
     "timestamp": "0:41", "speakers": "Ada, Grace", "text": "…", "chunk_id": "…"}
  ],
  "cited": [1],
  "used_semantic": false,
  "no_matches": false,
  "pending": 0
}
```

The `[n]` markers in `answer` index into `sources` — `[1]` is `sources[0]` —
so the UI can turn each one into a link that opens the recording at
`start`. `cited` lists which of them the answer actually used; a citation the
model invented (`[9]` out of four sources) is dropped rather than shown.
`no_matches` means nothing in the library matched, and the model was never
asked — an empty-handed search should not become an invented answer.

#### How retrieval works

Transcripts are split into passages of roughly a paragraph, each keeping the
timestamps it spans, in the `transcriptchunk` table. A whole transcript is the
wrong unit — a two-hour recording matches everything and cites nothing — and a
diarized segment is often four words long.

Retrieval is hybrid:

- **Keyword** (always available, no setup). FTS5 over the chunks. Note that a
  question is ORed rather than ANDed after its stopwords are dropped: ANDing
  "what did we decide about pricing" would match nothing.
- **Semantic** (optional). Set an embedding model and the same passages are
  matched by meaning, so a question about pricing finds a passage that says
  "forty a seat". Without it, that passage is only found if it says "pricing".

Both rankings are combined with reciprocal rank fusion, so a passage only one
of them found still reaches the model.

#### Index endpoints

**GET /api/library/index** — how much is indexed, how much is embedded, and
whether semantic search is available.

**POST /api/library/index/rebuild** — chunk transcripts that have none;
`?all=true` rebuilds everything. Chunks are otherwise maintained automatically:
written when a transcription finishes, rebuilt when a segment is edited or a
speaker renamed, and deleted with the recording.

**POST /api/library/index/embed** — embed a batch of chunks (200 at a time),
returning `remaining`. Call it until `remaining` is 0; the UI's **Rebuild &
embed** button does this loop for you.

Embeddings come from the `/v1/embeddings` endpoint of the LLM server already
configured — Ollama, LM Studio, llama.cpp and vLLM all expose one — so there is
no second runtime and no new Python dependency. Vectors are stored unit-length
on the chunk row, which means similarity is a plain dot product and a database
copy carries its index with it.

---

### Smart tagging

**POST /api/recordings/{id}/suggest-tags**

Reads the transcript with the configured LLM and returns candidate tags:

```json
{"suggestions": [{"name": "quarterly review", "tag_id": "…"}, {"name": "hiring", "tag_id": null}]}
```

`tag_id` is the existing tag when the library already has one by that name —
attach it with the usual `POST /api/recordings/{id}/tags/{tag_id}` — and `null`
when it would have to be created first.

**Nothing is applied.** The endpoint only proposes; the tag routes attach what
the user picks. It also never suggests a tag the recording already carries.

The prompt includes the tags the library already uses, so a second standup is
tagged `standup` rather than `stand-up` or `Daily Standup`, and a suggestion
that matches an existing tag apart from case comes back spelled the library's
way. At most six are returned, each at most three words.

Unlike an analysis this runs synchronously — the output is a handful of words.
It answers 400 when no model is configured or a hosted provider has not been
allowed to see transcripts, and 502 when the model cannot be reached. A
transcript longer than the context budget is sampled across its whole length
rather than truncated to the beginning, so the topics in the second half still
reach the model.

---

## Re-running a transcription

**POST /api/recordings/{id}/retry**

Queues an existing recording for transcription again, reusing the options it was
first run with. Available for recordings in a finished state (`error`,
`interrupted`, `cancelled`, `done`); anything still in flight answers 409, as
does a recording whose audio is no longer on disk.

The library shows a retry button on failed, cancelled and interrupted rows.

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
