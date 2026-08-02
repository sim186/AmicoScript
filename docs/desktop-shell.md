# Desktop shell

How AmicoScript gets a window. Two phases: `pywebview` today, Tauri later.

The backend is Python (torch, faster-whisper, pyannote, ffmpeg) and is not going
anywhere. Neither shell replaces it — both only decide what draws the frontend.

## Why not Electron

Electron bundles its own Chromium (~120 MB) on top of a bundle that is already
~630 MB. Every option below reuses the webview the OS already ships:

| OS | Engine | Shipped by us |
|----|--------|---------------|
| macOS | WKWebView (system framework) | nothing |
| Windows | WebView2 (Microsoft-updated Chromium runtime) | nothing; bootstrapper if absent |
| Linux | WebKitGTK (`libwebkit2gtk-4.1`) | nothing; must be installed |

pywebview and Tauri use exactly these. The renderer is not what separates them.

## Phase 1 — pywebview (current)

`run.py` decides the UI mode, then either opens a window or serves headless.

```
AMICOSCRIPT_NO_BROWSER=1   -> none     (TUI, Docker, CI smoke test)
AMICOSCRIPT_UI=window      -> native window (default)
AMICOSCRIPT_UI=browser     -> serve + open default browser
AMICOSCRIPT_UI=none        -> serve only
```

In window mode uvicorn moves to a background thread and pywebview takes the main
thread, because macOS requires the GUI event loop there. `run.make_server()`
returns a `uvicorn.Server` rather than calling `uvicorn.run()` so the window can
set `should_exit` on close. `wait_until_serving()` blocks until the port answers
— opening the window first shows a blank error page.

### Non-obvious settings

These pywebview defaults are wrong for this app and are overridden in `run.py`:

| Setting | Default | Why we change it |
|---------|---------|------------------|
| `text_select` | `False` | The whole product is transcripts you copy out of |
| `zoomable` | `False` | Long-form reading needs ⌘+/− |
| `private_mode` | `True` | Wipes the profile on exit; the frontend keeps settings in `localStorage` (26 call sites) |
| `ALLOW_DOWNLOADS` | `False` | Library exports (SRT/TXT/MD/JSON) are `<a download>` + blob URLs |

`storage_path` points at `$STORAGE_ROOT/webview` so the profile lives beside the
database instead of in a temp dir.

### Packaging

`pywebview` is in `requirements-pyinstaller.txt`, not `backend/requirements.txt`
— the Docker image and the TUI run headless and must not pull a GUI toolkit.
Markers keep it off Linux (see below) and add `pythonnet` on Windows.

`package.py` collects it only when installed, so minimal/Docker builds stay lean:

- `--collect-all=webview` — pywebview's injected JS shims, plus the WebView2
  interop DLLs under `webview/lib/` on Windows.
- macOS: hidden imports for `objc`, `Foundation`, `AppKit`, `WebKit`, `Quartz`.
  The Cocoa backend reaches for pyobjc lazily and static analysis misses it.
- Windows: `--collect-all` for `clr_loader` / `pythonnet`, plus `--hidden-import=clr`.

If pywebview is absent at build time, `package.py` prints a warning and the
bundle ships in browser-fallback mode rather than failing.

### Known gaps

- **Linux has no native window.** The backend there is WebKitGTK through
  PyGObject, which lives in system packages PyInstaller cannot relocate into a
  bundle. Linux builds fall back to a browser tab. Fixing this properly is
  Tauri's job — its `.deb` can just declare `libwebkit2gtk-4.1-0` as a dependency
  and let apt solve it.
- **Closing the window quits the process.** On Windows that also stops the
  embedded meeting watcher, which previously survived closing a browser tab.
  Users who want the old behaviour can set `AMICOSCRIPT_UI=browser`. Phase 2
  should make the window close to the tray instead.
- **No auto-update.** Releases are still hand-downloaded zips.

### Offline assets

Tailwind, marked, WaveSurfer and the Inter font are vendored under
`frontend/vendor/` at pinned versions, so the UI renders with no network at all.
`tests/test_frontend_assets.py` fails the build if a CDN reference creeps back
in or a vendored file goes missing. Details and refresh steps:
[`frontend/vendor/README.md`](../frontend/vendor/README.md).

This was a prerequisite for Phase 2 regardless — Tauri's default CSP blocks
external hosts.

## Phase 2 — Tauri sidecar (planned)

Tauri is a Rust shell. It cannot host Python, so the Python side stays a
PyInstaller binary, shipped as a Tauri **sidecar** that the Rust process spawns
and supervises. Both build chains survive; Tauri is added, not swapped in.

What it buys over Phase 1:

- Signed, real installers: `.dmg`, `.msi`/`.nsis`, `.deb`/`.AppImage`
- Built-in updater (signed `latest.json`), replacing manual zip downloads
- Native tray that outlives the window, single-instance guard, deep links
- A working Linux window via an apt-declared WebKitGTK dependency
- ~8–12 MB added to the bundle

Shape of the migration:

1. ~~Vendor the CDN assets into `frontend/`~~ — done, see "Offline assets" above.
2. `src-tauri/` with `tauri.conf.json`, `Cargo.toml`, `src/main.rs`.
3. `package.py` output becomes the sidecar: copy the binary to
   `src-tauri/binaries/amicoscript-<target-triple>`, which is the naming Tauri
   requires.
4. `main.rs` spawns the sidecar, polls `127.0.0.1:8002` until it answers, then
   shows the window; on exit it must kill the child — orphaned backends holding
   the port are the main failure mode here.
5. `.github/workflows/release.yml` gains a Rust toolchain step and
   `tauri-apps/tauri-action`, which publishes the installers and the updater
   manifest.
6. Store the updater signing key as a repo secret; Apple notarization needs a
   Developer ID cert to remove the current "unidentified developer" dance in the
   README.

Once Tauri owns the window, `run.py`'s `--windowed` handling and the stdio
fallbacks it needs (`_ensure_standard_streams`) become dead weight — the backend
runs as a headless child with real pipes.
