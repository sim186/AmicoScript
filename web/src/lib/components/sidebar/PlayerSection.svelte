<script lang="ts">
  import { onDestroy } from "svelte";
  import { transcriptState } from "$lib/transcript.svelte";
  import { playerState, createWaveform, destroyWaveform, togglePlayPause, setSpeed } from "$lib/waveform.svelte";

  let container: HTMLDivElement | undefined;

  $effect(() => {
    const url = transcriptState.audioUrl;
    if (url && container) createWaveform(container, url);
  });

  onDestroy(() => destroyWaveform());

  function fmtTime(s: number): string {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60)
      .toString()
      .padStart(2, "0");
    return `${m}:${sec}`;
  }
</script>

<div>
  <h2 class="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">Player</h2>
  <div bind:this={container} class="w-full rounded-lg border border-border bg-surface-alt" style="min-height:80px;" aria-label="Audio waveform"></div>
  {#if !playerState.ready}
    <p class="py-2 text-center text-xs text-ink-muted">{playerState.loadError ? "Waveform unavailable for this format." : "Generating waveform…"}</p>
  {/if}
  <div class="mt-2 flex items-center gap-3">
    <button
      onclick={togglePlayPause}
      disabled={!playerState.ready}
      aria-label={playerState.playing ? "Pause" : "Play"}
      class="flex h-8 w-8 items-center justify-center rounded-full bg-brand text-white transition hover:bg-brand-hover focus:outline-none focus:ring-2 focus:ring-brand focus:ring-offset-1 disabled:opacity-40"
    >
      {#if playerState.playing}
        <svg class="h-4 w-4" fill="currentColor" viewBox="0 0 24 24"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" /></svg>
      {:else}
        <svg class="h-4 w-4" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
      {/if}
    </button>
    <span class="text-xs tabular-nums text-ink-muted">{fmtTime(playerState.currentTime)} / {fmtTime(playerState.duration)}</span>
  </div>
  <div class="mt-2 flex items-center gap-2">
    <label for="speed-select" class="shrink-0 text-xs text-ink-muted">Speed</label>
    <select
      id="speed-select"
      value={playerState.speed}
      onchange={(e) => setSpeed(parseFloat((e.target as HTMLSelectElement).value))}
      class="rounded border border-border bg-surface px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
    >
      <option value="0.5">0.5×</option>
      <option value="0.75">0.75×</option>
      <option value="1">1×</option>
      <option value="1.25">1.25×</option>
      <option value="1.5">1.5×</option>
      <option value="2">2×</option>
    </select>
  </div>
</div>
