# Meeting watcher → AmicoScript

Local-only daemon (no MS Graph / cloud APIs). Detects a call from any
conferencing or chat app — **Teams, Zoom, Webex, Google Meet, WhatsApp,
Telegram, Signal, Slack, Discord, and more** — records the meeting audio, then
submits it to the normal AmicoScript transcription queue.

Runs on **Windows, macOS 14.2+, and Linux** (PulseAudio or PipeWire).

## Two ways it runs

- **Native install (packaged app):** nothing to set up. The watcher is bundled
  and runs **inside the app** as a background thread (`watcher.run_embedded`,
  started from `backend/main.py`). Just flip the **Meeting auto-capture** toggle.
  No separate process, login task, or setup script.
- **Docker:** the app runs in a container with no audio/host access, so it
  can't host the watcher. Run this script on the **host** instead — the web UI
  shows a one-time *"Set it up"* banner offering the right installer for your
  system (see [One-click setup](#one-click-setup-recommended)).

The shared loop is identical in both modes; only *where the loop runs* differs.
Force it with `AMICOSCRIPT_EMBEDDED_WATCHER=on|off|auto` (`auto` = in-process on
any desktop OS, external whenever the app is in a container).

## What differs per platform

Everything above the audio layer — the debounce loop, the app lists, the
mic heuristic, the mixer, the upload — is shared. Only detection and capture
are per-OS, behind `watcher_platform/`:

| | Windows | macOS | Linux |
|---|---|---|---|
| Detection | WASAPI audio sessions (`pycaw`), both device roles | `kAudioHardwarePropertyProcessObjectList` + `IsRunningInput`/`IsRunningOutput` | `pactl -f json list sink-inputs source-outputs` |
| System audio | WASAPI loopback (`pyaudiowpatch`) | Core Audio process tap in a private aggregate device | `parec` on the default sink's `.monitor` |
| Microphone | WASAPI default capture device | Core Audio IO proc on the default input | `parec` on the default source |
| Extra deps | pyaudiowpatch, pycaw, comtypes, winotify, pystray | none (ctypes) | none (`pactl`/`parec` from `pulseaudio-utils`) |
| Autostart | Scheduled Task at logon | launchd LaunchAgent | systemd `--user` unit |
| Notifications | `winotify` | `osascript` | `notify-send` |
| Tray icon | yes | no (needs the main thread) | no (needs a GTK loop) |
| Minimum OS | Windows 10 | **macOS 14.2** | PulseAudio 15 / PipeWire |

## How it works

0. **Enable** — only records while the **Meeting auto-capture** toggle in the
   AmicoScript sidebar is ON (the watcher polls `GET /api/settings`).
1. **Trigger** — polls which processes hold the speaker and the mic. A call is
   detected when either:
   - a **meeting app** (`AMICOSCRIPT_CALL_APPS`: Teams, Zoom, Webex…) is playing
     audio, **or**
   - with the mic heuristic on, **any app is on the mic *and* the speaker at
     once** — this catches browser meetings (Google Meet = `chrome`/`msedge`)
     **and chat-app calls** (`AMICOSCRIPT_CHAT_APPS`: WhatsApp, Telegram, Signal,
     Slack, Discord…).

   Must hold for `START_DEBOUNCE` seconds. Chat apps are deliberately *not* on
   the speaker-only list, so playing a WhatsApp voice note or a Telegram video
   clip does **not** trigger a recording — only a real two-way call does.

   The default app lists are per-OS, because process names are
   (`ms-teams.exe` / `Microsoft Teams` / `teams-for-linux`). On macOS both the
   executable name and the bundle ID are matched, so either form works in
   `AMICOSCRIPT_CALL_APPS`.
2. **Capture** — records the system output (= remote participants) plus your
   **microphone**, and mixes them to one mono 16 kHz WAV. No virtual cable and
   no admin rights on any platform. App-agnostic — the capture grabs whatever
   plays through the speakers regardless of which app.
3. **Transcribe** — on meeting end, POSTs the WAV to AmicoScript
   `/api/transcribe`. From there it uses the same queue, library, transcript,
   export, and optional Ollama analysis workflows as a manually uploaded file.

**Desktop notifications** fire on: recording started, recording stopped, and
transcription queued.

## Enable / disable

Open AmicoScript → sidebar → **Meeting auto-capture** → flip the toggle. State is
saved server-side; the watcher picks it up within ~5 s. Turning it off stops an
active capture immediately and prevents new captures.

## Setup

```powershell
pip install -r requirements.txt
```

AmicoScript must already be running (`python run.py`, default
`http://localhost:8002`).

## Run

```powershell
python watcher.py
```

## One-click setup (recommended)

| | Run | Registers |
|---|---|---|
| Windows | double-click **`setup.bat`** | Scheduled Task at logon, windowless via `pythonw.exe` |
| macOS | `bash ~/Downloads/setup.command` | launchd LaunchAgent |
| Linux | `bash ~/Downloads/setup.sh` | systemd `--user` unit (XDG autostart fallback) |

Each installs the Python dependencies, registers the watcher to start silently
at every login, and starts it. No admin rights anywhere. After this single
step, recording is controlled entirely from the AmicoScript web UI — you never
run anything manually again.

Only Windows can be double-clicked: a browser download on macOS/Linux is
neither executable nor un-quarantined, so those two are run with `bash`.

When AmicoScript runs in Docker it cannot install a helper on the host, so it
surfaces a one-time **“Auto-record your meetings? → Set it up”** banner offering
the right installer for the browser's OS. The banner disappears automatically
once the watcher is running (it heartbeats `POST /api/watcher/status`).

The watcher idles until you flip the **Meeting auto-capture** toggle ON, so it is
safe to leave installed.

### Manual equivalent

```powershell
# Windows
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1   # register + start
.\uninstall-windows.ps1                                          # stop + remove
```

```bash
# macOS / Linux — the installers create their own venv and install into it
bash install-macos.sh   ;  bash uninstall-macos.sh
bash install-linux.sh   ;  bash uninstall-linux.sh
```

On Windows, persistent env overrides are inherited by the task if set with
`setx` (e.g. `setx AMICOSCRIPT_MODEL medium`) — transient shell variables are
not. On macOS/Linux, put overrides in the generated plist/unit file.

### Building the native app with the embedded watcher

`package.py` bundles this watcher **only if its deps are installed in the build
environment** (it prints a warning and builds without it otherwise), so install
them before building:

```bash
pip install -r scripts/meeting_watcher/requirements.txt
python package.py
```

The requirements file is marker-driven, so that line is correct on every OS —
Windows pulls the WASAPI/tray stack, macOS and Linux get numpy and nothing
else. The release workflow runs it on all three runners, and
`scripts/smoke_test_bundle.py` fails the build if the watcher didn't make it
into the bundle.

On macOS `package.py` also injects `NSMicrophoneUsageDescription` and
`NSAudioCaptureUsageDescription` into the built `.app`'s `Info.plist`. Without
those keys macOS never prompts and the app records silence — see
[the permission section](#the-permission-and-why-it-is-unusual).

To try embedded mode from source (`python run.py`), install the same
requirements into that venv — otherwise the app logs *"Embedded meeting watcher
unavailable"* and you fall back to the external watcher. On macOS and Linux
that only means `numpy`, which the backend already needs.

## macOS

```bash
bash setup.command            # deps + LaunchAgent, or:
bash install-macos.sh         # register + start
bash uninstall-macos.sh       # stop + remove
```

Needs **macOS 14.2 or newer** — system-audio capture uses Core Audio *process
taps*, which do not exist before that. On an older Mac the watcher still runs
and still *detects* calls (the process list needs no such API); it reports
"recording system audio needs macOS 14.2+" in the sidebar and never captures.

### The permission, and why it is unusual

Recording the computer's audio is gated by TCC's *audio capture* service, and
macOS attributes the grant to **the app that launched the watcher**, not to the
watcher itself:

| How it runs | Who needs the permission |
|---|---|
| Packaged `.app` | AmicoScript. It carries `NSAudioCaptureUsageDescription`, so macOS prompts normally the first time a meeting is captured. |
| LaunchAgent (`install-macos.sh`) | The agent's own interpreter, `…/watcher/.venv/bin/python3`. The installer builds that venv with `python3 -m venv --copies` precisely so the grant belongs to a private binary at a stable path, instead of leaking to every script that shares a Homebrew interpreter. |
| `python run.py` in a terminal | **Your terminal app** (Terminal, iTerm…). |

The failure mode is the nasty part: when consent is missing, macOS does not
refuse. It creates the tap, clocks it, and delivers **pure silence** while
every API call reports success — and if the responsible app declares no
audio-capture usage (a terminal never does), you are not even prompted. So a
misconfigured Mac would otherwise record meetings containing nothing but your
own microphone.

The watcher therefore treats an all-silent system tap as an error: it logs it,
raises a desktop notification once, and reports it in the heartbeat so the
sidebar shows *"Helper running — no system audio was captured…"* instead of a
reassuring green *"Helper running"*.

To fix it: **System Settings › Privacy & Security › Screen & System Audio
Recording**, and enable the entry for whichever binary the table above names.
Enabling the *System Audio Only* variant is enough.

The microphone is a separate, ordinary permission and does prompt normally.

### Not yet on macOS

- **No menu-bar icon.** pystray's Darwin backend needs `NSApplication` on the
  main thread, which neither a watcher thread inside the app nor a LaunchAgent
  has. The web UI's recording chip and the notifications are the indicators.
- **Browser meetings are labelled oddly.** Safari routes audio through
  `com.apple.WebKit.GPU`, so a Meet call in Safari is detected by the mic
  heuristic but may be labelled from that process name rather than "Safari".

## Linux

```bash
bash setup.sh                 # deps + systemd user unit, or:
bash install-linux.sh         # register + start
bash uninstall-linux.sh       # stop + remove
```

Needs `pactl` and `parec` (`pulseaudio-utils` on Debian/Ubuntu,
`pulseaudio-utils`/`pipewire-pulse` elsewhere). Both PulseAudio and PipeWire
work — the watcher speaks the PulseAudio protocol, which `pipewire-pulse`
implements. On a host with neither, the watcher runs, reports why it cannot
capture, and never records.

Autostart is a systemd `--user` unit, falling back to an XDG autostart entry on
sessions without systemd. There is no tray icon (the AppIndicator backends are
unreliable and Wayland-hostile).

## Tray icon (pause / stop) — Windows only

The standalone watcher shows a **system-tray icon** (notification area, by the
clock — may sit in the `^` overflow; drag it onto the taskbar to pin). Colour =
state:

- 🟢 green — running, waiting for a call
- 🔴 red — recording now
- ⚪ grey — auto-capture is off

Hover for a status tooltip. **Right-click** for the menu:

- **Auto-capture (record calls)** — checkbox; pause/resume. Flips the *same*
  server toggle as the web UI, so both stay in sync.
- **Open AmicoScript** — opens the web UI.
- **Quit watcher** — stops the process. It restarts at next logon (scheduled
  task) or via `Start-ScheduledTask -TaskName "AmicoScript Meeting Watcher"`.

Needs `pystray` + `pillow` (in `requirements.txt`). Without them the watcher runs
headless. Disable the icon with `AMICOSCRIPT_TRAY=off`. The native build doesn't
show this icon (the app owns its own UI).

## Recording indicator in the web UI

While a meeting is being captured, the watcher heartbeats AmicoScript
(`POST /api/watcher/status`) and the web UI shows a red **Recording · &lt;app&gt;**
chip in the bottom-right stack (above the job-queue pill). If the watcher is
killed mid-call, the chip clears on its own within ~20 s (heartbeat TTL).

## Config (environment variables)

| Var | Default | Meaning |
|-----|---------|---------|
| `AMICOSCRIPT_URL` | `http://localhost:8002` | AmicoScript base URL |
| `AMICOSCRIPT_WATCHER_OUT` | `STORAGE_ROOT/meetings` (`~/.amicoscript/data/meetings`, or `./amicoscript-data/meetings` in portable mode) | where captured WAVs go |
| `AMICOSCRIPT_MODEL` | *(follows the app's sidebar)* | pin the Whisper model for auto-captured meetings |
| `AMICOSCRIPT_LANGUAGE` | *(follows the app's sidebar)* | pin the language (empty = auto-detect) |
| `AMICOSCRIPT_DIARIZE` | *(follows the app's Speakers toggle)* | pin speaker diarization on/off (needs an HF token in the app) |
| `AMICOSCRIPT_MIX_MIC` | `true` | mix mic into recording (`false` = remote only) |
| `AMICOSCRIPT_CALL_APPS` | *(per-OS, see below)* | meeting apps detected on **speaker alone** |
| `AMICOSCRIPT_CHAT_APPS` | `whatsapp,telegram,signal,messenger,slack,discord` | chat apps detected only on **mic + speaker** (avoids voice-note false triggers) |
| `AMICOSCRIPT_BLOCK_APPS` | *(per-OS, see below)* | never treat these as a meeting (keep browsers OUT) |
| `AMICOSCRIPT_MIC_HEURISTIC` | `true` | detect calls by mic+speaker concurrency (**required** for chat apps + web meetings) |
| `AMICOSCRIPT_WATCHER_BACKEND` | *(this platform)* | force a backend (`windows`/`macos`/`linux`), or point at an importable module |
| `AMICOSCRIPT_TRAY` | `true` | tray icon on/off (Windows only; ignored elsewhere) |
| `AMICOSCRIPT_LINUX_MONITOR` | `@DEFAULT_MONITOR@` | Linux: source to record system audio from |
| `AMICOSCRIPT_LINUX_SOURCE` | `@DEFAULT_SOURCE@` | Linux: microphone source |
| `AMICOSCRIPT_LINUX_CAPTURE_RATE` | `48000` | Linux: `parec` capture rate before the 16 kHz mixdown |

The app lists default per-OS, because process names are — `ms-teams.exe` vs
`Microsoft Teams` vs `teams-for-linux`, and a blocklist entry like `wmplayer`
means nothing on a Mac. See `APP_DEFAULTS` in `watcher_platform/__init__.py`;
setting the env var replaces the whole list for every platform.

Model, language and diarization are **not** watcher settings by default: the
watcher reads them from `GET /api/settings`, so an auto-captured meeting is
transcribed with exactly the options shown in the AmicoScript sidebar. Set the
env vars above only if you want auto-captures to differ from manual uploads.

Tuning constants (`START_DEBOUNCE`, `STOP_DEBOUNCE`, `MIN_MEETING_SECONDS`) are
at the top of `watcher.py`.

## Detection notes

- **Allowlist** = precise, low false-positive, but misses browser meetings.
- **Mic heuristic** = catches everything incl. browser meetings, but can
  false-trigger (e.g. a voice memo while music plays). The blocklist + debounce
  tame it. If a backend can't report microphone activity, the heuristic is
  skipped automatically (logged once) and the allowlist still works. In
  practice that only happens on Windows builds of `pycaw` without capture
  sessions, and on Linux without `pactl`.
- The watcher only knows *which* app from the matched process name; under the
  pure heuristic path the recording label uses the best-guess app.

## Caveats

- Capture records **all** system audio — keep music/notifications quiet.
- A long notification sound can false-trigger; raise `START_DEBOUNCE`.
- Diarization requires a Hugging Face token configured in AmicoScript.
- **Desktop clients only.** WhatsApp/Telegram calls are captured only when the
  call runs on the **desktop app** (audio goes through the computer's speaker +
  mic). Calls on your phone are not visible to the watcher.
- **Chat apps need the mic heuristic.** Where a backend can't see microphone
  activity, WhatsApp/Telegram/Signal/Slack/Discord calls won't be detected
  (Teams/Zoom still work via the speaker list).
- If you stay **muted the entire call** and the app releases the mic while
  muted, the heuristic may miss it. Most clients keep the mic session open when
  muted, so this is rare.
- ⚠️ **Consent.** Recording personal one-to-one calls (WhatsApp/Telegram are
  end-to-end encrypted and private) carries stricter all-party consent
  requirements than work meetings in many jurisdictions. Make sure every
  participant has agreed before recording.
