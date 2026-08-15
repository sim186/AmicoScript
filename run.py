import os
import sys
import threading
import webbrowser
import time
from pathlib import Path

_STDIO_FALLBACK_HANDLES = []


def _ensure_standard_streams() -> None:
    """Provide file-like stdio streams in windowed/noconsole builds."""
    if sys.stdin is None:
        stdin_fallback = open(os.devnull, "r", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(stdin_fallback)
        sys.stdin = stdin_fallback
    if sys.stdout is None:
        stdout_fallback = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(stdout_fallback)
        sys.stdout = stdout_fallback
    if sys.stderr is None:
        stderr_fallback = open(os.devnull, "w", encoding="utf-8", errors="replace")
        _STDIO_FALLBACK_HANDLES.append(stderr_fallback)
        sys.stderr = stderr_fallback


_ensure_standard_streams()

# Fix for PyInstaller paths
if hasattr(sys, '_MEIPASS'):
    # Running in a bundle
    BASE_DIR = Path(sys._MEIPASS)
    EXE_DIR = Path(sys.executable).parent
else:
    # Running in normal Python
    BASE_DIR = Path(__file__).parent / "backend"
    EXE_DIR = Path(__file__).parent

# Ensure we can import backend packages if not in bundle
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# torch, torchaudio and pyannote are not in the bundle; they are downloaded on
# first use. If a previous run already did that, put them back on the import
# path now so this process starts out the way a bundled build would have — no
# download here, only what is already on disk.
try:
    import runtime_pack

    runtime_pack.activate_if_installed()
except Exception as e:
    print(f"Runtime pack skipped: {e}")

# CUDA libraries have to be loaded before anything creates a CTranslate2 model,
# which is why this sits above the backend imports rather than inside them.
# No-op until a CUDA runtime has been downloaded.
try:
    import cuda_runtime

    cuda_runtime.preload()
except Exception as e:
    print(f"CUDA preload skipped: {e}")

# Download FFmpeg on start if missing
try:
    import config
    from ffmpeg_helper import start_background_download
    # Download into config.STORAGE_ROOT/bin (user-writable) rather than the
    # app/executable directory.
    start_background_download(config.STORAGE_ROOT / "bin")
except Exception as e:
    print(f"Failed to setup FFmpeg: {e}")

# Ensure ffmpeg and other bundled binaries are found in PATH
try:
    import config
    storage_bin = config.STORAGE_ROOT / "bin"
except Exception:
    storage_bin = None

path_parts = []
if storage_bin is not None:
    path_parts.append(str(storage_bin))
path_parts.append(str(EXE_DIR))
path_parts.append(os.environ.get("PATH", ""))
path_parts.append(str(BASE_DIR))
os.environ["PATH"] = os.pathsep.join(p for p in path_parts if p)


def open_browser(url):
    # Wait a bit for the server to start
    time.sleep(1.5)
    webbrowser.open(url)


def resolve_ui_mode() -> str:
    """Decide how the frontend is shown: native window, browser tab, or nothing.

    AMICOSCRIPT_NO_BROWSER=1 still wins outright — the TUI and the CI smoke test
    rely on it to get a headless backend.
    """
    if os.environ.get("AMICOSCRIPT_NO_BROWSER", "0") == "1":
        return "none"
    mode = os.environ.get("AMICOSCRIPT_UI", "window").strip().lower()
    if mode not in ("window", "browser", "none"):
        mode = "window"
    return mode


def make_server(host: str, port: int):
    """Build the uvicorn server without starting it.

    uvicorn.run() would do this too, but we need the Server object so the
    webview can ask it to shut down when the window closes.
    """
    import main  # noqa: F401 - imported for side effects + app lookup
    import uvicorn
    # In windowed/"noconsole" builds, stderr can be missing; avoid uvicorn's
    # default formatter setup that expects a TTY-backed stream.
    config = uvicorn.Config(
        main.app, host=host, port=port, log_level="info", log_config=None
    )
    return uvicorn.Server(config)


def wait_until_serving(server, thread, timeout: float = 90.0) -> bool:
    """Block until uvicorn accepts connections, or the server thread dies.

    Opening the window before the port is up shows a blank error page, so the
    wait is not optional.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.started:
            return True
        if not thread.is_alive():
            return False
        time.sleep(0.1)
    return False


def webview_storage_path():
    """Persistent profile dir, so the frontend keeps its localStorage."""
    try:
        import config
        path = config.STORAGE_ROOT / "webview"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)
    except Exception:
        return None


def run_windowed(url: str, host: str, port: int) -> int:
    import webview

    # Defaults are restrictive: downloads are off and the profile is wiped on
    # exit. Both matter here — the library exports via <a download> and the
    # frontend keeps its settings in localStorage.
    webview.settings["ALLOW_DOWNLOADS"] = True

    server = make_server(host, port)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()

    if not wait_until_serving(server, thread):
        print(f"Backend did not come up on {url}; aborting.")
        return 1

    version = ""
    try:
        version = (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        pass

    # macOS: when running via uv/pip (no .app bundle), set the dock icon so the
    # app appears in the Dock and Cmd-Tab with the proper AmicoScript logo.
    if sys.platform == "darwin":
        try:
            from AppKit import NSImage, NSApplication
            # Look for icons relative to this file (works both in repo and installed package)
            script_dir = Path(__file__).parent
            icon_dirs = [
                script_dir / "images",
                BASE_DIR.parent / "images",
                script_dir.parent / "images",
            ]
            icon_path = None
            for d in icon_dirs:
                candidates = sorted(d.glob("*.icns"))
                if candidates:
                    icon_path = candidates[0]
                    break
            if icon_path:
                app = NSApplication.sharedApplication()
                image = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
                if image and app:
                    app.setApplicationIconImage_(image)
        except Exception:
            pass

    try:
        webview.create_window(
            f"AmicoScript {version}".strip(),
            url,
            width=1440,
            height=900,
            min_size=(1024, 680),
            # Both default to False in pywebview and both are load-bearing here:
            # the whole app is transcripts you copy out of, at your own zoom.
            text_select=True,
            zoomable=True,
        )
        webview.start(
            private_mode=False,
            storage_path=webview_storage_path(),
            debug=os.environ.get("AMICOSCRIPT_WEBVIEW_DEBUG", "0") == "1",
        )
    except Exception as exc:
        # No usable webview engine (missing WebKitGTK / WebView2 runtime).
        # The backend is already serving, so degrade to a browser tab rather
        # than dying with a stack trace.
        print(f"Native window unavailable ({exc}); opening in the system browser.")
        webbrowser.open(url)
        try:
            thread.join()
        except KeyboardInterrupt:
            server.should_exit = True
            thread.join(timeout=10)
        return 0

    # webview.start() returns once the last window closes.
    server.should_exit = True
    thread.join(timeout=10)
    return 0


def main() -> int:
    """Start the app. Shared by `python run.py`, the PyInstaller bundle, and the
    `amicoscript` console script the wheel installs (see pyproject.toml)."""
    # Ensure frontend and uploads dirs are found
    os.chdir(BASE_DIR)

    # Path to the frontend folder
    # In a bundle, BASE_DIR/frontend should exist

    # Start server
    host = "127.0.0.1"
    port = int(os.environ.get("AMICOSCRIPT_PORT", "8002"))
    url = f"http://{host}:{port}"

    print(f"Starting AmicoScript at {url}...")

    ui_mode = resolve_ui_mode()
    if ui_mode == "window":
        try:
            import webview  # noqa: F401
        except ImportError:
            # Dev checkouts and Docker images don't install pywebview (it drags
            # in GTK/pyobjc); the browser path stays the fallback.
            print("pywebview not available — opening in the system browser instead.")
            ui_mode = "browser"

    if ui_mode == "window":
        return run_windowed(url, host, port)

    if ui_mode == "browser":
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    server = make_server(host, port)
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
