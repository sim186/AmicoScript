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
    app.push_busy()
    try:
        await cmd.handler(app, args)
    except Exception as e:
        app.notify(f"/{cmd_name} failed: {e}")
    finally:
        app.pop_busy()


# --- handlers --------------------------------------------------------


async def _whisper_options(app) -> dict:
    """Return the saved Whisper model from settings for transcribe calls."""
    try:
        s = await app.api.settings()
        model = s.get("whisper_model", "").strip()
        if model:
            return {"model": model}
    except Exception:
        pass
    return {}


@command("help", "show command reference")
async def _help(app, args):
    from .screens.help import HelpScreen
    app.push_screen(HelpScreen())


@command("welcome", "return to the welcome screen")
async def _welcome(app, args):
    from .screens.welcome import WelcomeScreen
    while not isinstance(app.screen, WelcomeScreen) and len(app.screen_stack) > 1:
        app.pop_screen()


@command("transcribe", "upload <path> and transcribe")
async def _transcribe(app, args):
    if not args:
        from .palette import Palette, seed_palette
        pal = Palette()
        app.push_screen(pal)
        pal.call_after_refresh(seed_palette, pal, "/transcribe ")
        return

    arg = args[0]

    if arg.startswith("@"):
        from .palette import Palette, seed_palette
        pal = Palette()
        app.push_screen(pal)
        pal.call_after_refresh(seed_palette, pal, f"/transcribe {arg}")
        return

    path = Path(arg).expanduser()
    if path.is_file():
        opts = await _whisper_options(app)
        result = await app.api.transcribe_file(path, options=opts)
        job_id = result.get("job_id") or result.get("id")
        if job_id:
            from .screens.job_detail import JobDetailScreen
            app.push_screen(JobDetailScreen(job_id))
        else:
            app.notify(f"submitted: {result}")
        return

    from .palette import Palette, seed_palette
    pal = Palette()
    app.push_screen(pal)
    pal.call_after_refresh(seed_palette, pal, f"/transcribe {path}")


@command("transcribe-url", "transcribe from <url>")
async def _transcribe_url(app, args):
    if not args:
        app.notify("usage: /transcribe-url <url>")
        return
    opts = await _whisper_options(app)
    result = await app.api.transcribe_url(args[0], **opts)
    jobs = result.get("jobs") or ([result] if result.get("job_id") else [])
    if jobs:
        from .screens.job_detail import JobDetailScreen
        app.push_screen(JobDetailScreen(jobs[0].get("job_id") or jobs[0].get("id")))
    else:
        app.notify(f"submitted: {result}")


@command("search", "full-text search <query>")
async def _search(app, args):
    if not args:
        app.notify("usage: /search <query>")
        return
    q = " ".join(args)
    from .screens.search import SearchScreen
    app.push_screen(SearchScreen(q))


EXPORT_FORMATS = ("json", "srt", "vtt", "txt", "md", "csv")


@command("export", f"export <id> <fmt: {'|'.join(EXPORT_FORMATS)}>")
async def _export(app, args):
    if len(args) < 2:
        app.notify(f"usage: /export <id> <{'|'.join(EXPORT_FORMATS)}>")
        return
    rec_id, fmt = args[0], args[1].lower()
    if fmt not in EXPORT_FORMATS:
        app.notify(f"unknown format {fmt!r}; use one of {', '.join(EXPORT_FORMATS)}")
        return
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
        from .palette import Palette, seed_palette
        pal = Palette()
        app.push_screen(pal)
        pal.call_after_refresh(seed_palette, pal, "/delete ")
        return
    rec_id = args[0]
    from .widgets.confirm import ConfirmDialog
    confirmed = await app.push_screen_wait(
        ConfirmDialog(f"Delete recording {rec_id[:8]}…? This cannot be undone.")
    )
    if not confirmed:
        return
    await app.api.delete_recording(rec_id)
    app.notify(f"deleted {rec_id}")
    screen = app.screen
    if hasattr(screen, "refresh_library"):
        screen.refresh_library()


@command("rename", "rename recording <id> <new name>")
async def _rename(app, args):
    if len(args) < 2:
        app.notify("usage: /rename <id> <new name>")
        return
    rec_id, *name_parts = args
    alias = " ".join(name_parts)
    try:
        await app.api.update_recording(rec_id, alias=alias)
        app.notify(f"renamed {rec_id[:8]} → {alias}")
        screen = app.screen
        if hasattr(screen, "refresh_library"):
            screen.refresh_library()
    except Exception as e:
        app.notify(f"rename failed: {e}", severity="error")


@command("move", "move recording <id> to a folder")
async def _move(app, args):
    if not args:
        app.notify("usage: /move <id> [folder_id]")
        return
    rec_id, *rest = args
    if rest:
        try:
            await app.api.update_recording(rec_id, folder_id=rest[0])
            app.notify(f"moved {rec_id[:8]}")
            screen = app.screen
            if hasattr(screen, "refresh_library"):
                screen.refresh_library()
        except Exception as e:
            app.notify(f"move failed: {e}", severity="error")
        return
    from .palette import _open_move_to_folder_picker
    _open_move_to_folder_picker(app, rec_id)


@command("tag-toggle", "add/remove a tag on recording <id>")
async def _tag_toggle(app, args):
    if not args:
        app.notify("usage: /tag-toggle <id>")
        return
    from .palette import _open_tag_toggle_picker
    _open_tag_toggle_picker(app, args[0])


@command("folder", "pick a folder (or 'new <name>' / 'rename <id> <name>' / 'delete <id>')")
async def _folder(app, args):
    if args and args[0] == "new":
        if len(args) < 2:
            app.notify("usage: /folder new <name>")
            return
        name = " ".join(args[1:])
        await app.api.create_folder(name)
        app.notify(f"folder created: {name}")
        return
    if args and args[0] == "rename":
        if len(args) < 3:
            app.notify("usage: /folder rename <id> <new name>")
            return
        folder_id, *name_parts = args[1:]
        name = " ".join(name_parts)
        try:
            await app.api.update_folder(folder_id, name=name)
            app.notify(f"folder renamed to {name}")
        except Exception as e:
            app.notify(f"rename failed: {e}", severity="error")
        return
    if args and args[0] == "delete":
        if len(args) < 2:
            app.notify("usage: /folder delete <id>")
            return
        folder_id = args[1]
        from .widgets.confirm import ConfirmDialog
        confirmed = await app.push_screen_wait(
            ConfirmDialog(
                f"Delete folder {folder_id[:8]}…? Recordings inside move to "
                "All Recordings, they are not deleted."
            )
        )
        if not confirmed:
            return
        try:
            await app.api.delete_folder(folder_id)
            app.notify("folder deleted")
        except Exception as e:
            app.notify(f"delete failed: {e}", severity="error")
        return
    # No args (or unrecognised args) — re-open palette in folder-pick mode.
    from .palette import Palette, seed_palette
    pal = Palette()
    app.push_screen(pal)
    pal.call_after_refresh(seed_palette, pal, "/folder ")


@command("tag", "pick a tag (or 'new <name>' / 'rename <id> <name>' / 'delete <id>')")
async def _tag(app, args):
    if args and args[0] == "new":
        if len(args) < 2:
            app.notify("usage: /tag new <name>")
            return
        name = " ".join(args[1:])
        await app.api.create_tag(name)
        app.notify(f"tag created: {name}")
        return
    if args and args[0] == "rename":
        if len(args) < 3:
            app.notify("usage: /tag rename <id> <new name>")
            return
        tag_id, *name_parts = args[1:]
        name = " ".join(name_parts)
        try:
            await app.api.update_tag(tag_id, name=name)
            app.notify(f"tag renamed to {name}")
        except Exception as e:
            app.notify(f"rename failed: {e}", severity="error")
        return
    if args and args[0] == "delete":
        if len(args) < 2:
            app.notify("usage: /tag delete <id>")
            return
        tag_id = args[1]
        from .widgets.confirm import ConfirmDialog
        confirmed = await app.push_screen_wait(
            ConfirmDialog(f"Delete tag {tag_id[:8]}…? It's removed from every recording.")
        )
        if not confirmed:
            return
        try:
            await app.api.delete_tag(tag_id)
            app.notify("tag deleted")
        except Exception as e:
            app.notify(f"delete failed: {e}", severity="error")
        return
    # No args (or unrecognised args) — re-open palette in tag-pick mode.
    from .palette import Palette, seed_palette
    pal = Palette()
    app.push_screen(pal)
    pal.call_after_refresh(seed_palette, pal, "/tag ")




@command("logs", "show server log buffer")
async def _logs(app, args):
    from .screens.logs import LogsScreen
    app.push_screen(LogsScreen())


@command("settings", "open settings screen")
async def _settings(app, args):
    from .screens.settings import SettingsScreen
    app.push_screen(SettingsScreen())


@command("import", "browse the filesystem to pick a file to transcribe")
async def _import(app, args):
    from .screens.import_ import ImportScreen
    start = Path(args[0]).expanduser() if args else None
    app.push_screen(ImportScreen(start))


@command("library", "open the recordings library")
async def _library(app, args):
    from .screens.library import LibraryScreen
    app.push_screen(LibraryScreen())


@command("jobs", "open the active-jobs list")
async def _jobs(app, args):
    from .screens.jobs_list import JobsListScreen
    app.push_screen(JobsListScreen())


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


@command("models", "pick a Whisper transcription model")
async def _models(app, args):
    from .palette import Palette, seed_palette
    pal = Palette()
    app.push_screen(pal)
    pal.call_after_refresh(seed_palette, pal, "/models ")


@command("llm", "pick an LLM model")
async def _llm(app, args):
    from .palette import Palette, seed_palette
    pal = Palette()
    app.push_screen(pal)
    pal.call_after_refresh(seed_palette, pal, "/llm ")


@command("quit", "exit the app")
async def _quit(app, args):
    app.exit()
