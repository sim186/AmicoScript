# Architecture & code quality review

Scope: the whole repository as of `2fd637e` — `backend/` (10.5k LOC Python),
`frontend/js/` (6.0k LOC ES modules), `tui/` (6.1k LOC Textual), `tests/`
(7.7k LOC). Read against clean-architecture layering and clean-code rules on
duplication, naming, function size and error handling.

**Status: all thirteen findings have been fixed**, along with the palette split
that §8 left open — see the per-section notes below and the commits following
this document. One item remains open, and it is a product decision rather than
a defect: see [Still worth doing](#still-worth-doing).

The findings were originally derived by reading the code. They have since been
verified against a running suite: the two defects in §2 and the one in §12 were
reproduced as failing tests before the fix, and every change below is covered
by tests that fail without it (838 passing, from 649 at the start).

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
| 4 | `_`-prefixed names are the cross-module public API (27 imports) | Medium | S | **fixed** |
| 5 | 16-field form signature duplicated across two routes | Medium | S | **fixed** |
| 6 | `backend/pipeline.py` is dead code | Medium | XS | **fixed** |
| 7 | Frontend: three copies of the same FormData builder in one file | Medium | S | **fixed** |
| 8 | TUI: each action written 2–3 times, with drift | Medium | M | **fixed** |
| 9 | `main.py` owns six unrelated concerns | Medium | M | **fixed** |
| 10 | 92 function-local imports, load-bearing and accidental mixed | Low‑Med | M | **fixed** |
| 11 | `core/` has no port/adapter seam (settings, HTTP, filesystem) | Low‑Med | L | **fixed** |
| 12 | A failed analysis marks the *recording* as failed | Medium | S | **fixed** |
| 13 | Dead-code leftovers (10 unused imports, 1 unused local) | Low | XS | **fixed** |

Three more defects were found while closing those two. All are fixed, and each
is described in the section it came out of: an analysis key that raised
`ImportError` and a bulk-tag picker that opened two modals (§8), and a
malformed `# noqa` that silenced nothing (§13).

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

**Fixed.** 38 names renamed across 50 files, as one commit with no behaviour
change. Three collisions had to be resolved first, all the same shape: a route
handler named after the operation it exposes (`get_llm_settings`,
`save_llm_settings`, `save_settings`) in the same module as the store function
it calls. Those two route modules reach the store through `import settings`,
which reads better anyway — it is obvious which side is the endpoint. Two local
variables named `settings` were shadowing that module.

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

The contract gap is unchanged and is the one open item left in this document
— see [Still worth doing](#still-worth-doing).

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

**Fixed.** `tui/actions.py` holds one function per operation. Callers keep
only what is genuinely theirs: the library screen passes the row it already
has, so a refusal can still be explained without a round trip, and asks not to
be navigated away from. The palette's type picker can now pass the per-type
arguments only the slash command could. A failed delete no longer leaves the
busy spinner up. `RETRYABLE` moved next to the action that applies it.

**The follow-up is now done too.** `tui/entries.py` takes the middle layer:
`Entry`, the six `entries_from_*` builders, the two picker variants, and the
`on_select` adapters an entry carries. Nothing in that file awaits, touches
`app.api` or pushes a screen, which is what makes the six payload shapes the
backend returns testable without a running app. `palette.py` keeps the widget
and the flows, and drops from 1000 lines to 800.

The builders and tab-completion had been spelling the row glyphs separately —
completion turns a picked entry back into a slash command by stripping `"♪ "`
or `"▣ "` off the display text, so the two had to agree by hand. One constant
each now. The three picker entrypoints that `commands.py` and the library
screen import also lost their leading underscore: §4's rule, which had only
ever been applied to the backend.

Two defects came out of that move, both of them things the split makes
harder to reintroduce:

- **The analysis key on a transcript raised `ImportError`.** The §8 rename
  dropped the underscore from `_open_analysis_type_picker` and updated
  `commands.py` but not `screens/transcript.py`. It survived because the
  import is deferred into the method body — which the TUI does deliberately
  and often, since the screens import the palette and the palette pushes the
  screens, so one direction can only happen at call time. Nothing checks a
  deferred import until the key is pressed. `tests/test_tui_deferred_imports.py`
  now resolves every one of them by reading the source; it fails on exactly
  this bug when the fix is reverted. A linter cannot catch this class of
  error — the name is only wrong relative to another module.
- **`open_bulk_tag_picker` started its worker twice**, fetching the tag list
  twice and stacking two identical modals.

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

**Fixed.** 456 lines down to 186. `core/job_lifecycle.py` takes recovery and
expiry, with `classify_interrupted` and `sweep_expired_jobs` split out so
neither can only be read; `meeting_watcher_host.py` takes the watcher, with
`wants_embedded` exposing the on/off/auto matrix without booting the app on
Windows; `releases.py` takes the poller, next to the functions it already
called. Both also stop printing to stdout and use the backend's loggers.

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

**Fixed.** 92 down to 44. 31 of those defer a heavy or optional dependency,
and the handful that break a cycle now say so on the line above. The config
paths became `import config` + `config.STORAGE_ROOT`, which hoists the import
while keeping the value read at call time — the reason it was local.

That last change exposed a real fragility. `test_config_lazy_mkdir` deleted
`config` from `sys.modules` to re-import it and never put the original back,
leaving two config modules in the session: the one every already-imported
module holds, and the one a later monkeypatch writes to. Nothing noticed while
the read happened inside an import statement. The test now restores what it
swaps out.

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
size.

**Fixed, as the seam rather than the layer.** `core/runtime_config.py` names
all five environment knobs, documents what each does and what a nonsense value
gets you, and makes them agree on what counts as "off" — one site had accepted
`0/false/no/off` while another compared to the string `"1"`.
`create_analysis_job`, `maybe_queue_auto_summary` and `build_job` take their
configuration as an optional argument, defaulting to the store. Every caller in
the app is unchanged; what is new is that the decision `maybe_queue_auto_summary`
makes about a hosted provider can be tested by handing it a config rather than
by writing `settings.json` and hoping.

A full port/adapter split is still not worth it here, and is not what was
done.

---

---

## 12. A failed analysis marks the *recording* as failed

Found while working through §1 and set aside as out of scope at the time.

`sync_job_to_db` (`core/job_helpers.py:105`) writes the job's status onto the
Recording row:

```python
rec.status = job.get("status", rec.status)
```

That is right for a transcription job, whose status *is* the recording's
status. It is wrong for the other two job types, which do not own the
recording at all. `handle_job_error` calls it on every failure, and
`cancel_job` (`routes/transcription.py:459`) calls it on every cancellation —
for analysis and translation jobs too.

So an analysis that fails because the LLM refused, or is simply cancelled,
leaves the transcript perfectly intact and the recording reading `error` or
`cancelled`:

```
recording status after a FAILED ANALYSIS: error
```

The consequences are visible: the library lists the recording as failed, and
because `error` is in `RETRYABLE` the UI offers to transcribe it again —
re-running a two-hour transcription to recover from a failed summary.

**Fixed.** `core/jobs.py` gained `JobType` — the four kinds, which were bare
strings in nine places — and `OWNS_RECORDING_STATUS`, the two that may write
the row. `sync_job_to_db` consults it; an analysis reports its own outcome on
the `Analysis` row, which already has a `status` column and is already set to
`error` by `process_analysis_job`.

The check itself is one line. Naming the distinction was the actual work, and
is why the fix is worth more than the line: the reason it was not already there
is that the four job factories (§1) never made it explicit, so there was
nowhere for it to be written down. `tests/test_job_recording_status.py`
reproduces the defect for both non-owning job types, pins the transcription
behaviour that had to survive, and asserts every `JobType` member is accounted
for — so the next job type added cannot inherit the wrong answer by default.

---

## 13. Dead-code leftovers

Small, and listed here so they are not rediscovered as findings:

- `backend/ffmpeg_helper.py` imports `sys`, `urllib.request` and `json`, none
  of which it uses.
- `backend/core/transcription.py` imports `COLAB_UPLOADING` from
  `core/messages.py` and never uses it — as does nothing else, so the constant
  is dead too.
- `tui/palette.py` imports `COMMANDS`, `tui/screens/search.py` imports
  `VerticalScroll`, `tui/widgets/waveform_view.py` imports `Segment`, none used.
- `tui/screens/library.py:380` assigns a local `name` that is never read.

`python -m pyflakes backend/ tui/` reports exactly these and nothing else, so
adding it to CI would keep the list at zero rather than letting it grow back.

**Fixed**, and the list above was wrong in both directions.

`COLAB_UPLOADING` is *not* dead — `core/colab_proxy.py` pushes it as a job
event. Only `transcription.py`'s import of it was unused; the constant stays.
Missed in the other direction: `tui/screens/import_.py` carries the
`TYPE_CHECKING` import of `AmicoTUI` that its six sibling screens use and it
never does, and the sweep had only looked at `backend/` and `tui/` — `tests/`
and `package_interactive.py` held nine more between them.

The CI check therefore runs over the whole tree, since the parts this section
did not look at are exactly the parts that had rotted. It is **ruff's `F`
rules, not pyflakes**: the same checks, but the repo already carries
`# noqa: F401` on the two imports that exist for their side effects — `db.py`
populating `SQLModel.metadata` before `create_all`, and the Windows-only
watcher dependencies — and pyflakes does not honour `noqa`, so it would have
failed on both. Only `F` is selected; nothing here is formatted for a style
ruleset, and turning one on would bury the signal this is for.

One of those two `noqa`s turned out to be malformed —
`meeting_watcher_host.py` wrote its reason where the code list goes, so it had
been silencing nothing.

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

## What it cost

All eleven, in this order: §2 (one `JobStatus` enum, two live bugs), §3 (the
data-loss swallow), §6 (delete `pipeline.py`), §1 (the single job factory),
§5 and §7 (the duplicated form builders on both sides of the API), §4 (the
underscore rename), §9 (splitting `main.py`), §10 (the local imports), §11 (the
config seam), §8 (`tui/actions.py`). One commit each.

Production code is **+381 lines net**. Seven new modules account for +935 of
that — `core/job_status.py`, `core/jobs.py`, `core/job_lifecycle.py`,
`core/runtime_config.py`, `meeting_watcher_host.py`, `tui/actions.py` — and a
large share of each is the docstring explaining what it is for and what its
absence cost. Everything they replaced shrank: `main.py` 456 → 186,
`api/routes/transcription.py` and `tui/commands.py` down by a third each.

Deduplication paid for itself inside the files it touched. It did not shrink
the tree, and was not meant to: the point is that each concept now has one
place to be changed, not fewer characters.

Tests grew by 915 lines across seven new files. The suite went from 649 to 749
passing, and stopped being flaky — see below.

### Closing §12, §13 and the §8 follow-up

Three more commits, in that order, +301 lines net across 29 files. Two new
modules: `tui/entries.py` (the palette's middle layer) and, in tests,
`test_job_recording_status.py` and `test_tui_deferred_imports.py`.
`tui/palette.py` went 1000 → 800.

The suite went 751 → 838. Most of that growth is one parametrised test:
`test_tui_deferred_imports` generates a case per deferred import in the TUI,
which is 77 of them today. It is cheap — it resolves names, it does not press
keys — and it is the only thing in the tree that checks that layer at all.

Worth noting what the three cost relative to what they were billed at. §12 was
estimated at "one line, plus a test" and the line was indeed one line; naming
`JobType` and `OWNS_RECORDING_STATUS` so the line had somewhere to live was the
rest of it. §13 was billed XS and turned up two errors in its own list plus
nine files it had not looked at. The §8 follow-up was billed as a tidy-up and
turned up two live defects, one of which had been shipping a broken keybinding.

Estimates written while reading code stay estimates about the code you read.

### Two things found along the way

**A flaky test, failing about one full run in eight.**
`test_a_failed_recording_can_be_transcribed_again` is not a product bug. The
worker loop is live under the test client, so a retry test that asserts the
recording is *queued* races the worker that legitimately picks it up — and
under the load of a full run the worker gets there first, fails the job (there
is no Whisper model in the test environment) and the row already reads `error`.
The `idle_worker` fixture holds the worker off for tests that are about
queueing rather than processing. Worth knowing when adding tests here: anything
that queues a job and then inspects state is racing a real background thread.

**A module split in two.** `test_config_lazy_mkdir` deleted `config` from
`sys.modules` to re-import it and never restored the original, so from then on
the session held two config modules — the one every already-imported module
references, and the one a later monkeypatch writes to. It was invisible while
every read happened inside a function-local `from config import …`, and
surfaced the moment those were hoisted in §10. The test now puts back what it
swaps out.

## Still worth doing

One thing, and it is not a defect — it is a decision nobody has made:

**Eight transcription options are offered to clients that do not exist.**
`compute_type`, `device`, `device_index`, `vad_filter`, `word_timestamps`,
`beam_size`, `best_of` and `force_normalize_audio` are declared on
`TranscriptionForm` and carried through `TranscriptionConfig`, and neither the
web frontend (`buildTranscriptionFormData`, which sends eight of the seventeen
fields) nor the TUI sends any of them. `to_options` falls back to the saved
Whisper settings, which is the right behaviour and is why nothing is broken.

Two coherent answers, and this document should not pick between them because
the choice is about who the API is for, not about the code:

- **Drop them.** The published surface shrinks to what is actually used, and
  the saved settings become the only way to set a device or a precision.
  Cheap, and it forecloses per-job overrides.
- **Keep them, and say so.** They are a real affordance for a scripted client
  — the one case where you want a precision for *this* job without changing a
  global setting. That is a legitimate reason for an unused parameter, but it
  needs to be written down, or the next reader files this finding again.

What should *not* happen is a third pass that leaves them undocumented and
unsent. Everything else in this document is closed.
