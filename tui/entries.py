"""What the palette can offer, and what picking it does.

The palette is three things stacked on top of each other: a modal widget, a
set of picker flows that push that widget with a prepared list, and — between
them — the translation from an API payload into rows a person can see and
choose. This module is that middle layer.

It is pure. Every function here takes data that has already been fetched and
returns a list of :class:`Entry`; nothing in this file awaits, touches
``app.api`` or pushes a screen. That is what makes the six shapes the backend
returns — some of which are a list, some a dict with a ``models`` key, some
either — testable without a running app, which is how the UUID-vs-int crash
these builders once had is kept from coming back.

The ``on_select`` a builder attaches is the other half: an entry knows what it
does, so the palette can stay ignorant of what it is showing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from .actions import ANALYSIS_TYPES
from .commands import list_commands, run_command

if TYPE_CHECKING:
    from .app import AmicoTUI


# The glyph each kind of row is displayed with. Tab-completion turns a
# selected entry back into the slash command that would have produced it, by
# stripping this prefix off the display text — so the two have to agree, and
# did not while both spelled the character out separately.
RECORDING_PREFIX = "♪ "
FOLDER_PREFIX = "▣ "
TAG_PREFIX = "# "
ANALYSIS_TYPE_PREFIX = "✦ "


@dataclass
class Entry:
    kind: str            # "command" | "recording" | "folder" | "tag"
    key: str             # stable identifier for MRU
    display: str         # one-line label
    subtitle: str        # dim hint
    search_text: str     # text fuzzy-matched against
    on_select: Callable[["AmicoTUI"], Awaitable[None]]


# --- selection adapters ------------------------------------------------------
#
# Each returns the coroutine function an Entry carries as its on_select. The
# screen imports are deferred because those modules import the palette back.


async def noop(app: "AmicoTUI") -> None:
    """For entries whose picker handles the choice through ``on_pick``."""
    return None


def run_cmd(name: str):
    async def go(app: "AmicoTUI") -> None:
        await run_command(app, name)
    return go


def open_recording(rec_id: str):
    async def go(app: "AmicoTUI") -> None:
        from .screens.transcript import TranscriptScreen
        app.push_screen(TranscriptScreen(rec_id))
    return go


def open_library_folder(folder_id: str):
    async def go(app: "AmicoTUI") -> None:
        from .screens.library import LibraryScreen
        app.push_screen(LibraryScreen(folder_id=folder_id, title=f"Folder · {folder_id[:8]}"))
    return go


def open_library_tag(tag_id: str):
    async def go(app: "AmicoTUI") -> None:
        from .screens.library import LibraryScreen
        app.push_screen(LibraryScreen(tag_id=tag_id, title=f"Tag · {tag_id[:8]}"))
    return go


def set_whisper_model(name: str):
    async def go(app: "AmicoTUI") -> None:
        try:
            await app.api.save_whisper_model(name)
            app.notify(f"whisper model set to {name}")
        except Exception as e:
            app.notify(f"failed to save: {e}", severity="error")
    return go


def set_llm_model(name: str):
    async def go(app: "AmicoTUI") -> None:
        try:
            await app.api.save_llm_settings(model_name=name)
            app.notify(f"LLM model set to {name}")
        except Exception as e:
            app.notify(f"failed to save: {e}", severity="error")
    return go


# --- builders, one per data type --------------------------------------------


def entries_from_commands() -> list[Entry]:
    return [
        Entry(
            kind="command",
            key=f"command:{c.name}",
            display=f"/{c.name}",
            subtitle=c.help,
            search_text=f"/{c.name} {c.help}",
            on_select=run_cmd(c.name),
        )
        for c in list_commands()
    ]


def entries_from_recordings(items: list[dict] | None) -> list[Entry]:
    out: list[Entry] = []
    for r in items or []:
        rid = str(r.get("id", ""))
        name = r.get("alias") or r.get("filename") or f"#{rid}"
        out.append(Entry(
            kind="recording",
            key=f"recording:{rid}",
            display=f"{RECORDING_PREFIX}{name}",
            subtitle=f"{r.get('status', '')} · {rid[:8]}",
            search_text=f"{name} {rid}",
            on_select=open_recording(rid),
        ))
    return out


def entries_from_folders(folders: list[dict] | None) -> list[Entry]:
    return [
        Entry(
            kind="folder",
            key=f"folder:{f.get('id')}",
            display=f"{FOLDER_PREFIX}{f.get('name', '?')}",
            subtitle=f"folder · id {str(f.get('id'))[:8]}",
            search_text=str(f.get("name", "")),
            on_select=open_library_folder(str(f.get("id"))),
        )
        for f in (folders or []) if f.get("id") is not None
    ]


def entries_from_tags(tags: list[dict] | None) -> list[Entry]:
    return [
        Entry(
            kind="tag",
            key=f"tag:{t.get('id')}",
            display=f"{TAG_PREFIX}{t.get('name', '?')}",
            subtitle=f"tag · id {str(t.get('id'))[:8]}",
            search_text=str(t.get("name", "")),
            on_select=open_library_tag(str(t.get("id"))),
        )
        for t in (tags or []) if t.get("id") is not None
    ]


def entries_from_models(data) -> list[Entry]:
    """Whisper models. The endpoint answers with a dict, a bare list, or strings."""
    items = data.get("models") if isinstance(data, dict) else (data or [])
    entries: list[Entry] = []
    for it in items or []:
        if isinstance(it, dict):
            mid = str(it.get("id", ""))
            if not mid:
                continue
            name = it.get("name", mid)
            subtitle = (
                f"Whisper · {it.get('params', '')} · {it.get('ram', '')} · "
                f"accuracy {it.get('accuracy', '?')}/5"
            )
        elif isinstance(it, str):
            mid = name = it
            subtitle = "Whisper model"
        else:
            continue
        entries.append(Entry(
            kind="model",
            key=f"model:{mid}",
            display=f"{name}",
            subtitle=subtitle,
            search_text=f"{mid} {name}",
            on_select=set_whisper_model(mid),
        ))
    return entries


def entries_from_llm_models(data) -> list[Entry]:
    """LLM models. Providers disagree about which key holds the id."""
    items = data if isinstance(data, list) else (data.get("models") if isinstance(data, dict) else [])
    entries: list[Entry] = []
    for it in items or []:
        if isinstance(it, str):
            mid = name = it
        elif isinstance(it, dict):
            mid = str(it.get("id") or it.get("name") or it.get("model") or "")
            name = str(it.get("name") or it.get("id") or mid)
        else:
            continue
        if not mid:
            continue
        entries.append(Entry(
            kind="llm_model",
            key=f"llm_model:{mid}",
            display=f"{name}",
            subtitle="set as default LLM model",
            search_text=mid,
            on_select=set_llm_model(mid),
        ))
    return entries


def entries_from_analysis_types() -> list[Entry]:
    return [
        Entry(
            kind="analysis_type",
            key=f"analysis_type:{name}",
            display=f"{ANALYSIS_TYPE_PREFIX}{name}",
            subtitle=desc,
            search_text=name,
            on_select=noop,
        )
        for name, desc in ANALYSIS_TYPES
    ]


# --- builders for the pickers that answer through on_pick --------------------
#
# Same data, different job: these rows are a choice the caller acts on, not a
# navigation. Their on_select is noop and the picker's on_pick does the work,
# which is also why they can show state the navigation entries cannot — which
# tags are already applied, and the option to belong to no folder at all.


def folder_choice_entries(folders: list[dict] | None, include_none: bool = True) -> list[Entry]:
    entries: list[Entry] = []
    if include_none:
        entries.append(Entry(
            kind="folder",
            key="folder:",
            display="▢ (no folder)",
            subtitle="remove from any folder",
            search_text="no folder none",
            on_select=noop,
        ))
    entries += [
        Entry(
            kind="folder",
            key=f"folder:{f.get('id')}",
            display=f"{FOLDER_PREFIX}{f.get('name', '?')}",
            subtitle=f"folder · id {str(f.get('id'))[:8]}",
            search_text=str(f.get("name", "")),
            on_select=noop,
        )
        for f in (folders or []) if f.get("id") is not None
    ]
    return entries


def tag_choice_entries(tags: list[dict] | None, applied_ids: set[str] | None = None) -> list[Entry]:
    applied_ids = applied_ids or set()
    return [
        Entry(
            kind="tag",
            key=f"tag:{t.get('id')}",
            display=f"{'●' if str(t.get('id')) in applied_ids else '○'} {t.get('name', '?')}",
            subtitle="applied — enter removes" if str(t.get("id")) in applied_ids else "enter adds",
            search_text=str(t.get("name", "")),
            on_select=noop,
        )
        for t in (tags or []) if t.get("id") is not None
    ]
