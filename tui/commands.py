"""Slash command registry and handlers."""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .app import AmicoTUI


@dataclass
class Command:
    name: str
    help: str
    handler: Callable[["AmicoTUI", list[str]], Awaitable[None]]


COMMANDS: dict[str, Command] = {}


def command(name: str, help: str):
    def decorator(fn):
        COMMANDS[name] = Command(name, help, fn)
        return fn
    return decorator


def list_commands() -> list[Command]:
    return sorted(COMMANDS.values(), key=lambda c: c.name)


async def run_command(app: "AmicoTUI", raw: str) -> None:
    raw = raw.strip()
    if not raw:
        return
    if raw.startswith("/"):
        raw = raw[1:]
    try:
        parts = shlex.split(raw)
    except ValueError as e:
        app.notify(f"parse error: {e}")
        return
    if not parts:
        return
    cmd_name, *args = parts
    cmd = COMMANDS.get(cmd_name)
    if cmd is None:
        app.notify(f"unknown command: /{cmd_name}")
        return
    try:
        await cmd.handler(app, args)
    except Exception as e:
        app.notify(f"/{cmd_name} failed: {e}")


# --- handlers --------------------------------------------------------


@command("help", "show command reference")
async def _help(app, args):
    lines = [f"/{c.name} — {c.help}" for c in list_commands()]
    app.notify("\n".join(lines), timeout=10)


@command("transcribe", "upload <path> and transcribe")
async def _transcribe(app, args):
    if not args:
        app.notify("usage: /transcribe <path>")
        return
    path = Path(args[0]).expanduser()
    if not path.is_file():
        app.notify(f"not a file: {path}")
        return
    result = await app.api.transcribe_file(path)
    job_id = result.get("job_id") or result.get("id")
    if job_id:
        from .screens.jobs import JobScreen
        app.push_screen(JobScreen(job_id))
    else:
        app.notify(f"submitted: {result}")


@command("transcribe-url", "transcribe from <url>")
async def _transcribe_url(app, args):
    if not args:
        app.notify("usage: /transcribe-url <url>")
        return
    result = await app.api.transcribe_url(args[0])
    jobs = result.get("jobs") or ([result] if result.get("job_id") else [])
    if jobs:
        from .screens.jobs import JobScreen
        app.push_screen(JobScreen(jobs[0].get("job_id") or jobs[0].get("id")))
    else:
        app.notify(f"submitted: {result}")


@command("search", "full-text search <query>")
async def _search(app, args):
    if not args:
        app.notify("usage: /search <query>")
        return
    q = " ".join(args)
    data = await app.api.search(q)
    hits = data.get("results") or data.get("hits") or []
    if not hits:
        app.notify("no results")
        return
    lines = [
        f"{h.get('recording_id', '')[:8]}  {h.get('snippet') or h.get('text', '')[:60]}"
        for h in hits[:20]
    ]
    app.notify("\n".join(lines), timeout=10)


@command("export", "export <id> <fmt: json|srt|txt|md>")
async def _export(app, args):
    if len(args) < 2:
        app.notify("usage: /export <id> <fmt>")
        return
    rec_id, fmt = args[0], args[1]
    body, filename = await app.api.export(rec_id, fmt)
    out = Path.cwd() / (filename or f"{rec_id}.{fmt}")
    out.write_bytes(body)
    app.notify(f"saved: {out}")


@command("cancel", "cancel job <job_id>")
async def _cancel(app, args):
    if not args:
        app.notify("usage: /cancel <job_id>")
        return
    await app.api.cancel_job(args[0])
    app.notify(f"cancel sent for {args[0]}")


@command("delete", "delete recording <id>")
async def _delete(app, args):
    if not args:
        app.notify("usage: /delete <id>")
        return
    await app.api.delete_recording(args[0])
    app.notify(f"deleted {args[0]}")
    screen = app.screen
    if hasattr(screen, "refresh_library"):
        screen.refresh_library()


@command("folder", "pick a folder (or 'new <name>' to create)")
async def _folder(app, args):
    if args and args[0] == "new":
        if len(args) < 2:
            app.notify("usage: /folder new <name>")
            return
        name = " ".join(args[1:])
        await app.api.create_folder(name)
        app.notify(f"folder created: {name}")
        return
    # No args (or non-'new' args) — re-open palette in folder-pick mode.
    from .palette import Palette, seed_palette
    pal = Palette()
    app.push_screen(pal)
    pal.call_after_refresh(seed_palette, pal, "/folder ")


@command("tag", "pick a tag to scope library")
async def _tag(app, args):
    from .palette import Palette, seed_palette
    pal = Palette()
    app.push_screen(pal)
    pal.call_after_refresh(seed_palette, pal, "/tag ")




@command("logs", "show server log buffer")
async def _logs(app, args):
    if not app.server or not app.server.logs:
        app.notify("no logs captured")
        return
    tail = list(app.server.logs)[-40:]
    app.notify("\n".join(tail), timeout=12)


@command("settings", "open settings screen")
async def _settings(app, args):
    from .screens.settings import SettingsScreen
    app.push_screen(SettingsScreen())


@command("library", "open the recordings library")
async def _library(app, args):
    from .screens.library import LibraryScreen
    app.push_screen(LibraryScreen())


@command("jobs", "open the active-jobs list")
async def _jobs(app, args):
    from .screens.library import JobsListScreen
    app.push_screen(JobsListScreen())


@command("welcome", "return to the welcome screen")
async def _welcome(app, args):
    # Pop everything back to the welcome screen.
    while len(app.screen_stack) > 1:
        app.pop_screen()


@command("analyze", "pick a recording and run analysis")
async def _analyze(app, args):
    """Three forms:

    * ``/analyze`` — open palette in analyze mode (pick recording → type)
    * ``/analyze <rec_id>`` — skip the recording picker, choose type
    * ``/analyze <rec_id> <type> [extra]`` — fire immediately
    """
    from .palette import Palette, _open_analysis_type_picker, seed_palette
    if not args:
        pal = Palette()
        app.push_screen(pal)
        pal.call_after_refresh(seed_palette, pal, "/analyze ")
        return
    rec_id = args[0]
    if len(args) == 1:
        _open_analysis_type_picker(app, rec_id)
        return
    atype = args[1]
    extra: dict = {}
    if atype == "translate" and len(args) >= 3:
        extra["target_language"] = args[2]
    elif atype == "custom" and len(args) >= 3:
        extra["custom_prompt"] = " ".join(args[2:])
    try:
        await app.api.create_analysis(rec_id, atype, **extra)
        app.notify(f"{atype} analysis queued for {rec_id[:8]}")
    except Exception as e:
        app.notify(f"analysis failed: {e}", severity="error")


@command("models", "pick an LLM model")
async def _models(app, args):
    from .palette import Palette, seed_palette
    pal = Palette()
    app.push_screen(pal)
    pal.call_after_refresh(seed_palette, pal, "/models ")


@command("llm", "open LLM settings")
async def _llm(app, args):
    from .screens.settings import SettingsScreen
    app.push_screen(SettingsScreen())


@command("quit", "exit the app")
async def _quit(app, args):
    app.exit()
