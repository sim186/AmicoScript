# Changelog

All notable changes to this project will be documented in this file.
This project adheres to Semantic Versioning (https://semver.org/) and the
Keep a Changelog format.

## [Unreleased]

### 🧹 A structural pass over the whole codebase

A review of the repository against clean-architecture layering and clean-code
rules on duplication, naming, function size and error handling. The finding
was that nothing is tangled — the same concept had simply been re-implemented
at each place it was needed instead of extracted once, and the copies had
drifted. Two of those divergences were live defects. The full write-up, with
what each fix cost, is in `docs/architecture-review.md`.

**Two bugs, both caused by five modules each keeping their own idea of which
job statuses mean "still running":**

- **Jobs disappeared from the queue widget while they were working.** The
  `/api/jobs` filter left out `loading_model`, `translating`, `running` and
  `streaming` — so a job vanished for the whole model load, and for the entire
  span of an LLM analysis, then reappeared. The two states it *did* filter on,
  `preparing` and `postprocessing`, are never job statuses at all.
- **A download or an analysis running longer than an hour was killed
  mid-flight.** The hourly cleanup treated anything not in its own (different,
  shorter) list as abandoned: temp files deleted, the job record replaced by a
  tombstone with no SSE queue. The worker carried on pushing progress nobody
  received, and cancel, logs, result and export all began answering 410. A
  playlist import or a chunked summary of a long meeting reaches an hour
  routinely. The audio itself was never at risk.
- `core/job_status.py` now holds the vocabulary and the sets derived from it,
  and a test fails if a new status is added without classifying it.

**A transcription could be lost with no error at all.** If the recording row
could not be written, the upload still answered 200, the worker transcribed the
whole file, and the step that saves the transcript found nothing to attach it
to and returned quietly. The user waited out a full run of a long recording and
got no transcript, no error and nothing in the log. The upload is now refused
with an explanation, and the audio it had already ingested is removed rather
than orphaned.

**Everything a job needs is now built in one place.** The job record was
assembled in four, with four different sets of keys, so every reader defended
itself with a default and the log handler repaired the type of its own log
buffer on first use. Also: the transcription form was declared twice on the
backend and three times on the frontend; the meeting watcher, job recovery, job
expiry and release polling moved out of `main.py` (456 lines to 186); 38
functions that were named private and imported everywhere lost their
underscore; and 92 function-local imports became 44, with the ones that defer a
heavy dependency or break a cycle now saying so.

**The terminal UI stopped behaving differently depending on how you asked.**
Each operation was written two or three times — once for the slash command,
once for the palette, once for a keybinding — and the copies had drifted.
`/retry` would fire a retry the library screen had just refused, and got a 409
back. Picking "translate" from the palette had no way to say which language,
though `/analyze` did. A failed delete left the busy spinner spinning. There is
now one implementation per operation.

**A failed summary no longer marks the recording as failed.** An analysis that
the LLM refused — or that you simply cancelled — wrote its own status onto the
recording, so a perfectly good transcript showed up in the library as `error`
and the UI offered to transcribe it again. Accepting that offer re-ran the
whole transcription, which for a long meeting is an hour of work to recover
from a failed summary. Only a transcription describes the recording now; an
analysis reports its outcome on its own row, where it always belonged. The
same applied to bulk translation, and is fixed the same way.

Relatedly: **an analysis cancelled before it started running stayed listed as
pending forever.** The cancel was recorded against the job but never against
the analysis itself, so the recording kept showing an analysis that was about
to run and never would.

**Two things in the terminal UI that were simply broken.** The analysis key on
a transcript screen had been raising an error instead of opening the type
picker, since a rename that missed this one caller. And choosing "tag" from
the bulk menu opened two identical pickers stacked on each other, because the
loader was started twice. Deferred imports across the TUI are now checked by a
test rather than by pressing every key.

**The transcription endpoints stopped advertising eight options nothing sent.**
`compute_type`, `device`, `device_index`, `vad_filter`, `word_timestamps`,
`beam_size`, `best_of` and `force_normalize_audio` were accepted as form fields
on `/api/transcribe` and `/api/transcribe/url`, and no client — not the web UI,
not the TUI — ever sent one; every job already ran on the saved Whisper
settings. Device and precision are now set only where they were already being
set, in Settings. Jobs run exactly as before. If you have a script posting to
these endpoints, any of the eight it sends is now ignored rather than rejected;
set the device and precision in Settings instead. `AMICO_WORD_TIMESTAMPS` also
works now — the form had been overwriting it on every job, so it had never had
any effect.

**Nothing else about how the app is used changed.** No other API contract, no
setting, no file on disk. The test suite went from 649 to 858 passing and
stopped being intermittently red — one test had been racing the live background
worker about one run in eight. CI also fails now on an unused import or an
unused local, which is what let the two broken TUI paths above go unnoticed.

### 📦 One download per platform, not four

- **There is no longer a CPU build and a GPU build.** The two differed only in
  whether the CUDA torch wheels and the nvidia CUDA libraries had been
  collected into them, which put the choice on the user at download time — in a
  filename, before they could know the answer. There is now one build per
  platform, and it decides at first use from what the machine's driver actually
  reports. Five release artifacts become three.
- **PyTorch is downloaded on first use rather than shipped.** Whisper never
  touches it: faster-whisper transcribes through CTranslate2, and every
  `import torch` in the backend is on the diarization path. So torch,
  torchaudio and pyannote are no longer bundled at all. The first job that asks
  for speaker labels — or the first job on a GPU machine, since CTranslate2's
  cuBLAS and cuDNN travel with them — fetches the right set. A CPU-only machine
  transcribing without diarization downloads nothing. Every bundle is smaller
  than the old CPU build was.
- **The wheels are pinned at build time, not resolved on your laptop.** The
  build records exact URLs and sha256 hashes, resolved against the versions the
  bundle carries so the two halves cannot disagree about numpy. Nothing is
  installed that does not match its recorded hash.
- **GPU detection no longer goes through torch**, which would be circular now
  that torch is the thing being decided about. The CUDA driver is asked
  directly, with an `nvidia-smi` fallback for WSL. `AMICO_GPU=0` overrides it,
  and `AMICO_RUNTIME_FLAVOUR` overrides the choice that follows from it.
- **A missing runtime is not a failed job.** Diarization that cannot fetch
  PyTorch is skipped with a reason in the log and the transcript still
  delivered, the same way a missing Hugging Face token already behaved.
- **Linux artifacts were silently missing from releases.** Two causes, both
  fixed. The zip step ended in `|| true`, so a failure left the job green and
  the release without an asset; and all the build jobs called the release
  action concurrently with `allowUpdates`, racing each other. Builds now hand
  their artifact to a single publish job, which refuses to publish at all
  unless all three platforms are present.
- **The release workflow can be run without cutting a tag.** A tag used to be
  the only way to find out whether `package.py` still worked, which made every
  release the first run of the release path. `workflow_dispatch` builds all
  three platforms and, by default, publishes nothing.
- **The smoke test asserts the packaging contract**, rather than trusting it:
  torch absent from the bundle, manifest present and non-empty. A build machine
  that happens to have torch installed now fails there instead of shipping a
  bundle that ignores the download it performs.
- See `docs/runtime-pack.md`. Docker is unaffected: both images install the
  whole stack, because a container should carry what it needs.

### 🖥️ GPU and CPU builds that actually differ

- **The saved device and precision settings were write-only.** The settings
  page offered `whisper_device` and `whisper_compute`, the terminal UI could
  set them, `/api/settings` returned them — and no transcription ever read
  them, because the route's own form defaults shadowed the stored values.
  Choosing "cuda" changed nothing. A job now takes the request's value, then
  the saved one, then `auto`.
- **Saving only the device silently did nothing.** The save was gated on
  `whisper_model` being present, so a request that set just the device or the
  precision was dropped on the floor.
- **The precision default is `auto`, not `float16`.** float16 is the right
  choice on a GPU and the wrong one on a CPU, where CTranslate2 has to emulate
  it; `auto` resolves against the device instead of guessing once.
- **Transcription says which device it got**, like diarization now does:
  `Transcribing on cuda:0 (float16)` in the job log, and a line in the progress
  message when a GPU was wanted and the CPU is what turned up.
- **The GPU release build shipped CUDA torch but no CUDA Whisper.**
  faster-whisper does not transcribe with torch — it uses CTranslate2, which
  loads cuBLAS and cuDNN at runtime. Those arrive as `nvidia-*` packages that
  nothing imports, so PyInstaller never bundled them: diarization used the GPU
  while Whisper quietly fell back to the CPU. They are collected now, and
  loaded from inside the bundle before CTranslate2 starts.
- **The Linux "CPU" release was secretly a CUDA build.** On Linux the default
  PyPI torch wheel is the CUDA one, so the CPU artifact carried gigabytes of
  nvidia libraries it could not use and was nearly identical to the GPU
  artifact. The release workflow now pins the CPU index there, as the
  Dockerfile already did.
- **Docker can use a GPU.** `Dockerfile.gpu` plus
  `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build`.
  The base image is a cuDNN one, not a bare CUDA runtime, because a missing
  cuDNN fails at the first transcription rather than at build time.

### ⚡ Diarization was running on the CPU

- **Speaker diarization now uses the GPU.** pyannote's `from_pretrained`
  returns a pipeline on the CPU, and moving it takes an explicit `.to(device)`
  that was never there — so on a machine with a working GPU, Whisper
  transcribed on it while diarization crawled along on the CPU beside it. It
  now follows the same device the transcription was given.
- **The pipeline is loaded once, not once per job.** Whisper's model has been
  cached since it was written; diarization reloaded itself from disk every
  time. Only the first diarized job now pays that cost.
- **A CPU run says it is a CPU run.** The job log records which device
  diarization used, and running on the CPU adds a line to the progress message
  saying it will be much slower. There was previously no way to tell from the
  outside which one you were getting.
- Asking for `cuda` on a machine without one falls back to the CPU rather than
  failing the job, and a GPU that cannot be reached at all — a driver mismatch,
  too little VRAM — is remembered, so the next job does not reload the model
  and fail the same move again.

### 💬 Ask your library

- **A question box above the library answers from every transcript at once.**
  Search could find a recording that contains a word; it could not answer "what
  did we decide about pricing" — that meant opening each hit and reading.
- **Every answer carries citations, and a citation is a link.** Click it and
  the recording opens at the second the passage was spoken, so a claim about
  your own recordings can be checked in one click rather than taken on trust.
  A citation the model invented — `[9]` when it was shown four passages — is
  dropped instead of displayed.
- **Nothing matched means nothing matched.** When retrieval comes back
  empty the model is never asked, so an empty-handed search cannot turn into a
  confident invented answer.
- **Transcripts are indexed as passages, not as files.** A two-hour recording
  is one useless unit of retrieval and cannot cite a minute; a diarized segment
  is often four words. The index sits in between, keeping the timestamps each
  passage spans. It is maintained automatically — written when a transcription
  finishes, rebuilt when you edit a segment or rename a speaker, and removed
  with the recording.
- **A question is ORed, not ANDed.** The search box ANDs its words, which is
  right when every word should narrow the result and wrong for a question:
  "what did we decide about pricing" ANDed matches nothing at all. Chat drops
  the glue words and ranks on what is left.
- **Semantic search is optional.** With an embedding model named in LLM
  Settings, a question about pricing also finds the passage that says "forty a
  seat" without using the word. The vectors come from the `/v1/embeddings`
  endpoint of the server you already configured — no second runtime, no new
  Python dependency — and the two rankings are combined so a passage only one
  of them found still reaches the model. Left empty, chat runs on keyword
  search and needs no setup at all.
- In the terminal UI as `/ask <question>`, with `/chat-index` to see or rebuild
  the index.

### 🏷️ Tags the app can suggest

- **"Suggest tags" reads the transcript and proposes topics.** Tagging was
  entirely manual: every tag had to be thought of, typed and applied by hand,
  which is why most libraries have three tags and four hundred recordings.
- **Nothing is applied on its own.** The model proposes, you click. A chip
  becomes a tag only when you accept it, and a tag already on the recording is
  never suggested again. An LLM that quietly relabels a library is worse than
  no tagging at all.
- **It is shown the tags you already use**, so a second standup is tagged
  `standup` and not `stand-up`, `daily-standup` or `Standup`. A suggestion that
  matches an existing tag apart from case comes back spelled your way and
  reuses that tag rather than creating a twin beside it.
- **A long recording is sampled across its whole length**, not truncated to the
  start — otherwise a two-hour meeting gets tagged by its opening small talk.
- **The reply is read forgivingly.** Small local models answer "reply with a
  JSON array" with fenced JSON, an array inside a sentence, a bulleted list, or
  `{"tags": [...]}`; all of those are understood. Prose that is not a list at
  all — an apology, an offer to help — yields no suggestions rather than a
  chip named "I'm sorry".
- Available in the terminal UI as `/tag-suggest <id>`, and over the API as
  `POST /api/recordings/{id}/suggest-tags`. Like every other feature that would
  send a transcript to a hosted provider, it refuses until you have said that
  is allowed.

### 📝 Markdown that a vault understands

- **The Markdown export now opens with YAML frontmatter** — title, date,
  duration (both as `31:15` and as seconds), language, speakers, tags, folder,
  source and the model that produced it. Obsidian, Hugo, Jekyll and Quartz all
  read that block as note properties; previously all of it was one bold line of
  prose, and the tags and folder were not in the file at all, because the
  formatter was only ever handed the transcript and never the recording it
  belonged to.
- **A tag with a space in it becomes `team-sync`.** Obsidian tags cannot contain
  whitespace, and a leading `#` belongs in the note body.
- **Speaker names can be exported as `[[wikilinks]]`**, so each person
  accumulates a note that backlinks every conversation they appear in. It is a
  checkbox next to the export buttons, `?wikilinks=true` on the export
  endpoints, and `/export <id> md wikilinks` in the TUI — off by default,
  because that syntax is literal noise outside a wiki-style vault.
- **A bulk export carries exactly one properties block.** Frontmatter is only
  frontmatter at the top of a file; the collection gets a summary block with
  the date span, every speaker and every tag, and each transcript below keeps
  the inline metadata line it has always had.
- **Titles are quoted, not trusted.** A recording aliased `- notes`, `no`, or
  anything containing a quote or a colon used to be a plausible way to produce
  a file whose frontmatter did not parse.

### ⌨️ The terminal UI caught up

- **Fixed a regression that erased your Hugging Face token.** When the API
  started masking secrets, the TUI kept reading the old field: its settings form
  showed an empty token and saving wrote that emptiness back. Opening settings
  and pressing Save was enough to lose the token. The form now shows the masked
  preview and tells the server to keep what it has unless you actually type
  something. The same protection covers the LLM API key.
- **`/retry <id>`** and `Ctrl+R` in the library re-run a failed, cancelled or
  interrupted transcription.
- **`/backup export [path]`** and **`/backup import <path> [overwrite]`** for the
  library bundle.
- **`/llm-providers`** lists the supported backends; **`/llm-detect`** scans for
  a running server and offers to use it, the same one-click flow the web UI has.
- **Settings gained provider, context budget, cloud consent and the
  auto-summarise toggle**, so the two interfaces configure the same things.
- **`/export` accepts `vtt` and `csv`**, and rejects an unknown format with the
  list of valid ones instead of a server error.
- **The library shows the new states**: `⚠ interrupt` and `⊘ cancelled` have
  their own marks, a captured meeting is prefixed `◉` and a link import `↗`.
- **Errors read like sentences.** A `401` now explains that the server wants a
  token and where to get it, a `410` points at the library, and an unreachable
  backend says so instead of surfacing a bare exception.
- **Short recordings show their length.** Durations were always rendered as
  hours and minutes, so a 22-second clip read `0h 00m`; under an hour it is now
  `0:22`. The MODEL column was always blank because the model lives inside
  `transcription_options` — it reads that now.

### 🔁 Recovering from a failed transcription

- **Transcribe again.** A recording that failed, was cancelled, or was
  interrupted by a restart now has a retry button in the library. The audio was
  always still on disk, but the only way to try again was to delete the
  recording and re-import the file. Retries reuse the original model, language
  and diarization settings.
- **The reason is shown, not hidden in a tooltip.** An `interrupted` recording
  explains itself in the card ("Interrupted by an app restart"), which a touch
  screen could not surface before.
- **Recordings say where they came from.** An auto-captured call and a file you
  dragged in used to look identical; captures and link imports now carry a badge.
- **Automatic summaries are labelled.** A summary AmicoScript produced by itself
  is marked "automatic", so an unexpected entry in the analysis history is not a
  mystery.

### 🐛 Fixes found by running the app

- **Creating a tag that already exists returned HTTP 500.** It hit the database's
  unique constraint and surfaced as a server error with no message. It is now a
  409 that names the clash, and renaming a tag onto an existing name is caught
  the same way.
- **API errors were shown to the user as raw JSON** — `Save failed:
  {"detail":"…"}`. Folder, tag and library actions now show the sentence the
  server actually sent.
- **The password fields were not inside a form** and had no username field, so
  browsers warned about them and password managers had nothing to associate a
  saved credential with. The Hugging Face and LLM key fields are now marked so
  password managers leave them alone entirely — they are tokens, not logins.
- Library rows carry a `data-recording-id`, so a row can be identified without
  counting its position in the list.

### 🧠 LLM setup that does not require guesswork

- **Pick your tool from a list.** LLM Settings now offers presets for Ollama, LM
  Studio, Unsloth Studio, llama.cpp, vLLM, Jan, LocalAI, OpenRouter and "anything
  OpenAI-compatible", each filling in the right address, saying whether an API key
  is required and what it looks like, and linking to that tool's setup guide.
- **Find running servers** scans the well-known ports and reports what answered,
  which models it has loaded and whether it wants a key. One click adopts it —
  provider, address and a model are filled in. A server that answers 401 still
  shows up, marked as needing a key, which is Unsloth Studio's normal state.
- **Paste the address in any form.** Every one of these tools displays a URL
  ending in `/v1`, but AmicoScript appends `/v1/chat/completions` itself, so
  pasting what LM Studio showed you produced `/v1/v1/…` and a 404 that looked
  like the server was broken. `http://localhost:1234`, `.../v1`, a full endpoint
  URL and a bare `localhost:1234` now all resolve to the same thing, and the UI
  shows what it changed.
- **Docker works out of the box.** `docker-compose.yml` maps
  `host.docker.internal` to the host gateway — Docker Desktop provides it, Linux
  does not, which is why pointing a container at `localhost:11434` never worked
  there. AmicoScript also detects that it is containerised and rewrites
  `localhost` addresses to the host alias, saying so, and scans the host rather
  than the container.
- **Failures explain themselves.** Instead of a raw exception, the connection
  test says which tool is not running, that the key was rejected and what a valid
  one looks like, that the address has a stray `/v1`, or that the server answered
  in a format that is not OpenAI's.
- **LLM Settings moved to the main sidebar.** It used to live in the transcript
  panel, so it was only reachable after transcribing something — you had to
  produce a transcript before you could configure the thing that analyses it.

### ☁️ Hosted providers, behind a door

- OpenRouter and any other remote endpoint are supported, and gated. Audio never
  leaves your machine either way, but a hosted provider receives the transcript
  text, so it takes an explicit confirmation. Until you give it, manual analyses
  and automatic meeting summaries both refuse to run and say why.
- OpenRouter requests carry the attribution headers it documents.

### 🔐 Access control

- **AmicoScript now refuses network requests until a password is set.** The
  project documents a Traefik deployment on a public domain, but every API route
  was open there — anyone who found the hostname could read the library, download
  the audio and read the stored Hugging Face token out of `GET /api/settings`.
  Requests from the machine AmicoScript runs on behave exactly as before, with no
  password and no prompt; requests from anywhere else are refused with an
  explanation until a password exists. Exposing the app unconfigured now fails
  closed instead of silently publishing your transcripts.
- **Security panel** in the sidebar to set, change or remove the password, and to
  read the API token that headless clients (the TUI, the meeting watcher) use.
  `AMICOSCRIPT_PASSWORD` sets it at startup; `AMICOSCRIPT_AUTH=always` requires a
  session even locally; `AMICOSCRIPT_AUTH=off` disables the layer for deployments
  that put their own authentication in front.
- **Secrets are no longer echoed back to clients.** `GET /api/settings` reports
  whether a Hugging Face token is stored and shows its last four characters;
  `GET /api/llm/settings` reports whether an API key is set. Saving a form no
  longer risks overwriting a stored credential with its own placeholder.
- Login attempts are throttled after repeated failures, and the loopback check
  reads the direct peer address rather than `X-Forwarded-For`, which a caller
  controls.

### ✨ Library export and import

- **Your library is now portable.** Export everything — recordings, transcripts,
  analyses, folders and tags — as a single zip from **Backup** in the sidebar, and
  import it on another machine or after a reinstall. Until now a library existed
  only inside `~/.amicoscript` with no backup path and no way to move it.
- Import matches rows by id, so re-importing the same bundle is a no-op rather
  than a duplicated library; `Overwrite` replaces existing rows instead.
- Bundles deliberately exclude `settings.json` — it holds your Hugging Face
  token, LLM API key and password hash, none of which should travel in a file you
  email to yourself. Imports reject path traversal entries and zip bombs.

### 📤 New export formats

- **WebVTT** (`.vtt`) — the subtitle format browsers accept in `<track>`.
  Speakers become `<v Name>` voice spans rather than text baked into the caption.
- **CSV** (`.csv`) — one row per segment with both raw and human-readable
  timestamps, speaker, text, translation and an edited flag. Written with a BOM
  so Excel reads accents correctly, and leading `=`/`+`/`-`/`@` in transcript text
  is defused so a spreadsheet cannot execute it as a formula.
- Both are available for single recordings, in the bulk-export menu, and from the
  TUI's `/export` command.

### 🤖 Long transcripts no longer get silently truncated

- **AI analysis handles recordings larger than the model's context window.** A
  one-hour meeting is roughly 12k tokens and Ollama defaults to 4096, so the
  model was quietly dropping the *beginning* of the transcript and returning a
  confident summary of the last few minutes. Anything over the configured budget
  is now summarised in parts and merged, with progress reported per part.
  Translation concatenates its parts instead of merging them, because merging
  would rewrite the translation.
- The context budget is configurable under AI Analysis (default 8192 tokens).
- Analyses fall back to a non-streaming request when a server does not deliver
  SSE, instead of completing with an empty result.

### ✨ Automatic meeting summaries

- Turn on **Summarise automatically** under Meeting auto-capture and every
  finished call is summarised by your LLM without being asked. Fires only for
  captured calls, only when an LLM is configured, and only once per recording.

### 🔧 Reliability

- **A restart no longer destroys work in progress.** Interrupted recordings used
  to be flipped to `error` with no explanation — a two-hour meeting that was 90%
  transcribed was simply lost. Anything whose audio is still on disk is requeued
  automatically; anything that cannot be resumed is marked `interrupted` with a
  reason the library shows on hover. `AMICOSCRIPT_RESUME_JOBS=0` restores the old
  behaviour.
- **Finished jobs leave a tombstone** when they are evicted from memory after an
  hour, so `/api/jobs/{id}/…` answers 410 with the recording id instead of a 404
  that looked like the job never existed.
- **URL imports download while the previous job is still transcribing.** Model
  inference is still strictly one at a time, but fetching audio is network-bound
  and no longer waits behind it — importing a playlist is roughly twice as fast.
  Tune with `AMICOSCRIPT_DOWNLOAD_CONCURRENCY` (default 2).
- **Schema changes are versioned migrations.** They used to be ad-hoc `ALTER
  TABLE` statements wrapped in `except: pass`, so a failed upgrade left a broken
  database looking healthy. Steps are numbered, recorded in a `schema_version`
  table, and fail loudly; a database from a newer build is refused rather than
  guessed at.
- **Search no longer breaks on ordinary punctuation.** `covid-19`, `C++`,
  `hello "world` and a bare `AND` were all FTS5 syntax errors that silently
  downgraded the search to a slower, different query. Terms are now escaped
  properly, quoted phrases are honoured, and the last word is treated as a prefix
  so results narrow as you type.

### 🧹 Maintenance

- **The frontend is a set of ES modules.** `index.html` carried a single
  4,800-line `<script>` block — the largest file in the repository by a wide
  margin. It is now 20 modules under `frontend/js/`, loaded natively by the
  browser. Still no build step, no bundler, no dependencies.
- **Route-level tests.** The suite had 128 unit tests over helper functions and
  not one that exercised an HTTP route; `test_search_escaping.py` even
  re-implemented the code it claimed to test, so it would have passed with the
  escaping deleted. There are now 329 tests covering upload, export, editing,
  deletion, search, backup round-trips, authentication, migrations, job recovery
  and analysis chunking.
- **CI runs the whole suite.** The workflow named ten test files explicitly, so
  everything added since was never run.
- The test suite no longer reads or writes the developer's real
  `~/.amicoscript` library.



## [1.16.0] - 2026-08-02
### 🖥️ Native desktop window

- **The app opens in a real window instead of a browser tab.** The packaged build now draws the UI in the webview the OS already ships — WKWebView on macOS, WebView2 on Windows — so no Chromium is bundled and the download does not grow. Closing the window shuts the backend down cleanly.
- **Choose how the UI opens** with `AMICOSCRIPT_UI`: `window` (default), `browser` (serve and open your default browser, the old behaviour), or `none` (serve only). `AMICOSCRIPT_NO_BROWSER=1` still forces a headless backend, so the TUI, Docker and CI are unaffected.
- **Graceful fallback:** if no webview engine is available the app opens a browser tab instead of failing. Source checkouts without `pywebview` installed behave exactly as before.
- **Exports, text selection and zoom work in the window.** pywebview disables downloads, text selection and zoom by default; all three are enabled here, and the window keeps a persistent profile under the storage root so sidebar settings survive a restart.

### 📦 Offline-first frontend

- **Tailwind, marked, WaveSurfer and the Inter font are now bundled with the app** instead of being fetched from CDNs on every load. The UI renders with no network at all, which is what a local-first, privacy-focused app should have been doing from the start. Assets are pinned by version and add ~770 KB.

### ⚠️ Known changes in behaviour

- **Closing the window now quits the app.** On Windows this also stops the embedded meeting watcher, which previously kept running after you closed the browser tab. Set `AMICOSCRIPT_UI=browser` to restore the old behaviour; a tray icon that outlives the window is planned.
- **Linux keeps opening a browser tab.** Its webview backend (WebKitGTK) lives in system packages that cannot be bundled into a portable build. A native Linux window is planned via a different desktop shell.

### 🔧 Maintenance

- New `docs/desktop-shell.md` documents the window architecture, packaging details and the planned Tauri sidecar migration.
- New `tests/test_frontend_assets.py` fails the build if a CDN reference reappears in the frontend or a bundled asset goes missing.

## [1.15.0] - 2026-08-02
- No change details provided.

## [1.14.0] - 2026-08-02
- No change details provided.

## [1.13.0] - 2026-08-02
### ✨ Meeting auto-capture (Windows, beta)

- **Automatic meeting recording:** A background helper detects an in-progress call — Teams, Zoom, Webex, Google Meet in a browser, plus WhatsApp/Telegram/Signal/Slack/Discord voice calls — records both system audio (WASAPI loopback) and your microphone, and submits the result to the normal transcription queue when the call ends. Detection is fully local (pycaw audio-session inspection); no meeting APIs, no cloud.
- **Driven from the web UI:** New "Meeting auto-capture" section in the sidebar toggles recording on/off. The helper polls the toggle, so nothing records until you opt in.
- **Bundled with the Windows app:** The native build runs the watcher in-process — no separate install, no scheduled task. Docker and source installs can still install the standalone helper via a one-click `setup.bat`, offered by a first-run banner that disappears once the helper is alive.
- **Live recording indicator:** A red "Recording" chip with an elapsed timer and the detected app appears in the bottom-right stack while a meeting is being captured, and the finished transcript opens automatically once it's ready. A crashed helper can't leave the chip stuck on — the server expires a stale heartbeat.
- **Helper update prompt:** The watcher reports its version in its heartbeat; the UI offers a one-click in-place update when an installed helper is older than the one shipped with the running app.
- **Tray icon:** colour-coded status (grey off / green idle / red recording) with pause/resume auto-capture and "Open AmicoScript". Shown by the embedded watcher too, so the native app always has a visible recording indicator even with the browser tab closed.
- **Captures are written at 16 kHz mono**, the rate Whisper transcribes at — a 2 h meeting is ~230 MB instead of ~700 MB. Decimation goes through a windowed-sinc low-pass so nothing above 8 kHz aliases back into the speech band.

### 🐛 Fixes

- **Auto-captured meetings now use your actual transcription settings.** The watcher previously hard-coded `diarize=true` and `model=small` and never sent a language, so every recorded meeting was diarized regardless of the sidebar Speakers toggle. Model, language and diarization are now read from the app's saved settings, which the web UI keeps in sync; `AMICOSCRIPT_MODEL` / `AMICOSCRIPT_LANGUAGE` / `AMICOSCRIPT_DIARIZE` remain available to pin an option for auto-captures only.
- **No more Windows-only setup nag on macOS/Linux/Docker browsers:** the `setup.bat` onboarding banner and download link are hidden unless the browser is running on Windows.
- **Uninstalling the helper actually stops it:** `uninstall-windows.ps1` now kills the running watcher process instead of leaving it recording until the next logoff.
- **Orphaned capture scratch files** (`capture-*.raw`, hundreds of MB after a hard kill mid-meeting) are cleaned up on watcher start.
- **Long meeting uploads no longer time out** at 60 s.
- **The Windows release actually ships the watcher.** The release workflow never installed `scripts/meeting_watcher/requirements.txt`, so `package.py`'s dependency check silently dropped the embedded watcher from the bundle and meeting auto-capture was dead in the packaged app. The build now installs them, warns loudly if they're missing, and bundles the tray dependencies too.

### 🔧 Maintenance

- **Repo rename:** in-app GitHub links (repo, changelog, issues, releases, Colab notebook) now point at `sim186/AmicoScript` instead of the old `sim186/amico-script`.
- **Watcher tests run in CI:** `test_meeting_watcher.py` and `test_watcher_status.py` existed but were missing from the workflow's explicit test list.

## [1.12.2] - 2026-06-03
### ✨ UI

- **Floating queue widget:** Active jobs now surface in a bottom-right pill showing the live count. Click to expand the panel listing each non-terminal job (filename, status, progress bar) with per-row cancel and click-to-attach. Replaces the old full-page processing card and the duplicated amber transcript strip with a single source of truth.
- **Enqueue while running:** Drop zone and URL field stay visible during transcription, so additional files/links can be queued without waiting for the current job. Inline status bar above the drop zone shows the attached job's filename, message, percentage, and a "View" shortcut to the transcript view.
- **Transcript tab pulses** while a job is running, making the running state discoverable without the modal card.
- **Foldable Transcribe sidebar sections:** Model, Language, Speakers, Cloud Power, and Benchmark are now collapsible `<details>` blocks. BETA badge moved from "Cloud Power" to "Speakers" to reflect the actual experimental surface.
- **Export + New moved into the Transcript sidebar** as a compact "Actions" section (grid of JSON/SRT/TXT/MD plus full-width "+ New transcription"), freeing the top tab bar at narrow widths.

### ⚡ Performance

- **Diarization progress is real:** Mapped pyannote's `ProgressHook` step events (segmentation → embeddings → clustering → discrete diarization) into the 0.82 → 0.95 progress range with step labels, replacing the previous frozen 82% indicator. Falls back gracefully if the installed pyannote build lacks the hook kwarg.

### 🐛 Fixes

- **Cancel is now real for queued and running jobs:** `_process_job` checks `cancel_flag` before starting, the `/cancel` route terminalizes the job immediately (SSE event + DB sync), `list_jobs` filters out cancelled jobs, and the URL download phase honours cancellation via a yt-dlp progress hook. Diarization checks the flag before/between/after the pyannote pipeline call. Cancel × in the queue panel removes the row optimistically.
- **Diarization compatibility with pyannote.audio 3.4+** ([#24](https://github.com/sim186/amico-script/issues/24)): `Pipeline.from_pretrained` is now called with the auth kwarg supported by the installed pyannote version (`token` or `use_auth_token`), fixing `TypeError: Pipeline.from_pretrained() got an unexpected keyword argument 'token'`. Thanks to [@Tiritibambix](https://github.com/Tiritibambix) for the detailed report and analysis.
- **Diarization under huggingface_hub 1.0+ and torch 2.6+:** Added a `torch.load` shim that allowlists `TorchVersion` and defaults `weights_only=False` for trusted pyannote checkpoints, and pinned `huggingface_hub<1.0` so pyannote internals that still call `hf_hub_download(use_auth_token=...)` keep working. Fixes `hf_hub_download() got an unexpected keyword argument 'use_auth_token'` and `Weights only load failed ... TorchVersion not an allowed global` on fresh Docker builds.
- **Export filenames with non-latin-1 characters** ([#25](https://github.com/sim186/amico-script/issues/25)): Export endpoints now emit RFC 5987 `Content-Disposition` headers, so filenames containing curly apostrophes (`’`), accented letters, CJK, etc. no longer trigger `UnicodeEncodeError: 'latin-1' codec can't encode character`. Thanks again to [@Tiritibambix](https://github.com/Tiritibambix) for spotting it.

### 🔌 API

- **`GET /api/jobs`:** Lists non-terminal jobs with `id`, `status`, `progress`, `filename`/`source_url`, `position`, and `created_at`, sorted by creation time. Cancelled jobs (`cancel_flag` set) are filtered out so the UI can drop them immediately.
- No change details provided.

## [1.12.1] - 2026-05-13
### ⚡ Performance

- **Benchmark elapsed time:** Each model result now includes an **Elapsed** column (load + inference combined). Total wall-clock time for the full benchmark run shown below the table and included in shared results.
- No change details provided.

## [1.12.0] - 2026-05-12
### ⚡ Performance

- **Benchmark tool:** New **Benchmark** section in the transcribe sidebar. Runs tiny/small/medium Whisper models against a standard 11 s reference clip and reports load time, inference time, and RTF (real-time factor) for each. Results can be shared to the community via a pre-filled GitHub issue. See [BENCHMARKS.md](BENCHMARKS.md) for community results.
- No change details provided.

## [1.11.0] - 2026-05-12
### ✨ UI

- **Recording alias** ([#7](https://github.com/sim186/amico-script/issues/7))**:** Rename any recording with a display name independent of the source filename. Alias shown in library card title; original filename visible as subtitle. Used as title in Markdown exports.
- **Transcript tab decluttered:** Collapsed inner tab bar, separate search bar, and segment selection bar into a single compact toolbar — reducing pre-content chrome from 5 bands to 2.
- **AI Analysis slide-over:** AI Analysis moved from a sub-tab to a slide-over panel (lightbulb icon), keeping the transcript always visible.
- **Contextual bulk toolbar:** Toolbar switches from search mode to speaker-assign mode when segments are selected; search and bulk controls no longer stack vertically.
- **Edited segment indicator:** Replaced text badge with amber left border already present on the segment card.
- **Bulk speaker assignment:** Select multiple segments and assign a speaker name in one action via the contextual toolbar.
- **AI result markdown rendering:** AI Analysis output now renders as formatted markdown (headings, lists, code blocks, tables, blockquotes) instead of plain text.

### 📤 Export

- **Enhanced Markdown export** ([#12](https://github.com/sim186/amico-script/issues/12))**:** Speaker runs merged into paragraphs, timestamp only at start of each speaker turn, metadata header includes duration, language, speaker list, and date.
- **Bulk Markdown export** ([#12](https://github.com/sim186/amico-script/issues/12))**:** Selecting multiple recordings and exporting as MD now produces a single combined file with a table of contents and `---` separators between recordings (previously downloaded N separate files).
- No change details provided.

## [1.10.5] - 2026-05-05
- Fix torch/torchaudio dependency caps for Python 3.13 and lightning compatibility

## [1.10.4] - 2026-04-30
- Error in transcription.py #22 (thanks for @nyfon for reporting it)

## [1.10.3] - 2026-04-21
- Fix GPU release build
- No change details provided.

## [1.10.2] - 2026-04-19
- Creating distributable with GPU enabled;

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
