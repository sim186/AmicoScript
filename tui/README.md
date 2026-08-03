# AmicoScript TUI

Terminal interface for AmicoScript. Wraps the FastAPI backend over HTTP/SSE.

## Install

```bash
pip install -r tui/requirements.txt
```

## Run

```bash
python -m tui                              # spawn local server, attach
python -m tui --no-server                  # attach to already-running server
python -m tui --api-url http://host:8002   # remote server
```

The TUI launches `run.py` as a subprocess if no backend responds at the API
URL. The subprocess inherits `AMICOSCRIPT_NO_BROWSER=1` so it does not pop a
browser window. On exit (Ctrl+C / `q` / `/quit`) the subprocess is terminated.

## Keys

The TUI is **modeless and palette-driven** — no tabs. Press `Space` to arm
the leader; the status bar lists the available chords. Press `/` or
`Ctrl+K` to open the unified fuzzy palette (commands + recordings + jobs).

### Leader chords (Space + …)

Available on the welcome screen and every full-screen view; the set
varies per screen and is shown in the status bar when armed.

| Chord | Action |
|-------|--------|
| `Space l` | Library |
| `Space j` | Jobs |
| `Space s` | Settings |
| `Space h` | Back to welcome |
| `Space ?` | Help |
| `Space q` | Quit |

On the welcome screen, bare `l` / `j` / `s` also jump directly.

### Palette

| Key | Action |
|-----|--------|
| `/` | Open palette pre-seeded with `/` (commands) |
| `@` | Open palette pre-seeded with `@` (transcripts) |
| `Ctrl+K` | Open palette empty (free fuzzy) |
| `Ctrl+P` | Open palette pre-seeded with `/` (commands) |
| `Tab` | Auto-complete the current command name; cycles if no match |
| `↑` / `↓` / `Shift+Tab` | Move selection |
| `Enter` | Activate highlighted entry |
| `Escape` | Close |

Free-text fuzzy matching ranks commands, recordings, and active jobs in
one list. Leading `/` filters to commands only. Recent selections (MRU)
rank higher on subsequent opens.

**Sub-pickers.** After auto-completing certain commands the palette
switches mode and filters a different source:

| Trigger | Mode | Source | Enter action |
|---------|------|--------|--------------|
| `/library ` | library | recordings | open transcript |
| `/folder ` | folder | folders | open library scoped to folder |
| `/tag ` | tag | tags | open library scoped to tag |
| `/analyze ` | analyze | recordings | choose analysis type, then queue |
| `/models ` | model | LLM models | set as default model |
| `@` | transcript | recordings | open transcript |

### Library
| Key | Action |
|-----|--------|
| `↑↓` / `j` `k` | Move row |
| `g g` / `G` | Top / bottom |
| `Enter` | Open recording (transcript screen) |
| `r` | Refresh library |
| `y` | Copy filename to clipboard |
| `d` | Delete (prompt) |
| `Escape` | Back |

### Transcript
| Key | Action |
|-----|--------|
| `j` / `k` | Move segment |
| `g g` / `G` | First / last segment |
| `n` / `N` | Next / previous speaker change |
| `y` | Copy current segment |
| `Y` | Copy full transcript |
| `Space` | Play / pause |
| `s` | Stop |
| `Ctrl+A` | Run LLM analysis on this recording |
| `Escape` / `q` | Back to library |

### Job screen
| Key | Action |
|-----|--------|
| `c` | Cancel job |
| `Escape` / `q` | Back |

### Global
| Key | Action |
|-----|--------|
| `Space` | Arm leader chord |
| `/` · `Ctrl+K` | Open palette |
| `Ctrl+C` | Quit |

## Slash Commands

Press `/` to open the command palette.

| Command | Action |
|---------|--------|
| `/help` | Show command reference |
| `/transcribe <path>` | Upload file and transcribe |
| `/transcribe-url <url>` | Download from URL and transcribe |
| `/search <query>` | Full-text search across transcripts |
| `/export <id> <fmt>` | Export transcript (json/srt/txt/md) — saved to CWD |
| `/cancel <job_id>` | Cancel running job |
| `/delete <id>` | Delete recording |
| `/folder new <name>` | Create folder |
| `/library` | Open the recordings library (sub-picker after space) |
| `/folder` | Pick a folder (or `/folder new <name>` to create) |
| `/tag` | Pick a tag |
| `/analyze` | Pick a recording and run summary / action_items / translate / custom |
| `/models` | Pick an LLM model (sets as default) |
| `/llm` | Open LLM settings |
| `/jobs` | Open the active-jobs list |
| `/welcome` | Return to the welcome screen |
| `/settings` | Open settings screen |
| `/logs` | Show captured server logs |
| `/quit` | Exit |

## Drag & Drop

Drag an audio/video file onto the terminal window — modern terminals
(iTerm2, WezTerm, Kitty, Windows Terminal, GNOME Terminal) emit the path
as a paste event. The TUI intercepts paths with audio extensions and
auto-triggers `/transcribe`.

Inside tmux you may need `set -g allow-passthrough on` for OSC 52
clipboard writes to reach the host clipboard.

## Clipboard

Copy operations (`y`, `Y`) try `pyperclip` first, then fall back to
OSC 52 escape sequences — so clipboard works over SSH.

## Waveform

Transcript screen renders a single-line unicode waveform of the audio
using block characters `▁▂▃▄▅▆▇█`. Audio is fetched from
`/api/recordings/{id}/audio` to a temp file, downsampled via numpy +
soundfile, and discarded on screen exit.

## Limitations (v1)

- No segment editing (view + copy only)
- No audio playback
- No multi-select copy across segments (single segment via `y`, full
  transcript via `Y`)
- Folder/tag pickers not yet wired into library filtering UI
