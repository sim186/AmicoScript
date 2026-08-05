"""Transcript screen: waveform + segments + playback."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static

from ..clipboard import copy_to_clipboard
from ..playback import Player
from ..waveform import compute_levels_async
from ..widgets.chrome import CommandBar, ContextHint, TitleBar
from ..widgets.segment_list import SegmentList, parse_timestamp
from ..widgets.status_bar import StatusBar
from ..widgets.waveform_view import WaveformView

if TYPE_CHECKING:
    from ..app import AmicoTUI


class TranscriptScreen(Screen):
    BINDINGS = [
        Binding("escape", "pop", "Back"),
        Binding("q", "pop", "Back"),
        Binding("y", "copy_segment", "Copy seg"),
        Binding("Y", "copy_all", "Copy all"),
        Binding("space", "toggle_play", "Play/Pause"),
        Binding("s", "stop_play", "Stop"),
        Binding("ctrl+a", "analyze", "Analyze"),
        Binding("slash", "focus_search", "Find"),
        Binding("e", "edit_segment", "Edit"),
        Binding("ctrl+r", "reset_segment", "Reset seg"),
        Binding("a", "assign_speaker", "Assign speaker"),
        Binding("S", "rename_speaker", "Rename speaker"),
    ]

    DEFAULT_CSS = """
    TranscriptScreen { layout: vertical; }
    #meta {
        height: 1;
        padding: 0 2;
        background: #12152a;
        color: #dde1ff;
        border-bottom: solid #2a2860;
    }
    #legend {
        height: 1;
        padding: 0 2;
        background: #0c0e1a;
        color: #6b6e9a;
        border-bottom: solid #2a2860;
    }
    #findline {
        height: 3;
        padding: 0 2;
        background: #12152a;
        color: #dde1ff;
        border-bottom: solid #2a2860;
        display: none;
    }
    #findline Static {
        width: 6;
        height: 3;
        content-align: left middle;
        color: #7c79f0;
    }
    #findline Input {
        height: 3;
        background: #12152a;
        color: #dde1ff;
        border: none;
    }
    """

    leader_chords = {
        "l": ("Library", "/library"),
        "j": ("Jobs", "/jobs"),
        "s": ("Settings", "/settings"),
        "h": ("Welcome", "/welcome"),
        "question_mark": ("Help", "/help"),
        "q": ("Quit", "/quit"),
    }

    def __init__(self, recording_id: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.recording_id = recording_id
        self._tmp_audio: Path | None = None
        self.player = Player()
        self.duration_s: float = 0.0
        self._anim_timer = None
        self.title = "Transcript"

    def compose(self):
        yield TitleBar(id="titlebar")
        yield Static("loading…", id="meta")
        yield Static("", id="legend")
        with Horizontal(id="findline"):
            yield Static("find:", id="findlabel")
            yield Input(placeholder="text, or a timestamp like 1:23…", id="findinput")
        with Vertical():
            yield WaveformView(id="wave")
            yield SegmentList(id="segments")
        yield ContextHint(
            "Space play  ·  y copy  ·  / find  ·  e edit  ·  a speaker  ·  S rename speaker  ·  ^A analyze",
            id="ctxhint",
        )
        yield CommandBar(id="cmdbar")
        yield StatusBar(id="statusbar")

    def on_mount(self) -> None:
        self.query_one(SegmentList).focus()
        self.run_worker(self._load(), exclusive=True)
        self._anim_timer = self.set_interval(1 / 15, self._tick, pause=False)

    async def _load(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        meta = self.query_one("#meta", Static)
        legend = self.query_one("#legend", Static)
        seg_list = self.query_one(SegmentList)
        status = self.query_one(StatusBar)

        try:
            rec = await app.api.recording(self.recording_id)
        except Exception as e:
            meta.update(f"[#ef4444]error: {e}[/]")
            return
        name = rec.get("alias") or rec.get("filename") or self.recording_id
        self.duration_s = float(rec.get("duration") or 0.0)
        model = rec.get("model_size") or rec.get("model") or ""

        try:
            tdata = await app.api.transcript(self.recording_id)
            segs = (
                tdata.get("segments")
                or tdata.get("json_data", {}).get("segments")
                or []
            )
            seg_list.load(segs)
        except Exception as e:
            status.set_connection(f"transcript load failed: {e}", ok=False)
            segs = []

        speakers = sorted({(s.get("speaker") or s.get("speaker_label") or "")
                          for s in segs} - {""})
        word_count = sum(len((s.get("text") or "").split()) for s in segs)
        meta.update(
            f"[b #dde1ff]{name}[/]  [#6b6e9a]·  "
            f"{self._fmt_dur(self.duration_s)}  ·  {word_count:,} words  ·  "
            f"{len(speakers)} speakers  ·  [/][#7c79f0]{model}[/]"
        )
        if speakers:
            chips = "  ".join(
                f"[{seg_list.speaker_color(sp)}]■[/] [#dde1ff]{sp}[/]"
                for sp in speakers
            )
        else:
            chips = "[#6b6e9a]no speakers[/]"
        legend.update(
            f"{chips}        "
            f"[#6b6e9a]export:[/] [#7c79f0]/export json[/]  "
            f"[#7c79f0]/export srt[/]  [#7c79f0]/export vtt[/]  [#7c79f0]/export txt[/]  "
            f"[#7c79f0]/export md[/]  [#7c79f0]/export csv[/]"
        )

        self.run_worker(self._load_audio(), exclusive=False, name="audio")

    async def _load_audio(self) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        wave = self.query_one(WaveformView)
        status = self.query_one(StatusBar)
        status.flash("loading audio...")
        try:
            url = f"/api/recordings/{self.recording_id}/audio"
            tmp = tempfile.NamedTemporaryFile(
                prefix="amicoscript-tui-", suffix=".audio", delete=False
            )
            self._tmp_audio = Path(tmp.name)
            tmp.close()
            async with app.api.client.stream("GET", url) as r:
                r.raise_for_status()
                with self._tmp_audio.open("wb") as out:
                    async for chunk in r.aiter_bytes():
                        out.write(chunk)
            width = max(40, self.size.width - 6)
            wave.levels = await compute_levels_async(self._tmp_audio, width=width)
            status.flash("audio ready · Space to play")
        except Exception as e:
            status.flash(f"audio: {e}")

    def on_unmount(self) -> None:
        self.player.stop()
        if self._anim_timer is not None:
            self._anim_timer.stop()
        if self._tmp_audio and self._tmp_audio.exists():
            try:
                self._tmp_audio.unlink()
            except OSError:
                pass

    def _tick(self) -> None:
        wave = self.query_one(WaveformView)
        if self.player.is_playing() and self.duration_s > 0:
            pos = self.player.position()
            wave.position = max(0.0, min(1.0, pos / self.duration_s))

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        seg = self.query_one(SegmentList).selected_segment()
        if not seg:
            return
        self._play_from(float(seg.get("start", 0.0)))

    def action_pop(self) -> None:
        findline = self.query_one("#findline", Horizontal)
        if findline.display:
            self.action_clear_search()
            return
        self.app.pop_screen()

    def action_focus_search(self) -> None:
        self.query_one("#findline", Horizontal).display = True
        self.query_one("#findinput", Input).focus()

    def action_clear_search(self) -> None:
        self.query_one("#findinput", Input).value = ""
        self.query_one("#findline", Horizontal).display = False
        self.query_one(SegmentList).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "findinput":
            return
        self._run_find(event.value, cycle=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "findinput":
            return
        self._run_find(event.value, cycle=True)

    def _run_find(self, query: str, cycle: bool) -> None:
        seg_list = self.query_one(SegmentList)
        query = query.strip()
        if not query:
            return
        seconds = parse_timestamp(query)
        if seconds is not None:
            idx = seg_list.jump_to_time(seconds)
            if idx is not None:
                seg_list.highlighted = idx
                self.query_one(StatusBar).flash(f"jumped to {query}")
            return
        start_from = ((seg_list.highlighted or 0) + 1) if cycle else 0
        idx = seg_list.find_first(query, start_from=start_from)
        if idx is None:
            self.query_one(StatusBar).flash(f"no matches for “{query}”")
            return
        seg_list.highlighted = idx

    def action_copy_segment(self) -> None:
        seg = self.query_one(SegmentList).selected_segment()
        if not seg:
            return
        text = (seg.get("text") or "").strip()
        if copy_to_clipboard(text):
            self.query_one(StatusBar).flash("copied segment")

    def action_copy_all(self) -> None:
        segs = self.query_one(SegmentList).segments
        text = "\n".join((s.get("text") or "").strip() for s in segs)
        if copy_to_clipboard(text):
            self.query_one(StatusBar).flash(f"copied {len(segs)} segments")

    def action_toggle_play(self) -> None:
        if self.player.is_playing():
            self.player.stop()
            self.query_one(StatusBar).flash("paused")
            return
        seg = self.query_one(SegmentList).selected_segment()
        offset = float(seg.get("start", 0.0)) if seg else 0.0
        self._play_from(offset)

    def action_stop_play(self) -> None:
        self.player.stop()
        wave = self.query_one(WaveformView)
        wave.position = 0.0
        self.query_one(StatusBar).flash("stopped")

    def action_analyze(self) -> None:
        from ..palette import _open_analysis_type_picker
        _open_analysis_type_picker(self.app, self.recording_id)

    def action_edit_segment(self) -> None:
        seg_list = self.query_one(SegmentList)
        idx = seg_list.highlighted
        seg = seg_list.selected_segment()
        if idx is None or seg is None:
            return
        self.run_worker(self._edit_segment(idx, seg.get("text") or ""), exclusive=False)

    async def _edit_segment(self, index: int, current_text: str) -> None:
        from ..widgets.prompt import PromptDialog
        new_text = await self.app.push_screen_wait(
            PromptDialog("Edit segment text:", initial=current_text)
        )
        if not new_text or new_text == current_text:
            return
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        app.push_busy()
        try:
            await app.api.edit_segment(self.recording_id, index, new_text)
            self.query_one(SegmentList).update_segment_text(index, new_text)
            self.query_one(StatusBar).flash("segment updated")
        except Exception as e:
            app.notify(f"edit failed: {e}", severity="error")
        finally:
            app.pop_busy()

    def action_reset_segment(self) -> None:
        seg_list = self.query_one(SegmentList)
        idx = seg_list.highlighted
        if idx is None:
            return
        self.run_worker(self._reset_segment(idx), exclusive=False)

    async def _reset_segment(self, index: int) -> None:
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        app.push_busy()
        try:
            result = await app.api.reset_segment(self.recording_id, index)
            text = result.get("text", "")
            self.query_one(SegmentList).update_segment_text(index, text)
            self.query_one(StatusBar).flash("segment reset")
        except Exception as e:
            app.notify(f"reset failed: {e}", severity="error")
        finally:
            app.pop_busy()

    def action_assign_speaker(self) -> None:
        seg_list = self.query_one(SegmentList)
        idx = seg_list.highlighted
        seg = seg_list.selected_segment()
        if idx is None or seg is None:
            return
        current = seg.get("speaker") or seg.get("speaker_label") or ""
        self.run_worker(self._assign_speaker(idx, current), exclusive=False)

    async def _assign_speaker(self, index: int, current: str) -> None:
        from ..widgets.prompt import PromptDialog
        new_speaker = await self.app.push_screen_wait(
            PromptDialog("Speaker for this segment:", initial=current)
        )
        if not new_speaker or new_speaker == current:
            return
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        app.push_busy()
        try:
            await app.api.assign_speaker(self.recording_id, [index], new_speaker)
            self.query_one(SegmentList).update_segment_speaker(index, new_speaker)
            self.query_one(StatusBar).flash(f"speaker set to {new_speaker}")
        except Exception as e:
            app.notify(f"assign failed: {e}", severity="error")
        finally:
            app.pop_busy()

    def action_rename_speaker(self) -> None:
        seg_list = self.query_one(SegmentList)
        seg = seg_list.selected_segment()
        current = (seg.get("speaker") or seg.get("speaker_label") or "") if seg else ""
        if not current:
            self.app.notify("select a segment with a speaker first")
            return
        self.run_worker(self._rename_speaker(current), exclusive=False)

    async def _rename_speaker(self, old_name: str) -> None:
        from ..widgets.prompt import PromptDialog
        new_name = await self.app.push_screen_wait(
            PromptDialog(f"Rename speaker “{old_name}” to:", initial=old_name)
        )
        if not new_name or new_name == old_name:
            return
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        app.push_busy()
        try:
            await app.api.rename_speaker(self.recording_id, old_name, new_name)
            self.query_one(SegmentList).rename_speaker_everywhere(old_name, new_name)
            self.query_one(StatusBar).flash(f"speaker renamed to {new_name}")
        except Exception as e:
            app.notify(f"rename failed: {e}", severity="error")
        finally:
            app.pop_busy()

    def _play_from(self, offset_s: float) -> None:
        if not self._tmp_audio or not self._tmp_audio.exists():
            self.query_one(StatusBar).flash("audio not loaded yet")
            return
        err = self.player.play(self._tmp_audio, offset_s=offset_s)
        status = self.query_one(StatusBar)
        if err:
            status.flash(err)
        else:
            status.flash(f"playing @ {self._fmt_dur(offset_s)}")

    @staticmethod
    def _fmt_dur(seconds: float) -> str:
        s = int(seconds or 0)
        h, rem = divmod(s, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h:d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
