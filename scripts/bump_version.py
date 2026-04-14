#!/usr/bin/env python3
"""Simple semantic version bumping and changelog helper."""
from __future__ import annotations

import re
import sys
import subprocess
import argparse
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
CHANGELOG = ROOT / "CHANGELOG.md"

def read_version() -> str:
    if not VERSION_FILE.exists():
        return "0.0.0"
    return VERSION_FILE.read_text(encoding="utf-8").strip()

def write_version(v: str) -> None:
    VERSION_FILE.write_text(v + "\n", encoding="utf-8")

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+].*)?$")

def bump(version: str, part: str) -> str:
    m = _SEMVER.match(version)
    if not m:
        raise SystemExit(f"Invalid semver in {VERSION_FILE}: '{version}'")
    major, minor, patch = map(int, m.groups()[:3])
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    return f"{major}.{minor}.{patch}"

def update_changelog(new_version: str, entry: str | None) -> None:
    today = date.today().isoformat()
    header = f"## [{new_version}] - {today}"
    entry_line = f"- {entry}" if entry else "- No change details provided."

    if not CHANGELOG.exists():
        content = f"# Changelog\n\n## [Unreleased]\n\n{header}\n{entry_line}\n"
        CHANGELOG.write_text(content, encoding="utf-8")
        return

    text = CHANGELOG.read_text(encoding="utf-8")
    
    # Logic: Keep the [Unreleased] header at the top, move old unreleased content 
    # to the new version section, and add the new entry.
    unreleased_pattern = r"(?m)^##\s*\[Unreleased\]"
    m = re.search(unreleased_pattern, text)
    
    if m:
        before = text[:m.end()].strip()
        after = text[m.end():].strip()
        
        # Split existing unreleased content from the rest of the file
        parts = re.split(r"(?m)^##\s*\[", after, maxsplit=1)
        current_unreleased_notes = parts[0].strip()
        rest = f"\n\n## [{parts[1]}" if len(parts) > 1 else ""

        # Construct the new release block
        notes = current_unreleased_notes + ("\n" + entry_line if current_unreleased_notes else entry_line)
        new_release_section = f"\n\n{header}\n{notes}"
        
        new_text = f"{before}\n\n{new_release_section}{rest}"
        CHANGELOG.write_text(new_text.strip() + "\n", encoding="utf-8")
    else:
        # Fallback: Just prepend after the title or append
        append = f"\n{header}\n{entry_line}\n"
        CHANGELOG.write_text(text.rstrip() + "\n" + append, encoding="utf-8")

def run_git(args: list[str]):
    try:
        subprocess.run(["git"] + args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e.stderr}")
        sys.exit(1)

def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Bump version and update CHANGELOG.md")
    parser.add_argument("part", choices=("major", "minor", "patch"))
    parser.add_argument("entry", nargs="*", help="Optional changelog entry text")
    parser.add_argument("-a", "--all", action="store_true", help="Do everything: commit, push, tag, and push tag")
    parser.add_argument("-t", "--create-tag", action="store_true", help="Create a git tag")
    parser.add_argument("-p", "--push-tag", action="store_true", help="Push tag to origin")
    parser.add_argument("-c", "--commit", action="store_true", help="Commit changes")
    parser.add_argument("-P", "--push-commit", action="store_true", help="Push commit to origin")

    args = parser.parse_args(argv[1:])
    
    # The -a flag enables all other workflow flags
    if args.all:
        args.commit = args.push_commit = args.create_tag = args.push_tag = True

    current = read_version()
    new = bump(current, args.part)
    entry = " ".join(args.entry) if args.entry else None

    # 1. Update Files
    write_version(new)
    update_changelog(new, entry)
    print(f"Bumped {current} -> {new}")

    tag_name = f"v{new}"

    # 2. Git Commit (Removed [skip ci] to ensure CI triggers on the tag)
    if args.commit:
        run_git(["add", "VERSION", "CHANGELOG.md"])
        run_git(["commit", "-m", f"Release {tag_name}"])
        print(f"Committed: Release {tag_name}")

    # 3. Push Commit (Crucial: Push code BEFORE tag to ensure CI finds the commit)
    if args.push_commit:
        run_git(["push", "origin", "HEAD"])
        print("Pushed commit to origin")

    # 4. Create Tag
    if args.create_tag:
        run_git(["tag", "-a", tag_name, "-m", f"Version {new}"])
        print(f"Created tag: {tag_name}")

    # 5. Push Tag
    if args.push_tag:
        run_git(["push", "origin", tag_name])
        print(f"Pushed tag {tag_name} to origin")

if __name__ == "__main__":
    main(sys.argv)