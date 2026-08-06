#!/usr/bin/env python3
"""Record the wheels the runtime pack will download, without downloading them.

``backend/runtime_pack.py`` fetches torch, torchaudio and pyannote.audio on
first use rather than shipping them. For that to be safe it needs an exact
list — URLs and hashes, resolved once on a build machine — instead of a
dependency resolver running on a user's laptop against whatever PyPI looks
like that day.

``pip install --dry-run --report`` produces exactly that: the resolved set,
with the URL and sha256 of every wheel, and no download. This script runs it
once per flavour and writes ``runtime_manifest.json`` next to the build.

Two things it does beyond transcribing pip's answer:

* It resolves *against the versions already installed*, passed as constraints.
  The bundle and the pack share transitive dependencies — numpy above all —
  and only one copy will be importable at runtime: the bundle's. Constraining
  the resolve means a pack that needs a numpy the bundle does not have fails
  here, loudly, instead of at a user's first diarization.
* It then drops those shared packages from the manifest, because the bundle
  already carries them.

Run it in the same environment the bundle is built from, after installing
backend/requirements.txt and before running package.py.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Which wheel sets are worth building on each platform. macOS has no CUDA at
# any version, so it gets one flavour and no choice to make at runtime.
FLAVOURS = {
    "linux": {
        "cpu": "backend/requirements-diarization.txt",
        "cu121": "backend/requirements-diarization-cu121.txt",
    },
    "win32": {
        "cpu": "backend/requirements-diarization.txt",
        "cu121": "backend/requirements-diarization-cu121.txt",
    },
    "darwin": {
        "cpu": "backend/requirements-diarization.txt",
    },
}


def _platform_key() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _normalize(name: str) -> str:
    """PEP 503 name normalisation, so `pyannote.audio` and `pyannote-audio` match."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _installed() -> dict[str, str]:
    """Every distribution in this environment, normalised name to version."""
    found: dict[str, str] = {}
    for dist in distributions():
        name = dist.metadata["Name"]
        if name:
            found[_normalize(name)] = dist.version or ""
    return found


def _bundled(directory: Path) -> dict[str, str]:
    """What the bundle will carry: requirements.txt's tree, at installed versions.

    Not simply "everything installed here". A build machine also has
    PyInstaller, a test runner, and whatever else the image came with, none of
    which PyInstaller puts in the bundle — and treating those as bundled would
    both drop them from the pack, where they may genuinely be needed, and pin
    the resolve to versions no one asked for. Resolving requirements.txt names
    the packages the app is responsible for; the installed environment supplies
    the versions, because that is what actually gets frozen.
    """
    entries = _resolve(ROOT / "backend" / "requirements.txt", None, directory)
    tree = {_normalize((entry.get("metadata") or {}).get("name") or "") for entry in entries}
    installed = _installed()
    return {name: version for name, version in installed.items() if name in tree}


def _constraints_file(pins: dict[str, str], directory: Path, flavour: str) -> Path:
    path = directory / f"constraints-{flavour}.txt"
    lines = [f"{name}=={version}" for name, version in sorted(pins.items()) if version]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _shared(entries: list[dict], bundled: dict[str, str]) -> dict[str, str]:
    """Packages the pack and the bundle both want, pinned to the bundle's version.

    Only these are constrained. Pinning everything would be simpler and is what
    this did first, but it fails on any machine carrying unrelated packages
    that conflict with each other — and that failure says nothing about the
    pack. The packages that matter are the ones that will exist twice, and only
    a resolve can name them.
    """
    return {
        name: bundled[name]
        for name in (
            _normalize((entry.get("metadata") or {}).get("name") or "") for entry in entries
        )
        if name in bundled
    }


def _resolve(requirements: Path, constraints: Path | None, directory: Path) -> list[dict]:
    """Ask pip what it would install, and hand back its report."""
    report_path = directory / "report.json"
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--only-binary=:all:",
        "--quiet",
        "--report",
        str(report_path),
        "-r",
        str(requirements),
    ]
    if constraints is not None:
        command += ["-c", str(constraints)]

    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise SystemExit(
            f"pip could not resolve {requirements.name}:\n"
            f"{completed.stdout}\n{completed.stderr}\n"
            "If this is a version conflict with the bundled dependencies, that "
            "conflict is real — fix the pins rather than passing --no-constraints."
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    return report.get("install") or []


def _hash_of(download_info: dict, url: str) -> str:
    archive = download_info.get("archive_info") or {}
    hashes = archive.get("hashes") or {}
    if hashes.get("sha256"):
        return str(hashes["sha256"])
    # pip < 23.1 reported a single "hash" string instead of the mapping.
    single = archive.get("hash") or ""
    if single.startswith("sha256="):
        return single.split("=", 1)[1]
    # download.pytorch.org puts it in the URL fragment; pip may pass it through.
    if "#sha256=" in url:
        return url.split("#sha256=", 1)[1]
    return ""


def _content_length(url: str) -> int:
    """Best-effort size, only so the app can say how big the download is."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.headers.get("Content-Length") or 0)
    except (urllib.error.URLError, OSError, ValueError):
        return 0


def _wheels(entries: list[dict], bundled: dict[str, str], with_sizes: bool) -> tuple[list[dict], dict]:
    wheels: list[dict] = []
    from_base: dict[str, str] = {}

    for entry in entries:
        metadata = entry.get("metadata") or {}
        name = metadata.get("name") or ""
        normalized = _normalize(name)

        if normalized in bundled:
            # Already in the bundle, and constrained above to the same version.
            from_base[normalized] = bundled[normalized]
            continue

        download_info = entry.get("download_info") or {}
        url = download_info.get("url") or ""
        if not url.startswith("https://"):
            raise SystemExit(f"{name} resolved to a non-https URL ({url or 'none'})")

        digest = _hash_of(download_info, url)
        if not digest:
            raise SystemExit(
                f"No sha256 for {name}; refusing to write a manifest the app "
                "cannot verify."
            )

        clean_url = url.split("#", 1)[0]
        wheels.append(
            {
                "name": name,
                "version": metadata.get("version") or "",
                "filename": clean_url.rsplit("/", 1)[-1],
                "url": clean_url,
                "sha256": digest,
                "size": _content_length(clean_url) if with_sizes else 0,
            }
        )

    wheels.sort(key=lambda wheel: wheel["name"].lower())
    return wheels, from_base


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(ROOT / "runtime_manifest.json"),
        help="where to write the manifest (default: repository root)",
    )
    parser.add_argument(
        "--flavour",
        action="append",
        dest="flavours",
        help="limit to one flavour (repeatable); default is every flavour for this platform",
    )
    parser.add_argument(
        "--no-constraints",
        action="store_true",
        help="resolve without pinning to the installed versions (see the module docstring)",
    )
    parser.add_argument(
        "--no-sizes",
        action="store_true",
        help="skip the HEAD request per wheel used to report the download size",
    )
    args = parser.parse_args()

    key = _platform_key()
    available = FLAVOURS[key]
    wanted = args.flavours or list(available)
    unknown = [flavour for flavour in wanted if flavour not in available]
    if unknown:
        raise SystemExit(f"No {', '.join(unknown)} runtime is defined for {key}")

    variants: dict[str, dict] = {}

    with tempfile.TemporaryDirectory() as raw_directory:
        directory = Path(raw_directory)

        print("Resolving what the bundle will carry...")
        bundled = _bundled(directory)
        print(f"  {len(bundled)} packages")

        for flavour in wanted:
            requirements = ROOT / available[flavour]
            if not requirements.is_file():
                raise SystemExit(f"Missing {requirements}")

            print(f"Resolving the {flavour} runtime from {requirements.name}...")
            entries = _resolve(requirements, None, directory)

            # Then again, pinned to the bundle's copy of everything the two
            # halves share, so the wheels recorded below are the ones that will
            # actually work beside it.
            if not args.no_constraints:
                pins = _shared(entries, bundled)
                if pins:
                    print(f"  re-resolving against {len(pins)} bundled packages")
                    entries = _resolve(
                        requirements, _constraints_file(pins, directory, flavour), directory
                    )

            wheels, from_base = _wheels(entries, bundled, not args.no_sizes)

            total = sum(wheel["size"] for wheel in wheels)
            print(
                f"  {len(wheels)} wheels"
                + (f", {total / 1e9:.2f} GB" if total else "")
                + f" ({len(from_base)} already in the bundle)"
            )
            variants[flavour] = {"wheels": wheels, "provided_by_base": from_base}

    version_file = ROOT / "VERSION"
    manifest = {
        "manifest_version": 1,
        "app_version": version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else "",
        "platform": key,
        "machine": platform.machine(),
        "python_tag": f"cp{sys.version_info.major}{sys.version_info.minor}",
        "variants": variants,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
