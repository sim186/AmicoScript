import WaveSurfer from "wavesurfer.js";
import { transcriptState, setCurrentSegment } from "./transcript.svelte";

export const playerState = $state({
  ready: false,
  playing: false,
  duration: 0,
  currentTime: 0,
  speed: 1,
  loadError: false,
});

let ws: WaveSurfer | null = null;

export function createWaveform(container: HTMLElement, audioUrl: string): void {
  destroyWaveform();
  playerState.ready = false;
  playerState.loadError = false;
  playerState.playing = false;
  playerState.currentTime = 0;
  playerState.duration = 0;

  ws = WaveSurfer.create({
    container,
    waveColor: "#c4b5fd",
    progressColor: "#6c63ff",
    height: 80,
    barWidth: 2,
    barGap: 1,
    barRadius: 2,
    cursorColor: "#6c63ff",
    cursorWidth: 2,
    normalize: true,
  });

  ws.on("ready", () => {
    playerState.ready = true;
    playerState.duration = ws?.getDuration() || 0;
    ws?.setPlaybackRate(playerState.speed);
  });
  ws.on("play", () => (playerState.playing = true));
  ws.on("pause", () => (playerState.playing = false));
  ws.on("finish", () => (playerState.playing = false));
  ws.on("timeupdate", (currentTime: number) => {
    playerState.currentTime = currentTime;
    const result = transcriptState.result;
    if (!result) return;
    const seg = result.segments.find((s) => currentTime >= s.start && currentTime < s.end);
    if (!seg || seg.id === transcriptState.currentSegmentId) return;
    setCurrentSegment(seg.id);
  });
  ws.on("error", () => {
    playerState.loadError = true;
  });

  ws.load(audioUrl);
}

export function destroyWaveform(): void {
  ws?.destroy();
  ws = null;
}

export function togglePlayPause(): void {
  ws?.playPause();
}

export function setSpeed(rate: number): void {
  playerState.speed = rate;
  ws?.setPlaybackRate(rate);
}

export function seekToSegment(start: number): void {
  if (!ws) return;
  const dur = ws.getDuration();
  if (dur > 0) {
    ws.seekTo(start / dur);
    ws.play();
  }
}
