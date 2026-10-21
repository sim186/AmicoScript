<script lang="ts">
  import { watcherState, meetingCaptureState, toggleMeetingCapture, autoSummaryState, toggleAutoSummary } from "$lib/watcher.svelte";
  import Toggle from "$lib/components/Toggle.svelte";

  const statusLine = $derived(
    watcherState.alive && watcherState.unsupported
      ? `● Helper running — ${watcherState.unsupported}`
      : watcherState.alive
        ? watcherState.recording
          ? "● Helper running — recording"
          : "● Helper running"
        : watcherState.canInstall
          ? "● Helper not running"
          : "● Helper not running — no setup available for this system",
  );
  const statusClass = $derived(
    watcherState.alive && watcherState.unsupported
      ? "text-amber-400"
      : watcherState.alive
        ? "text-good"
        : watcherState.canInstall
          ? "text-amber-400"
          : "text-ink-muted",
  );
</script>

<div class="flex flex-col gap-3">
  <div class="flex items-center gap-2">
    <Toggle
      id="meeting-capture-toggle"
      checked={meetingCaptureState.enabled}
      onchange={toggleMeetingCapture}
      label="Enable meeting auto-capture"
    />
    <span class="text-sm text-ink">Meeting auto-capture</span>
  </div>

  <p id="watcher-state-line" class="text-[10px] leading-tight {statusClass}">{statusLine}</p>

  {#if watcherState.installer && !watcherState.alive}
    <a
      id="watcher-setup-link"
      href={watcherState.installer.url}
      download={watcherState.installer.name}
      title={watcherState.installer.hint}
      class="text-[10px] font-semibold text-brand hover:underline"
    >
      Set it up ({watcherState.installer.name})
    </a>
  {/if}

  <div class="flex items-center gap-2 border-t border-border pt-3">
    <Toggle id="auto-summary-toggle" checked={autoSummaryState.enabled} onchange={toggleAutoSummary} label="Auto-summarize meetings" />
    <span class="text-sm text-ink">Auto-summarize meetings</span>
  </div>
  {#if !autoSummaryState.llmConfigured}
    <p id="auto-summary-hint" class="text-[10px] leading-tight text-ink-muted">Configure an LLM below to enable this.</p>
  {/if}
</div>
