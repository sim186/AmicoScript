"""Transcript screen: waveform + segments + playback."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Header, OptionList, Static

from ..clipboard import copy_to_clipboard
from ..playback import Player
from ..waveform import compute_levels_async
from ..widgets.segment_list import SegmentList
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
    ]

    DEFAULT_CSS = """
    TranscriptScreen { layout: vertical; }
    #title { padding: 0 1; height: 1; color: $accent; }
    """

    def __init__(self, recording_id: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.recording_id = recording_id
        self._tmp_audio: Path | None = None
        self.player = Player()
        self.duration_s: float = 0.0
        self._anim_timer = None

    def compose(self):
        yield Header(show_clock=False)
        with Vertical():
            yield Static("loading...", id="title")
            yield WaveformView(id="wave")
            yield SegmentList(id="segments")
            yield StatusBar(id="statusbar")

    def on_mount(self) -> None:
        self.run_worker(self._load(), exclusive=True)
        # Animate at ~15 fps.
        self._anim_timer = self.set_interval(1 / 15, self._tick, pause=False)

    async def _load(self) -> None:
        """Fast path: title + transcript visible immediately. Waveform separate worker."""
        app: "AmicoTUI" = self.app  # type: ignore[assignment]
        title = self.query_one("#title", Static)
        seg_list = self.query_one(SegmentList)
        status = self.query_one(StatusBar)

        try:
            rec = await app.api.recording(self.recording_id)
        except Exception as e:
            title.update(f"error: {e}")
            return
        name = rec.get("alias") or rec.get("filename") or self.recording_id
        self.duration_s = float(rec.get("duration") or 0.0)
        title.update(
            f"[b]{name}[/b]  ·  {self._fmt_dur(self.duration_s)}  ·  "
            f"status: {rec.get('status', '?')}"
        )

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

        # Waveform + audio: separate non-blocking worker so the screen is usable now.
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

    # --- animation --------------------------------------------------

    def _tick(self) -> None:
        wave = self.query_one(WaveformView)
        if self.player.is_playing() and self.duration_s > 0:
            pos = self.player.position()
            wave.position = max(0.0, min(1.0, pos / self.duration_s))
        # else leave position as-is (last cursor stays)

    # --- segment Enter → play ---------------------------------------

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        seg = self.query_one(SegmentList).selected_segment()
        if not seg:
            return
        self._play_from(float(seg.get("start", 0.0)))

    # --- actions ----------------------------------------------------

    def action_pop(self) -> None:
        self.app.pop_screen()

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

    # --- helpers ----------------------------------------------------

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
