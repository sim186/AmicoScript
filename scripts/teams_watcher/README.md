# Meeting watcher → AmicoScript

Local-only daemon (no MS Graph / cloud APIs). Detects a call from any
conferencing or chat app — **Teams, Zoom, Webex, Google Meet, WhatsApp,
Telegram, Signal, Slack, Discord, and more** — records the meeting audio, then
drives a running AmicoScript instance to transcribe it and write a markdown
report (summary + action items).

## Two ways it runs

- **Native install (Windows .exe):** nothing to set up. The watcher is bundled
  and runs **inside the app** as a background thread (`watcher.run_embedded`,
  started from `backend/main.py`). Just flip the **Meeting auto-capture** toggle.
  No separate process, scheduled task, or `setup.bat`.
- **Docker:** the app runs in a Linux container with no audio/host access, so it
  can't host the watcher. Run this script on the **Windows host** instead — the
  web UI shows a one-time *"Set it up"* banner that installs it (see
  [One-click setup](#one-click-setup-recommended)).

The detection/capture logic below is identical in both modes; only *where the
loop runs* differs. Force it with `AMICOSCRIPT_EMBEDDED_WATCHER=on|off|auto`.

## How it works

0. **Enable** — only records while the **Meeting auto-capture** toggle in the
   AmicoScript sidebar is ON (the watcher polls `GET /api/settings`).
1. **Trigger** — polls Windows audio sessions (`pycaw`). A call is detected when
   either:
   - a **meeting app** (`AMICOSCRIPT_CALL_APPS`: Teams, Zoom, Webex…) is playing
     audio, **or**
   - with the mic heuristic on, **any app is on the mic *and* the speaker at
     once** — this catches browser meetings (Google Meet = `chrome`/`msedge`)
     **and chat-app calls** (`AMICOSCRIPT_CHAT_APPS`: WhatsApp, Telegram, Signal,
     Slack, Discord…).

   Must hold for `START_DEBOUNCE` seconds. Chat apps are deliberately *not* on
   the speaker-only list, so playing a WhatsApp voice note or a Telegram video
   clip does **not** trigger a recording — only a real two-way call does.
2. **Capture** — records system output via WASAPI **loopback** (= remote
   participants) plus your **microphone** (`pyaudiowpatch`), mixes them to one
   mono WAV. No virtual cable, no admin rights. App-agnostic — loopback grabs
   whatever plays through the speakers regardless of which app.
3. **Report** — on meeting end, POSTs the WAV to AmicoScript `/api/transcribe`,
   waits, then runs `summary` + `action_items` analyses and writes
   `<app>_<timestamp>.report.md` next to the WAV (header notes the detected app).

**Desktop toasts** (via `winotify`) fire on: recording started, recording
stopped, and report ready.

## Enable / disable

Open AmicoScript → sidebar → **Meeting auto-capture** → flip the toggle. State is
saved server-side; the watcher picks it up within ~5 s. Turning it off prevents
*new* captures; a meeting already in progress finishes and is still reported.

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

**Double-click `setup.bat`.** It installs the Python dependencies, registers the
watcher to start silently at every logon (windowless via `pythonw.exe`, no admin
rights), and starts it. After this single step, recording is controlled entirely
from the AmicoScript web UI — you never run anything manually again.

Because AmicoScript runs in Docker, the app itself can't install a host helper,
so it surfaces a one-time **“Auto-record your meetings? → Set it up”** banner
that downloads this same `setup.bat`. The banner disappears automatically once
the watcher is running (it heartbeats `POST /api/watcher/status`).

The watcher idles until you flip the **Meeting auto-capture** toggle ON, so it is
safe to leave installed.

### Manual equivalent

```powershell
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1   # register + start
.\uninstall-windows.ps1                                          # stop + remove
```

Persistent env overrides are inherited by the task if set with `setx`
(e.g. `setx AMICOSCRIPT_MODEL medium`) — transient shell variables are not.

### Building the native app with the embedded watcher

`package.py` / `AmicoScript.spec` bundle this watcher **only if its deps are
installed in the build environment**, so install them before building on Windows:

```powershell
pip install -r scripts/teams_watcher/requirements.txt
python package.py
```

To try embedded mode from source (`python run.py`) on Windows, install the same
requirements into that venv — otherwise the app logs *"Embedded meeting watcher
unavailable"* and you fall back to the external watcher.

> **macOS** is not yet supported (the capture/detection stack is Windows-only:
> WASAPI loopback + `pycaw`). A `launchd` LaunchAgent + Core Audio / ScreenCaptureKit
> port is planned.

## Tray icon (pause / stop)

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
| `AMICOSCRIPT_WATCHER_OUT` | `STORAGE_ROOT/meetings` (`~/.amicoscript/data/meetings`, or `./amicoscript-data/meetings` in portable mode) | where WAVs + reports go |
| `AMICOSCRIPT_MODEL` | `small` | Whisper model |
| `AMICOSCRIPT_DIARIZE` | `true` | speaker diarization (needs HF token in app) |
| `AMICOSCRIPT_MIX_MIC` | `true` | mix mic into recording (`false` = remote only) |
| `AMICOSCRIPT_CALL_APPS` | `teams,zoom,webex,gotomeeting,bluejeans,whereby,ringcentral` | meeting apps detected on **speaker alone** |
| `AMICOSCRIPT_CHAT_APPS` | `whatsapp,telegram,signal,messenger,slack,discord` | chat apps detected only on **mic + speaker** (avoids voice-note false triggers) |
| `AMICOSCRIPT_BLOCK_APPS` | `spotify,vlc,wmplayer` | never treat these as a meeting (keep browsers OUT) |
| `AMICOSCRIPT_MIC_HEURISTIC` | `true` | detect calls by mic+speaker concurrency (**required** for chat apps + web meetings) |

Tuning constants (`START_DEBOUNCE`, `STOP_DEBOUNCE`, `MIN_MEETING_SECONDS`) are
at the top of `watcher.py`.

## Detection notes

- **Allowlist** = precise, low false-positive, but misses browser meetings.
- **Mic heuristic** = catches everything incl. browser meetings, but can
  false-trigger (e.g. a voice memo while music plays). The blocklist + debounce
  tame it. If your `pycaw` build can't enumerate mic sessions, the heuristic is
  skipped automatically (logged once) and the allowlist still works.
- The watcher only knows *which* app from the matched process name; under the
  pure heuristic path the report still records the best-guess app label.

## Caveats

- Loopback records **all** system audio — keep music/notifications quiet.
- A long notification sound can false-trigger; raise `START_DEBOUNCE`.
- Diarization requires a Hugging Face token configured in AmicoScript.
- **Desktop clients only.** WhatsApp/Telegram calls are captured only when the
  call runs on the **desktop app** (audio goes through the PC speaker + mic).
  Calls on your phone are not visible to the watcher.
- **Chat apps need the mic heuristic.** If `pycaw` can't enumerate mic sessions
  on your build, WhatsApp/Telegram/Signal/Slack/Discord calls won't be detected
  (Teams/Zoom still work via the speaker list).
- If you stay **muted the entire call** and the app releases the mic while
  muted, the heuristic may miss it. Most clients keep the mic session open when
  muted, so this is rare.
- ⚠️ **Consent.** Recording personal one-to-one calls (WhatsApp/Telegram are
  end-to-end encrypted and private) carries stricter all-party consent
  requirements than work meetings in many jurisdictions. Make sure every
  participant has agreed before recording.
