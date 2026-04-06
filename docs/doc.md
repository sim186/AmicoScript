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
Retrieve saved settings (e.g., Hugging Face token)

**POST /api/settings**  
Save settings

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

Download transcript in one of the following formats:

- json
- srt
- txt
- md

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

Running tests

Install `pytest` (if not already installed):

```bash
python -m pip install pytest
```

Run the test suite:

```bash
pytest -q
```
