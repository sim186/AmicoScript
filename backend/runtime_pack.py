"""Fetch the torch/pyannote stack on demand instead of shipping it.

Whisper does not use torch. faster-whisper transcribes through CTranslate2, and
every ``import torch`` in this backend sits inside a function on the
diarization path. That makes torch — plus torchaudio, pyannote.audio and, on a
GPU machine, a gigabyte of CUDA libraries — dead weight in a bundle for every
user who only ever transcribes.

So it is not in the bundle. The build records the exact wheels it *would* have
installed into ``runtime_manifest.json``, and the first job that actually needs
them downloads that list into the cache directory and appends it to
``sys.path``. Which list depends on what ``gpu_probe`` finds: the CPU wheels,
or the CUDA ones.

Three properties this leans on, all of them pre-existing:

* PyInstaller's ``FrozenImporter`` beats ``sys.path`` for anything inside the
  bundle, so this only works because torch is genuinely excluded from it.
  Shipping torch and shadowing it with a download would silently import the
  bundled copy.
* torch locates its own shared libraries relative to ``torch/__file__``, so it
  does not care that it is being imported from a cache directory.
* Nothing has imported torch yet when the hook runs, so the install takes
  effect immediately — there is no restart to ask the user for.

When torch already imports — a dev checkout, the Docker images, the test suite
— every entry point here is a no-op. The pack is a packaging detail, not a new
way to run the app.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

MANIFEST_NAME = "runtime_manifest.json"

# What the pack is for. If both import, there is nothing to fetch.
REQUIRED_MODULES = ("torch", "pyannote.audio")

_DOWNLOAD_ATTEMPTS = 3
_DOWNLOAD_TIMEOUT = 60
_CHUNK = 1 << 20

ProgressCallback = Callable[[str], None]

# One install at a time: the transcription and diarization hooks can both fire
# for the same job, and neither should watch the other unzip torch.
_install_lock = threading.Lock()


class RuntimePackError(RuntimeError):
    """The runtime pack was needed and could not be made available."""


# ---------------------------------------------------------------------------
# Locating things
# ---------------------------------------------------------------------------


def _cache_root() -> Path:
    """Match resource_downloader, so every downloaded asset lives together."""
    root = os.environ.get("AMICO_CACHE_DIR") or str(Path.home() / ".cache" / "amicoscript")
    return Path(root) / "runtime"


def manifest_path() -> Optional[Path]:
    """The manifest shipped with this build, if there is one.

    Absent in a dev checkout and in the Docker images, where the dependencies
    are installed the ordinary way — which is what makes this module inert
    there rather than broken.
    """
    override = os.environ.get("AMICO_RUNTIME_MANIFEST")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None

    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(__file__).resolve().parents[1])

    for root in roots:
        candidate = root / MANIFEST_NAME
        if candidate.is_file():
            return candidate
    return None


def load_manifest() -> Optional[dict]:
    """Parse the manifest, or None when there isn't a usable one."""
    path = manifest_path()
    if path is None:
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest.get("variants"), dict) else None


# ---------------------------------------------------------------------------
# Deciding what is needed
# ---------------------------------------------------------------------------


def already_importable() -> bool:
    """True when torch and pyannote can be imported without our help.

    ``sys.modules`` is checked first, and not only as a shortcut: a module that
    is already loaded is importable by definition, whether it got there from a
    normal install or was put there by a test.
    """
    for module in REQUIRED_MODULES:
        if module in sys.modules:
            continue
        try:
            if importlib.util.find_spec(module) is None:
                return False
        except Exception:
            # A parent package with no __path__, a broken finder: either way,
            # the question "can this be imported" has just been answered.
            return False
    return True


def select_variant(manifest: dict, prefer_cuda: Optional[bool] = None) -> Optional[str]:
    """Choose between the CPU and CUDA wheel sets.

    ``AMICO_RUNTIME_FLAVOUR`` overrides the probe, which is how a user on a
    fresh driver can force the CPU wheels without uninstalling anything.
    """
    variants = manifest.get("variants") or {}

    forced = (os.environ.get("AMICO_RUNTIME_FLAVOUR") or "").strip().lower()
    if forced:
        return forced if forced in variants else None

    if prefer_cuda is None:
        import gpu_probe

        prefer_cuda = gpu_probe.has_nvidia_gpu()

    if prefer_cuda:
        for name in variants:
            if name != "cpu":
                return name
    return "cpu" if "cpu" in variants else (next(iter(variants), None))


def variant_digest(spec: dict) -> str:
    """A short hash of the exact wheel list.

    Recomputed here rather than read from the manifest so the installed
    directory can only ever be named after what is actually inside it — a
    changed pin lands in a new directory instead of half-updating an old one.
    """
    lines = sorted(
        f"{wheel.get('name')}=={wheel.get('version')}#{wheel.get('sha256')}"
        for wheel in spec.get("wheels", [])
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]


def install_dir(variant: str, spec: dict) -> Path:
    return _cache_root() / f"{variant}-{variant_digest(spec)}"


def _marker(target: Path) -> Path:
    """Written last, so a half-extracted directory is never mistaken for one."""
    return target / ".complete"


def is_installed(variant: str, spec: dict) -> bool:
    return _marker(install_dir(variant, spec)).is_file()


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------


def _download(url: str, destination: Path, sha256: str, progress: Optional[ProgressCallback]) -> None:
    """Fetch one wheel and refuse it unless the hash matches."""
    if not url.lower().startswith("https://"):
        raise RuntimePackError(f"Refusing to download {destination.name} over {url.split(':', 1)[0]}")

    last_error: Optional[Exception] = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as response:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                milestone = 0
                with destination.open("wb") as handle:
                    while True:
                        chunk = response.read(_CHUNK)
                        if not chunk:
                            break
                        handle.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        if progress and total:
                            percent = done * 100 // total
                            if percent >= milestone + 25:
                                milestone = percent - percent % 25
                                progress(f"  {destination.name}: {milestone}%")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            if attempt < _DOWNLOAD_ATTEMPTS:
                continue
            raise RuntimePackError(f"Could not download {destination.name}: {exc}") from exc

        actual = digest.hexdigest()
        if sha256 and actual != sha256:
            destination.unlink(missing_ok=True)
            last_error = RuntimePackError(
                f"{destination.name} hash mismatch (expected {sha256[:12]}, got {actual[:12]})"
            )
            if attempt < _DOWNLOAD_ATTEMPTS:
                continue
            raise last_error
        return

    raise RuntimePackError(f"Could not download {destination.name}: {last_error}")


def _safe_members(archive: zipfile.ZipFile, target: Path) -> list[str]:
    """Reject any entry that would write outside the target directory."""
    resolved_target = target.resolve()
    names = []
    for name in archive.namelist():
        candidate = (target / name).resolve()
        if candidate != resolved_target and resolved_target not in candidate.parents:
            raise RuntimePackError(f"Wheel entry escapes the install directory: {name}")
        names.append(name)
    return names


def _flatten_data_dirs(target: Path) -> None:
    """Move ``*.data/{purelib,platlib}`` contents up to the install root.

    A wheel may route files through its ``.data`` directory instead of placing
    them at the archive root; installers replay that, and an unzip alone does
    not. Everything else under ``.data`` (scripts, headers) is for an
    environment we do not have and is dropped.
    """
    for data_dir in list(target.glob("*.data")):
        if not data_dir.is_dir():
            continue
        for scheme in ("purelib", "platlib"):
            source = data_dir / scheme
            if not source.is_dir():
                continue
            for item in list(source.iterdir()):
                destination = target / item.name
                if destination.exists():
                    continue
                shutil.move(str(item), str(destination))
        shutil.rmtree(data_dir, ignore_errors=True)


def _extract(wheel: Path, target: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(target, members=_safe_members(archive, target))


def _install(variant: str, spec: dict, progress: Optional[ProgressCallback]) -> Path:
    """Download and unpack the whole variant, then publish it atomically."""
    target = install_dir(variant, spec)
    if _marker(target).is_file():
        return target

    wheels = spec.get("wheels") or []
    if not wheels:
        raise RuntimePackError(f"The manifest lists no wheels for the '{variant}' runtime")

    staging = target.with_name(target.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    downloads = staging / ".downloads"
    downloads.mkdir(exist_ok=True)

    try:
        for index, wheel in enumerate(wheels, start=1):
            filename = wheel.get("filename") or f"{wheel.get('name')}.whl"
            if progress:
                progress(f"Downloading {wheel.get('name')} {wheel.get('version')} ({index}/{len(wheels)})")
            archive = downloads / filename
            _download(wheel["url"], archive, wheel.get("sha256", ""), progress)
            _extract(archive, staging)
            archive.unlink(missing_ok=True)

        _flatten_data_dirs(staging)
        shutil.rmtree(downloads, ignore_errors=True)
        _marker(staging).write_text("ok", encoding="utf-8")

        try:
            staging.rename(target)
        except OSError:
            # Another process finished first; theirs is as good as ours.
            if not _marker(target).is_file():
                raise
            shutil.rmtree(staging, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    _prune_superseded(variant, target)
    return target


def _prune_superseded(variant: str, keep: Path) -> None:
    """Drop packs from earlier versions of this app. Best effort, never fatal.

    Each pin change installs into a new directory, so without this an app that
    has been upgraded twice is sitting on three copies of torch.
    """
    root = _cache_root()
    if not root.is_dir():
        return
    for candidate in root.iterdir():
        if candidate == keep or not candidate.is_dir():
            continue
        if not candidate.name.startswith(f"{variant}-"):
            continue
        if str(candidate) in sys.path:
            continue
        shutil.rmtree(candidate, ignore_errors=True)


# ---------------------------------------------------------------------------
# Activating
# ---------------------------------------------------------------------------


def activate(target: Path) -> None:
    """Put an installed pack on the import path.

    Appended rather than inserted: the pack holds only what the bundle lacks,
    so it never needs to win a name clash, and appending means it cannot cause
    one either.
    """
    path = str(target)
    if path not in sys.path:
        sys.path.append(path)
        # The directory did not exist when the process started, and something
        # may already have looked for it and cached the absence.
        importlib.invalidate_caches()

    # CTranslate2 loads cuBLAS and cuDNN by soname at first use, and in the CUDA
    # variant those arrived with the pack rather than with the bundle. The
    # preload at startup ran before this directory existed, so it runs again.
    try:
        import cuda_runtime

        cuda_runtime.register_root(target)
        cuda_runtime.preload()
    except Exception:
        pass


def activate_if_installed() -> bool:
    """Put an already-downloaded pack on the path. Never downloads anything.

    Called at startup so a machine that has diarized before behaves as though
    the stack had been bundled: torch imports, the hardware panel sees the GPU,
    and no job has to wait for a decision that was made on a previous run.
    """
    if already_importable():
        return False

    manifest = load_manifest()
    if manifest is None:
        return False

    variant = select_variant(manifest)
    spec = (manifest.get("variants") or {}).get(variant or "")
    if not spec or not is_installed(variant, spec):
        return False

    activate(install_dir(variant, spec))
    return True


# ---------------------------------------------------------------------------
# The entry points
# ---------------------------------------------------------------------------


def wants_cuda(requested_device: str) -> bool:
    """Would this device request use a GPU, if one were set up for it?

    Asked before torch exists, so it reads the request and the driver rather
    than ``torch.cuda.is_available()``.
    """
    wanted = (requested_device or "auto").strip().lower()
    if wanted == "cpu":
        return False
    if not (wanted == "auto" or wanted == "gpu" or wanted.startswith("cuda")):
        return False

    import gpu_probe

    return gpu_probe.has_nvidia_gpu()


def ensure(progress: Optional[ProgressCallback] = None, prefer_cuda: Optional[bool] = None) -> str:
    """Make torch and pyannote importable. Returns what it had to do.

    * ``"present"`` — they already imported; nothing was fetched.
    * ``"activated"`` — a previously downloaded pack was put on the path.
    * ``"installed"`` — the pack was downloaded.

    Raises RuntimePackError when the stack is genuinely unavailable, including
    the case where this build carries no manifest and torch is missing anyway:
    that is a broken install, and saying so beats a bare ImportError from
    somewhere inside pyannote.
    """
    if already_importable():
        return "present"

    with _install_lock:
        # Another thread may have finished while this one waited.
        if already_importable():
            return "present"

        manifest = load_manifest()
        if manifest is None:
            raise RuntimePackError(
                "Speaker diarization needs PyTorch, which is not installed and "
                "which this build has no download manifest for. Install "
                "backend/requirements-diarization.txt."
            )

        variant = select_variant(manifest, prefer_cuda=prefer_cuda)
        spec = (manifest.get("variants") or {}).get(variant or "")
        if not spec:
            raise RuntimePackError(
                f"This build has no '{variant}' runtime for {sys.platform}"
            )

        installed = is_installed(variant, spec)
        if not installed and progress:
            size = sum(int(w.get("size") or 0) for w in spec.get("wheels", []))
            detail = f" (about {size / 1e9:.1f} GB)" if size else ""
            progress(
                f"Setting up the {variant} PyTorch runtime{detail}. "
                "This happens once; later jobs start immediately."
            )

        target = _install(variant, spec, progress)
        activate(target)

        if not already_importable():
            raise RuntimePackError(
                f"The {variant} runtime installed to {target} but torch still "
                "will not import. Delete that directory and try again."
            )

        if progress and not installed:
            progress(f"PyTorch runtime ready ({variant}).")
        return "installed" if not installed else "activated"


def status() -> dict:
    """A description of the runtime, for the hardware panel and for support."""
    if already_importable():
        return {"required": False, "state": "present", "variant": None}

    manifest = load_manifest()
    if manifest is None:
        return {"required": True, "state": "unavailable", "variant": None}

    variant = select_variant(manifest)
    spec = (manifest.get("variants") or {}).get(variant or "")
    if not spec:
        return {"required": True, "state": "unavailable", "variant": variant}

    return {
        "required": True,
        "state": "installed" if is_installed(variant, spec) else "not-installed",
        "variant": variant,
        "download_bytes": sum(int(w.get("size") or 0) for w in spec.get("wheels", [])),
    }
