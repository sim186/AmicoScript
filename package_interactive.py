#!/usr/bin/env python3
"""Interactive packager for AmicoScript.

Creates a PyInstaller build, embeds VERSION and optional GitHub repo metadata,
and produces a zip artifact ready for upload to GitHub Releases.

Usage: run from repo root: `python package_interactive.py`
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ARTIFACTS = ROOT / "build" / "artifacts"


def _add_data_arg(src: str, dest: str) -> str:
    """Return a PyInstaller --add-data argument with the correct separator.

    PyInstaller expects --add-data=SRC:DEST on POSIX and --add-data=SRC;DEST
    on Windows.
    """
    return f"--add-data={src}{os.pathsep}{dest}"


def read_version():
    vfile = ROOT / "VERSION"
    if vfile.exists():
        return vfile.read_text(encoding="utf-8").strip()
    return "0.0.0"


def prompt_bool(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    r = input(f"{prompt} [{d}]: ").strip().lower()
    if not r:
        return default
    return r[0] == "y"


def main():
    print("Interactive packager for AmicoScript")
    name = input("Output executable name (default: AmicoScript): ").strip() or "AmicoScript"
    entry = input("Entry script (relative to repo root, default: run.py): ").strip() or "run.py"
    onefile = prompt_bool("Build single-file executable (--onefile)?", default=False)
    noconsole = prompt_bool("Hide console window (--noconsole)? (Windows)", default=True)
    include_frontend = prompt_bool("Include frontend folder in bundle?", default=True)
    include_changelog = prompt_bool("Include CHANGELOG.md in bundle?", default=True)
    embed_repo = prompt_bool("Embed GitHub owner/repo metadata in bundle?", default=True)

    owner = ""
    repo = ""
    if embed_repo:
        owner = input("GitHub owner/org (leave blank to skip embedding): ").strip()
        repo = input("GitHub repo name (leave blank to skip embedding): ").strip()
        if not owner or not repo:
            embed_repo = False

    version = read_version()
    print(f"Detected version: {version}")

    # Clean
    for d in (DIST, BUILD):
        if d.exists():
            print(f"Cleaning {d}...")
            shutil.rmtree(d)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    # Build PyInstaller args
    args = [entry, f"--name={name}"]
    args.append("--paths=backend")
    if onefile:
        args.append("--onefile")
    else:
        args.append("--onedir")
    if noconsole:
        args.append("--noconsole")

    # Include frontend and metadata files
    if include_frontend and (ROOT / "frontend").exists():
        args.append(_add_data_arg("frontend", "frontend"))
    # Always try to include VERSION
    if (ROOT / "VERSION").exists():
        args.append(_add_data_arg("VERSION", "."))
    if include_changelog and (ROOT / "CHANGELOG.md").exists():
        args.append(_add_data_arg("CHANGELOG.md", "."))

    # Helpful hidden imports from existing package.py (avoid forcing heavy libs)
    hidden = [
        "main",
        "ffmpeg_helper",
        "sse_starlette.sse",
    ]
    for h in hidden:
        args.append(f"--hidden-import={h}")

    # Exclude common heavy/optional modules by default when building from
    # a minimal venv so they aren't accidentally bundled.
    excludes = [
        'torchcodec',
        'tensorboard',
        'torch.utils.tensorboard',
        'uvicorn.streaming',
    ]
    for ex in excludes:
        args.append(f"--exclude-module={ex}")

    # Bundle runtime data only if present in the environment (keeps minimal
    # venvs small).  Always try to collect faster_whisper if present.
    try:
        import importlib.util as _importlib_util
        if _importlib_util.find_spec('faster_whisper') is not None:
            args.append("--hidden-import=faster_whisper")
            args.append("--collect-data=faster_whisper")
        if _importlib_util.find_spec('pyannote.audio') is not None:
            args.append('--hidden-import=pyannote.audio')
            args.append("--collect-data=pyannote.audio")
        if _importlib_util.find_spec('huggingface_hub') is not None:
            args.append('--hidden-import=huggingface_hub')
    except Exception:
        pass

    print("\nPyInstaller arguments:")
    print(args)
    print("\nStarting PyInstaller build...")

    PyInstaller.__main__.run(args)

    # Locate output
    artifact_root = None
    dist_root = DIST
    if onefile:
        # Expect a single file in DIST named {name}.exe or {name}
        candidates = list(dist_root.glob(f"{name}*"))
        if candidates:
            artifact_root = dist_root
        else:
            artifact_root = dist_root
    else:
        candidate_dir = dist_root / name
        if candidate_dir.exists():
            artifact_root = candidate_dir
        else:
            # fallback to dist
            artifact_root = dist_root

    # Write metadata into artifact_root
    meta = {
        "name": name,
        "version": version,
        "built_at": int(time.time()),
    }
    if embed_repo:
        meta["github_owner"] = owner
        meta["github_repo"] = repo
    try:
        meta_path = artifact_root / "package_meta.json"
        with open(meta_path, "w", encoding="utf-8") as mf:
            json.dump(meta, mf, indent=2)
        print(f"Wrote package metadata to {meta_path}")
    except Exception as exc:
        print(f"Failed to write metadata: {exc}")

    # Create zip artifact
    artifact_name = f"{name}-{version}" if version else name
    zip_base = ARTIFACTS / artifact_name
    try:
        # shutil.make_archive will add extension automatically
        print(f"Creating zip artifact {zip_base}.zip")
        shutil.make_archive(str(zip_base), 'zip', root_dir=str(artifact_root))
        print(f"Artifact created: {zip_base}.zip")
    except Exception as exc:
        print(f"Failed to create zip artifact: {exc}")

    print("\nPackaging complete. Upload the created zip to GitHub Releases.")
    print(f"Artifacts directory: {ARTIFACTS}")


if __name__ == '__main__':
    main()
