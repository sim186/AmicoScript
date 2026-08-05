"""Slash command registry and handlers."""
from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from .api import UNCHANGED
from .errors import explain

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


@command("export", f"export <id> <fmt: {'|'.join(EXPORT_FORMATS)}> [wikilinks]")
async def _export(app, args):
    if len(args) < 2:
        app.notify(f"usage: /export <id> <{'|'.join(EXPORT_FORMATS)}> [wikilinks]")
        return
    rec_id, fmt = args[0], args[1].lower()
    if fmt not in EXPORT_FORMATS:
        app.notify(f"unknown format {fmt!r}; use one of {', '.join(EXPORT_FORMATS)}")
        return
    wikilinks = len(args) > 2 and args[2].lower() == "wikilinks"
    if wikilinks and fmt != "md":
        app.notify("wikilinks only apply to md; exporting without them")
        wikilinks = False
    body, filename = await app.api.export(rec_id, fmt, wikilinks=wikilinks)
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


@command("tag-suggest", "suggest tags for recording <id> with the LLM")
async def _tag_suggest(app, args):
    if not args:
        app.notify("usage: /tag-suggest <id>")
        return
    rec_id = args[0]
    app.notify("reading the transcript…")
    try:
        data = await app.api.suggest_tags(rec_id)
    except Exception as exc:
        app.notify(explain(exc, "could not suggest tags"), severity="error")
        return

    names = [s["name"] for s in data.get("suggestions", [])]
    if not names:
        app.notify("no new tags to suggest")
        return
    # Suggestions only; /tag-toggle applies the ones that are wanted.
    app.notify(f"suggested: {', '.join(names)} — apply with /tag-toggle {rec_id}")


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




@command("retry", "transcribe recording <id> again")
async def _retry(app, args):
    """Re-run a transcription that failed, was cancelled, or hit a restart."""
    if not args:
        from .palette import Palette, seed_palette
        pal = Palette()
        app.push_screen(pal)
        pal.call_after_refresh(seed_palette, pal, "/retry ")
        return
    rec_id = args[0]
    try:
        result = await app.api.retry_recording(rec_id)
    except Exception as exc:
        app.notify(explain(exc, "retry failed"), severity="error")
        return
    app.notify(f"queued again: {rec_id[:8]}…")
    job_id = result.get("job_id")
    if job_id:
        await _follow_job(app, job_id)
    screen = app.screen
    if hasattr(screen, "refresh_library"):
        screen.refresh_library()


async def _follow_job(app, job_id: str) -> None:
    """Open the job detail screen if the app knows how to."""
    try:
        from .screens.job_detail import JobDetailScreen
        app.push_screen(JobDetailScreen(job_id))
    except Exception:
        pass


@command("backup", "backup export [path] | backup import <path> [overwrite]")
async def _backup(app, args):
    """Save the whole library to a file, or restore one."""
    if not args or args[0] not in {"export", "import"}:
        app.notify("usage: /backup export [path] | /backup import <path> [overwrite]")
        return

    if args[0] == "export":
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = Path(args[1]) if len(args) > 1 else Path.cwd() / f"amicoscript-library-{stamp}.zip"
        app.notify("exporting library… this can take a while with audio")
        try:
            await app.api.export_library(dest)
        except Exception as exc:
            app.notify(explain(exc, "export failed"), severity="error")
            return
        size_mb = dest.stat().st_size / 1024 / 1024
        app.notify(f"saved: {dest} ({size_mb:.1f} MB)")
        return

    if len(args) < 2:
        app.notify("usage: /backup import <path> [overwrite]")
        return
    source = Path(args[1]).expanduser()
    if not source.exists():
        app.notify(f"no such file: {source}", severity="error")
        return
    mode = "overwrite" if len(args) > 2 and args[2].lower() == "overwrite" else "skip"

    from .widgets.confirm import ConfirmDialog
    question = (
        f"Import {source.name}? Existing recordings will be "
        + ("replaced." if mode == "overwrite" else "left as they are.")
    )
    if not await app.push_screen_wait(ConfirmDialog(question)):
        return

    try:
        result = await app.api.import_library(source, mode=mode)
    except Exception as exc:
        app.notify(explain(exc, "import failed"), severity="error")
        return
    counts = result.get("imported", {})
    app.notify(
        f"imported {counts.get('recordings', 0)} recording(s), "
        f"{counts.get('audio', 0)} audio file(s)"
    )
    screen = app.screen
    if hasattr(screen, "refresh_library"):
        screen.refresh_library()


@command("llm-providers", "list the supported LLM backends")
async def _llm_providers(app, args):
    try:
        info = await app.api.llm_providers()
    except Exception as exc:
        app.notify(explain(exc, "could not load providers"), severity="error")
        return

    lines = []
    for provider in info.get("providers", []):
        key = {
            "required": "key required",
            "optional": "key optional",
            "none": "no key",
        }.get(provider.get("api_key"), "")
        marks = [m for m in (key, "hosted" if provider.get("cloud") else "") if m]
        lines.append(
            f"  [b]{provider['id']}[/] — {provider['label']}"
            + (f"  [#6b6e9a]{provider.get('base_url') or 'custom address'}"
               f"{' · ' + ' · '.join(marks) if marks else ''}[/]")
        )
    if info.get("in_container"):
        lines.append(
            f"  [#6b6e9a]server runs in a container; localhost is rewritten to "
            f"{info.get('container_host')}[/]"
        )
    app.notify("set one with /settings, or scan with /llm-detect")
    _show_lines(app, "LLM providers", lines)


@command("llm-detect", "scan for a running local LLM server")
async def _llm_detect(app, args):
    app.notify("scanning the usual ports…")
    try:
        info = await app.api.llm_detect()
    except Exception as exc:
        app.notify(explain(exc, "scan failed"), severity="error")
        return

    servers = info.get("servers", [])
    if not servers:
        app.notify(
            f"nothing answered on {len(info.get('scanned', []))} addresses — "
            "start Ollama, LM Studio or Unsloth Studio and scan again",
            severity="warning",
        )
        return

    lines = []
    for server in servers:
        detail = (
            "needs an API key" if server.get("needs_api_key")
            else f"{server.get('model_count', 0)} model(s)"
        )
        lines.append(f"  [b]{server['label']}[/] — {server['base_url']}  [#6b6e9a]{detail}[/]")

    # Adopt the first find, which is what the web UI's one-click button does.
    first = servers[0]
    from .widgets.confirm import ConfirmDialog
    if await app.push_screen_wait(
        ConfirmDialog(f"Use {first['label']} at {first['base_url']}?")
    ):
        model = (first.get("models") or [{}])[0].get("id")
        try:
            await app.api.save_llm_settings(
                provider=first["provider"],
                base_url=first["base_url"],
                model_name=model,
                api_key=UNCHANGED,
            )
        except Exception as exc:
            app.notify(explain(exc, "could not save"), severity="error")
            return
        note = " — paste its API key in /settings" if first.get("needs_api_key") else ""
        app.notify(f"using {first['label']}{note}")
    else:
        _show_lines(app, "Detected servers", lines)


def _show_lines(app, title: str, lines: list[str]) -> None:
    """Print a block into the app's log/console area, or notify as a fallback."""
    text = f"[b]{title}[/]\n" + "\n".join(lines)
    for method in ("write_log", "log_line"):
        fn = getattr(app, method, None)
        if callable(fn):
            fn(text)
            return
    app.notify(text.replace("[b]", "").replace("[/]", ""))


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
