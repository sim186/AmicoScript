<script lang="ts">
  import { jobState, cancelJob, loadResult } from "$lib/jobs.svelte";

  const pct = $derived(Math.max(0, Math.min(100, Math.round(jobState.jobProgress * 100))));
</script>

{#if jobState.processing && !jobState.batchMode}
  <div id="active-job-bar" class="mb-4 flex items-center gap-3 rounded-xl border border-brand/30 bg-brand-muted/10 px-4 py-3" aria-live="polite">
    <svg class="h-4 w-4 shrink-0 animate-spin text-brand" fill="none" viewBox="0 0 24 24">
      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"></path>
    </svg>
    <div class="min-w-0 flex-1">
      <p id="proc-filename" class="truncate text-sm font-medium text-ink">{jobState.filename}</p>
      <div id="progress-bar-wrap" role="progressbar" aria-valuenow={pct} aria-valuemin="0" aria-valuemax="100" class="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-border">
        <div id="progress-bar" class="h-1.5 rounded-full bg-brand transition-all duration-300" style="width:{pct}%"></div>
      </div>
      <p id="proc-message" class="mt-1 text-xs text-ink-muted">{jobState.jobMessage || "Processing…"} <span id="proc-percent">{pct}%</span></p>
    </div>
    <button
      id="cancel-btn"
      onclick={cancelJob}
      class="shrink-0 rounded-md border border-border px-2.5 py-1.5 text-xs text-ink-muted transition hover:border-red-400 hover:text-red-400"
    >
      Cancel
    </button>
    {#if jobState.jobStatus && jobState.jobId}
      <button
        onclick={() => jobState.jobId && loadResult(jobState.jobId, jobState.recordingId)}
        class="shrink-0 text-xs text-brand hover:underline focus:outline-none"
      >
        View
      </button>
    {/if}
  </div>
{/if}
