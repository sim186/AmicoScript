# Architecture & code quality review

Scope: the whole repository as of `2fd637e` — `backend/` (10.5k LOC Python),
`frontend/js/` (6.0k LOC ES modules), `tui/` (6.1k LOC Textual), `tests/`
(7.7k LOC). Read against clean-architecture layering and clean-code rules on
duplication, naming, function size and error handling.

**Status: findings 1, 2, 3, 5, 6 and 7 have been fixed** — see the per-section
notes below and the commits following this document. The rest stand as written.

The findings were originally derived by reading the code. They have since been
verified against a running suite: the two defects in §2 were reproduced as
failing tests before the fix, and every change below is covered by tests that
fail without it (678 passing, 2 skipped).

---

## Verdict

This is **not spaghetti code**. The layering is real and mostly respected, the
comments explain *why* rather than *what*, and the seams that matter (job
queue, LLM transport, storage paths, migrations) are named and isolated.
Compared to the usual output of long AI-assisted sessions, the structure is
well above average.

What it does have is the specific decay pattern that AI-assisted development
produces: **the same concept re-implemented at each place it was needed,
instead of being extracted once.** Nothing is tangled — things are *copied*.
Each copy was correct when written; the copies have since drifted apart, and
two of the divergences are live defects (§2).

The single highest-value structural change is to give the job subsystem one
owner. Findings §1, §2, §5 and §11 are all the same missing abstraction seen
from four angles.

| # | Finding | Severity | Effort | Status |
|---|---------|----------|--------|--------|
| 1 | The job record is built in 4 places with 4 different shapes | High | M | **fixed** |
| 2 | Five divergent definitions of "job still in flight" — two are bugs | High | S | **fixed** |
| 3 | Silent `except Exception: pass` hides a data-loss path | Medium‑High | S | **fixed** |
| 4 | `_`-prefixed names are the cross-module public API (27 imports) | Medium | S | open |
| 5 | 16-field form signature duplicated across two routes | Medium | S | **fixed** |
| 6 | `backend/pipeline.py` is dead code | Medium | XS | **fixed** |
| 7 | Frontend: three copies of the same FormData builder in one file | Medium | S | **fixed** |
| 8 | TUI: each action written 2–3 times, with drift | Medium | M | open |
| 9 | `main.py` owns six unrelated concerns | Medium | M | open |
| 10 | 92 function-local imports, load-bearing and accidental mixed | Low‑Med | M | open |
| 11 | `core/` has no port/adapter seam (settings, HTTP, filesystem) | Low‑Med | L | open |

---

## 1. The job record is built in four places, with four different shapes

`state.jobs` is a `dict[str, dict]` — an untyped record that every layer
reads and writes. It is constructed independently at:

- `backend/api/routes/transcription.py:179` — `_create_job()` (transcribe, download+transcribe)
- `backend/api/routes/transcription.py:619` — inline, in `translate_all_api()`
- `backend/core/requeue.py:39` — `build_job()` (startup recovery, retry button)
- `backend/core/analysis_jobs.py:63` — `create_analysis_job()`

The four shapes do not agree:

| key | `_create_job` | `translate_all_api` | `build_job` | `create_analysis_job` |
|---|---|---|---|---|
| `source_url` / `source_platform` | ✅ | ❌ | ✅ (empty) | ❌ |
| `analysis_id` | ❌ | ❌ | ❌ | ✅ |
| `resumed` | ❌ | ❌ | ✅ | ❌ |
| `auto_generated` | ❌ | ❌ | ❌ | ✅ |
| `event_loop` source | `get_running_loop()` | `get_running_loop()` | `state.event_loop` | `state.event_loop` |

Because no consumer can rely on a key existing, **every read site defends
itself**. `list_jobs` (`routes/transcription.py:447-457`) calls `.get()` with a
default on all nine fields it returns. That defensiveness is not robustness —
it is the code paying interest on a missing type.

The clearest symptom is in `core/job_helpers.py:27-30`:

```python
from collections import deque
if "logs" not in job or not isinstance(job["logs"], deque):
    job["logs"] = deque(job.get("logs", []), maxlen=1000)
```

Every factory initialises `logs` to a plain `list`, so this branch runs on the
first log line of every job and silently repairs the type. A `logs` field that
was correct at construction would delete this code, the `"logs" not in job`
guard, and the `isinstance` check in `get_job_logs`
(`routes/transcription.py:510-511`) that exists for the same reason.

**Fixed.** `core/jobs.py` owns `create_job()` plus `submit()` /
`submit_threadsafe()`. Every job now carries every key, so unused fields are
present and empty instead of missing, and the SSE loop is resolved from the
calling context rather than guessed by each factory. The `logs` ring is bounded
at construction, which let both the repair in `_append_job_log` and the
`isinstance` guard in `get_job_logs` go. The two identical thread-safe enqueue
helpers collapsed into `submit_threadsafe`. Shape is pinned by
`tests/test_jobs_factory.py`.

---

## 2. Five definitions of "the job is still in flight" — two of them are wrong

The set of non-terminal statuses is written out as a literal in five places:

| location | contents |
|---|---|
| `main.py:269` `_RESUMABLE_STATUSES` | queued, downloading, preparing, loading_model, transcribing, diarizing, translating |
| `main.py:391` `_ACTIVE_STATUSES` | queued, transcribing, diarizing, loading_model, translating |
| `routes/transcription.py:438` `active_statuses` | queued, downloading, postprocessing, preparing, transcribing, diarizing, warning |
| `routes/benchmark.py:195` (inline) | queued, transcribing, diarizing, loading_model |
| `core/requeue.py:76-79` (inline) | queued, downloading, preparing, loading_model, transcribing, diarizing, translating |

The statuses actually pushed by `_push_event` are: `queued`, `downloading`,
`loading_model`, `transcribing`, `diarizing`, `translating`, `running`,
`streaming`, `warning`, `done`, `error`, `cancelled`. Neither `preparing` nor
`postprocessing` is ever a job status: `preparing` is a leftover from an
earlier state machine (old database rows still carry it), and `postprocessing`
is a phase name internal to the downloader, mapped to `downloading` before it
reaches a job. Both are filtered on regardless.

Two consequences are live defects:

**2a — jobs vanish from the queue strip.** `list_jobs` omits `loading_model`,
`translating`, `running` and `streaming`. Loading a Whisper model is the
slowest single step on a cold start, and `running`/`streaming` cover the entire
duration of an LLM analysis. During those windows `GET /api/jobs` returns
nothing for the job, so the UI queue widget shows it disappearing and then
reappearing.

**2b — the cleanup loop can tombstone a running job.** `_cleanup_loop`
(`main.py:394-413`) skips jobs whose status is in `_ACTIVE_STATUSES`, which
omits `downloading`, `running` and `streaming`. Any job in one of those states
for more than an hour is treated as abandoned:

```python
if job.get("status") in _ACTIVE_STATUSES or job.get("expired"):
    continue
if job.get("created_at", 0) < cutoff:
    ...
    _cleanup_job_temp_files(job)   # deletes temp files still in use
    _expire_job(job_id)            # replaces state.jobs[job_id]
```

`_expire_job` (`main.py:416`) substitutes a tombstone dict that has no
`sse_queue`, no `options`, no `cancel_flag`. The worker thread keeps running
against its own reference, but `_push_event` re-reads `state.jobs.get(job_id)`,
finds the tombstone, gets `sse_queue is None` and **returns without emitting**
— the SSE stream goes silent for the rest of the job. `_get_live_job` starts
answering `410`, so cancel, logs, result and export all fail. The audio itself
is safe (the `os.remove` is guarded by an `is_relative_to(STORAGE_ROOT)` check)
but the job is unreachable from the UI.

Both preconditions are ordinary, not exotic: a long playlist import
(`downloading`, bounded by a 2-way semaphore) and a map-reduce analysis of a
two-hour meeting against a local LLM (`running`/`streaming`) both routinely
exceed one hour.

**Fixed.** `core/job_status.py` holds one `StrEnum` and the frozensets derived
from it — `ACTIVE`, `RESUMABLE`, `RETRYABLE`, `TERMINAL` — and all five call
sites import them. Both defects were reproduced as failing tests first;
`tests/test_job_status.py` also asserts that every member of the enum is
classified, so the next status added cannot silently repeat this.

The expiry decision moved into `main._should_expire` so it can be tested
without driving an hour-long loop. `RESUMABLE` stays narrower than `ACTIVE` on
purpose: recovery rebuilds a *transcription* job, so admitting the analysis
states would re-transcribe a finished recording. The benchmark guard widened to
`ACTIVE` — refusing to benchmark while a local LLM saturates the same GPU is
the point of the guard.

---

## 3. Silent `except Exception: pass` hides a data-loss path

There are 15 exception handlers in `backend/` whose body is `pass`. Most are
defensible and documented (`_ensure_recording_platform_tag`,
`routes/transcription.py:231`, carries the comment *"Tagging should not fail
the transcription flow"* — that is exactly right).

One is not. `_create_recording_row` (`routes/transcription.py:142-164`):

```python
    except Exception:
        pass
```

If the `Recording` insert fails, the caller has no idea. `transcribe()`
proceeds to `_create_job()`, the worker transcribes the whole file, and then
`_sync_job_to_db` (`core/job_helpers.py:101-103`) does:

```python
rec = session.get(Recording, recording_id)
if not rec:
    return
```

— and returns quietly. The user waited through a full transcription; no
transcript was persisted, no error was raised, and nothing was logged at any
point. The recording row is a precondition for the job, not a nice-to-have: it
should raise and fail the request before the upload is accepted.

Two lesser cases worth tightening:

- `main.py:157-160` wraps `asyncio.create_task(_release_poller_loop())` in
  `try/except Exception: pass`. `create_task` does not raise for this; the
  handler protects nothing and implies a hazard that does not exist.
- `settings.py:22-29` `_load_settings()` returns `{}` on any error, so a
  corrupted `settings.json` silently reverts every setting to its default —
  including `llm_allow_cloud`. Failing closed is the right call for that flag,
  but the user should be told it happened.

**Fixed.** `_create_recording_row` now propagates; the route turns the failure
into a 500 that says what went wrong, and removes the already-ingested audio
first so no orphan directory is left behind. The `create_task` handler that
could not fire is gone, and `_load_settings` says so when it falls back to
defaults. Covered by `tests/test_upload_recording_row.py`.

The remaining swallows are the defensible ones. **Rule to keep:** a swallowed
exception needs a one-line comment saying why losing it is acceptable. Where
that sentence cannot be written honestly, the exception should propagate or at
minimum be logged.

---

## 4. Underscore-prefixed names are the cross-module public API

27 cross-module imports pull in `_`-prefixed names. The most-used API surface
in the backend is written as if it were private:

`_push_event`, `_append_job_log`, `_sync_job_to_db`, `_handle_job_error`,
`_cleanup_job_temp_files`, `_process_job`, `_worker_loop_async`,
`_get_whisper_model`, `_run_transcription_phase`, `_run_diarization_phase`,
`_build_analysis_prompt`, `_get_llm_settings`, `_get_whisper_settings`,
`_get_saved_hf_token`, `_convert_audio_for_diarization`, `_handle_colab_job` …

`backend/settings.py` is the extreme case: every one of its 18 functions is
underscore-prefixed, and 11 of them are imported by other modules. The
convention has been inverted so thoroughly that the leading underscore now
carries no information at all — a reader cannot tell which functions are safe
to change.

**Fix.** Mechanical rename: drop the underscore from anything imported across
module boundaries, keep it strictly for module-locals. Zero behaviour change,
and it restores a signal the codebase currently cannot express. Do it as its
own commit so it never has to be reviewed alongside logic changes.

---

## 5. The 16-field transcription form is declared twice

`transcribe()` (`routes/transcription.py:238-258`) and
`transcribe_from_url()` (`routes/transcription.py:314-334`) each declare the
same 16 `Form(...)` parameters, and each then calls
`_build_transcription_options()` with the same 15 keyword arguments.
`_build_transcription_options` itself takes 15 positional `str` parameters —
its signature (`:87-101`) is longer than its body.

That is roughly 70 lines of pure duplication, and it is fragile in a specific
way: adding a transcription option means editing four separate lists in the
right order, and the *only* thing preventing a mis-ordered keyword argument is
that they are all typed `str`.

**Fixed.** One `TranscriptionForm` dependency serves both routes, with the
`Form()` markers in `Annotated` rather than in the defaults — so the defaults
stay ordinary strings and the class is constructible outside FastAPI, which is
how the option tests now build it. The published contract is unchanged: same
fields, same content types, same required set, nothing moved to a query
parameter (verified against the generated OpenAPI schema).

---

## 6. `backend/pipeline.py` is dead code

31 lines re-exporting 20 private names from `core/`, with a docstring
describing it as a compatibility layer. **Nothing in the repository imports it**
— not `backend/`, not `tests/`, not `tui/`, not `scripts/`.

It was also actively misleading: `backend/shims.py:15` referred the reader to
"pipeline.py always normalises the diarization input", which is no longer where
that happens, and `state.py` named it as one of the two modules its globals
exist to keep apart.

**Fixed.** File deleted, both docstrings now name the module that does the
work.

---

## 7. Frontend: three copies of the same FormData builder in one file

In `frontend/js/upload.js`:

- `startTranscription()` lines 517-525 — inline
- `makeBatchFormData()` lines 558-570
- `makeUrlFormData()` lines 440-453

The first two are byte-identical apart from the file variable. All three append
the same nine fields from `state`.

There is also a **contract gap** worth noting on its own: none of the three
sends `compute_type`, `device`, `device_index`, `vad_filter`,
`word_timestamps`, `beam_size`, `best_of` or `force_normalize_audio`. The
backend carries all eight through the form layer, `TranscriptionConfig` and
`_build_transcription_options`, for clients that do not exist. The backend
comment at `routes/transcription.py:112` acknowledges this (*"which is every
client today"*) and works around it by falling back to saved settings — the
right call, but it means eight form parameters are dead weight on the public
API surface.

**Fixed.** One `buildTranscriptionFormData({ file, url })`, taking either a
file or a URL.

The contract gap is unchanged and still worth a decision: the eight unsent
options remain on the API for clients that do not exist.

---

## 8. TUI: each action is written two or three times, and the copies have drifted

The TUI exposes the same operations through three entry points — slash
commands (`tui/commands.py`), the fuzzy palette (`tui/palette.py`) and screen
keybindings (`tui/screens/*.py`) — and each re-implements the action rather
than calling a shared one.

**Retry** — `commands.py:423 _retry()` vs `screens/library.py:210`:
both call `api.retry_recording` and notify `f"queued again: {rec_id[:8]}…"`.
But the library screen first checks `status not in RETRYABLE` and surfaces
`status_detail`; the command does neither, and instead follows the job with
`_follow_job`. So the same user action behaves differently depending on how it
was triggered — from the command line you can fire a retry that the screen
would have refused, and from the screen you lose the progress follow.

**Analysis** — `commands.py:629 _analyze()` vs
`palette.py:737 _open_analysis_type_picker()`: identical `try/except/notify`
blocks with the same two message strings, but only the command form supports
`target_language` and `custom_prompt`.

**Fix.** A `tui/actions.py` holding one function per user-facing operation
(precondition check, API call, notification, refresh). Commands, palette and
screens become thin dispatchers. This is the largest single duplication cluster
in the repo and the one most likely to keep drifting.

Related: `tui/palette.py` is 1028 lines mixing the modal widget, entry
construction for six different data types, and six picker-opening flows. The
`entries_from_*` / `_*_entries` builders (lines 765-1007) are a coherent unit
that belongs in its own module.

---

## 9. `main.py` owns six unrelated concerns

454 lines covering: stdio patching for PyInstaller (`:21-37`), FastAPI + CORS +
auth middleware (`:64-136`), embedded/external meeting-watcher lifecycle
including Windows `schtasks` invocation (`:182-262`), interrupted-job recovery
(`:289-355`), GitHub release polling (`:358-388`), and the job cleanup/expiry
loop (`:391-437`).

Only the first two belong in an application entrypoint. Job recovery and job
expiry are domain logic operating on `state.jobs` — they belong next to the
other job code in `core/`, where they would naturally have picked up the
shared status sets from §2 instead of inventing their own. The watcher
lifecycle (~100 lines, platform-specific, two strategies) is a module of its
own.

Smaller items in the same file:

- `_recover_interrupted_jobs()` (`:289`) does four things — query, classify,
  mutate, re-enqueue — in one function with nested try/except and a tuple
  accumulator. Splitting the classify step out would make it directly testable.
- `_maybe_start_embedded_watcher()` (`:208`) mutates `sys.path`, sets two env
  vars, defines a nested `_run` closure, spawns a thread and falls back to a
  different strategy on import failure. Five responsibilities, one function.

---

## 10. 92 function-local imports, load-bearing and accidental mixed together

Many are genuinely necessary and should stay: deferring `torch`,
`faster_whisper`, `runtime_pack` and `pyannote` keeps startup fast and lets the
app run without a GPU stack installed. Breaking the `core ↔ routes` cycle is
also legitimate.

The problem is that the necessary ones are indistinguishable from the
accidental ones:

- `routes/transcription.py:523` re-imports `_sync_job_to_db` inside
  `rename_speaker()` — the module already imports it at line 17.
- `from config import STORAGE_ROOT` appears inside four different function
  bodies (`main.py:176`, `main.py:395`, `routes/transcription.py:78`,
  `routes/transcription.py:483`, `core/transcription.py:400`) while
  `db.py:21` and `storage.py:15` import from the same module at top level.
  `config` has no heavy dependencies and no cycle — these can all move up.

**Fix.** Hoist every local import that is not deferring a heavy dependency or
breaking a cycle, and add a one-line comment (`# deferred: pulls in torch`) to
the ones that stay. The comment is the whole point — it tells the next reader
which ones they may not touch.

---

## 11. `core/` has no port/adapter seam

The layering is otherwise sound — `models → db → core → api/routes`, with no
upward imports from `core` into `api`. But `core` reaches directly for its
infrastructure:

- `core/analysis.py` calls `requests.post` inline (`:268`, `:307`)
- `core/*` imports `settings`, which reads and writes `~/.amicoscript/settings.json`
- `core/transcription.py` reads `os.environ` in five places for tuning knobs
  (`AMICO_WORD_TIMESTAMPS`, `AMICOSCRIPT_DOWNLOAD_CONCURRENCY`, …)

So nothing in `core` can be exercised without patching module internals or
redirecting `$HOME` — which is exactly what `tests/conftest.py:26-31` has to
do for the whole session. That workaround is well-executed and clearly
explained, but it is compensating for a missing seam.

This is the lowest-priority item in the document and the one where the cost of
"correct" clean architecture is least obviously worth paying for an app of this
size. A pragmatic middle: pass an `LLMTarget`-style config object into `core`
functions (the pattern `core/analysis.py:LLMTarget` already establishes and
documents well) rather than having them call `_get_llm_settings()` themselves.

---

## What is working well — keep doing it

Worth stating explicitly, because these are the parts a future refactor should
not disturb:

- **Comments explain the failure that motivated the code.** `main.py:290-299`
  on job recovery, `core/analysis.py:1-13` on why map-reduce exists,
  `core/transcription.py:167-171` on why `resolve_compute_type` must run after
  the CUDA runtime is fetched, `models/__init__.py:41-47` on why
  `TranscriptChunk` is derived and never the source of truth. This is the
  hardest kind of documentation to get right and it is consistently right here.
- **`LLMTarget` (`core/analysis.py:226`)** — the exact abstraction §1 needs,
  with a docstring that names the problem it solved. Apply that reasoning to
  the job record.
- **Migrations are disciplined** — numbered steps in `migrations.py`, a
  `schema_version` table, and `db.py:28-38` explicitly refusing to swallow a
  failed migration.
- **`db.get_session` / `new_session`** — one commit/rollback policy, correctly
  split between request scope and thread scope.
- **The test suite is substantial** (7.7k LOC, 74% of backend LOC) and
  `conftest.py` sandboxes `$HOME` before any backend import — a subtle
  ordering requirement, handled correctly and explained.
- **Error messages are written for users, not developers** —
  `core/requeue.py:90-94`, `main.py:319-325`.

---

## Done, and what is left

Done, in this order — §2 (one `JobStatus` enum, two live bugs), §3 (the
data-loss swallow), §6 (delete `pipeline.py`), §1 (the single job factory),
then §5 and §7 (the duplicated form builders on both sides of the API).

Existing files lost 114 lines net. Two new modules — `core/job_status.py` and
`core/jobs.py` — added 229, most of it the docstrings explaining what each one
is for, so production code is **+115 overall**. Deduplication paid for itself
inside the files it touched; it did not shrink the tree, and was not meant to.
Tests grew by 327 lines across three new files, and the suite stabilised at 678
passing once a pre-existing flake was tracked down — see below.

Remaining, in the order worth doing them:

1. **§4** — the underscore rename. Do it as one isolated commit, so it never
   has to be reviewed alongside logic. It gets more expensive with every module
   added.
2. **§8** — `tui/actions.py`. The largest remaining duplication cluster, and
   the one already producing user-visible drift.
3. **§9, §10** — split `main.py`, hoist the accidental local imports. Both are
   easier now that the job subsystem has an owner: `_recover_interrupted_jobs`
   and `_should_expire` are the natural first tenants of a `core/` lifecycle
   module.
4. **§11** — only if the app grows another consumer of `core`.

### One thing found along the way

The suite carried a flaky test, failing about one full run in eight:
`test_a_failed_recording_can_be_transcribed_again`. It is not a product bug.
The worker loop is live under the test client, so a retry test that asserts the
recording is *queued* races the worker that legitimately picks it up — and
under the load of a full run the worker gets there first, fails the job (there
is no Whisper model in the test environment) and the row already reads `error`.

The `idle_worker` fixture holds the worker off for tests that are about
queueing rather than processing. Worth knowing about when adding tests here:
anything that puts a job on the queue and then inspects state is racing a real
background thread.
