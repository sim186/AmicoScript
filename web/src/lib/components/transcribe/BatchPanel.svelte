<script lang="ts">
  import { jobState, removeBatchItem, clearPendingBatch, newBatch, loadResult } from "$lib/jobs.svelte";

  const statusColors: Record<string, string> = {
    pending: "bg-surface-alt text-ink-muted",
    uploading: "bg-blue-100 text-blue-600",
    queued: "bg-warning/15 text-amber-400",
    processing: "bg-brand-muted/20 text-brand-dark-bg",
    done: "bg-good/15 text-good",
    error: "bg-red-500/15 text-red-400",
  };

  const allTerminal = $derived(jobState.batchQueue.every((i) => i.status === "done" || i.status === "error"));
  const pendingCount = $derived(jobState.batchQueue.filter((i) => i.status === "pending").length);

  function fmtBytes(b: number): string {
    return b < 1048576 ? `${(b / 1024).toFixed(0)} KB` : `${(b / 1048576).toFixed(1)} MB`;
  }
</script>

{#if jobState.batchMode}
  {#if !allTerminal || jobState.batchQueue.some((i) => i.status === "pending")}
    <div id="batch-panel" class="mt-4 rounded-2xl border border-border bg-surface p-4">
      <div class="mb-2 flex items-center justify-between">
        <p id="batch-count-label" class="text-xs text-ink-muted">
          {jobState.batchQueue.length} file{jobState.batchQueue.length !== 1 ? "s" : ""} · {pendingCount} pending
        </p>
        {#if pendingCount > 0}
          <button id="batch-clear-all-btn" onclick={clearPendingBatch} class="text-xs text-ink-muted hover:text-red-400">
            Clear pending
          </button>
        {/if}
      </div>
      <ul id="batch-list" class="flex flex-col gap-2">
        {#each jobState.batchQueue as item, idx (item.file.name + item.file.size)}
          {@const pct = Math.round(item.progress * 100)}
          {@const uploadPct = item.status === "uploading" ? Math.round((item.progress / 0.05) * 100) : 0}
          <li class="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm">
            <span class="flex-1 truncate font-medium text-ink" title={item.file.name}>{item.file.name}</span>
            <span class="shrink-0 text-xs text-ink-muted">{fmtBytes(item.file.size)}</span>
            <span class="shrink-0 rounded px-1.5 py-0.5 text-xs font-medium {statusColors[item.status] || statusColors.pending}">
              {item.status === "processing"
                ? `${pct}%`
                : item.status === "uploading"
                  ? `↑ ${uploadPct}%`
                  : item.status.charAt(0).toUpperCase() + item.status.slice(1)}
            </span>
            {#if item.status === "done"}
              <button
                type="button"
                onclick={() => {
                  if (item.jobId) loadResult(item.jobId, item.recordingId);
                }}
                class="shrink-0 text-xs text-brand hover:underline focus:outline-none"
              >
                View
              </button>
            {/if}
            {#if item.status === "pending"}
              <button onclick={() => removeBatchItem(idx)} class="shrink-0 text-ink-muted transition hover:text-red-300 focus:outline-none" aria-label="Remove">
                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  {:else}
    <div id="batch-summary" class="mt-4 rounded-2xl border border-border bg-surface p-4">
      <div class="mb-2 flex items-center justify-between">
        <p class="text-xs font-medium text-ink-muted">Batch complete</p>
        <button id="batch-new-btn" onclick={newBatch} class="text-xs text-brand hover:underline">Start a new batch</button>
      </div>
      <ul id="batch-summary-list" class="flex flex-col gap-1.5 text-sm">
        {#each jobState.batchQueue as item}
          {@const ok = item.status === "done"}
          <li class="flex items-center gap-2">
            <span class={ok ? "text-good" : "text-red-400"}>{ok ? "✓" : "✗"}</span>
            <span class="flex-1 truncate text-ink">{item.file.name}</span>
            {#if ok}
              <button
                type="button"
                onclick={() => {
                  if (item.jobId) loadResult(item.jobId, item.recordingId);
                }}
                class="text-xs text-brand hover:underline focus:outline-none"
              >
                View transcript
              </button>
            {:else}
              <span class="text-xs text-red-500">{item.message || "Failed"}</span>
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  {/if}
{/if}
