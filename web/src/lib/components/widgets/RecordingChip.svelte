<script lang="ts">
  import { watcherState, recordingElapsedLabel } from "$lib/watcher.svelte";

  let now = $state(Date.now());
  let timer: ReturnType<typeof setInterval> | undefined;

  $effect(() => {
    if (watcherState.recording) {
      timer = setInterval(() => (now = Date.now()), 1000);
      return () => clearInterval(timer);
    }
  });
</script>

{#if watcherState.recording}
  <div id="recording-chip" role="status" aria-live="polite" class="flex items-center gap-2 rounded-full bg-red-600 px-3.5 py-2 text-sm font-medium text-white shadow-lg">
    <span class="relative flex h-2.5 w-2.5">
      <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-300 opacity-75"></span>
      <span class="relative inline-flex h-2.5 w-2.5 rounded-full bg-surface"></span>
    </span>
    <span>Recording</span>
    <span id="recording-chip-timer" class="font-mono text-xs opacity-90">{recordingElapsedLabel(now)}</span>
    <span id="recording-chip-app" class="text-xs opacity-80">{watcherState.app ? `· ${watcherState.app}` : ""}</span>
  </div>
{/if}
