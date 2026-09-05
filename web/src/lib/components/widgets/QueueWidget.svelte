<script lang="ts">
  import { queueWidget, toggleQueuePanel, attachToJob, cancelQueuedJob, queueStatusLabel } from "$lib/queueWidget.svelte";
</script>

{#if queueWidget.jobs.length}
  <div id="queue-widget" class="flex flex-col items-end gap-2">
    {#if queueWidget.panelOpen}
      <div class="w-72 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl">
        <div class="max-h-80 divide-y divide-slate-100 overflow-y-auto">
          {#each queueWidget.jobs as job (job.id)}
            {@const pct = Math.round((job.progress || 0) * 100)}
            {@const isQueued = job.status === "queued"}
            {@const name = (job.filename || job.source_url || job.id || "").toString()}
            <div
              role="button"
              tabindex="0"
              class="flex cursor-pointer flex-col gap-1.5 px-3 py-2 hover:bg-slate-50"
              onclick={() => attachToJob(job.id)}
              onkeydown={(e) => (e.key === "Enter" || e.key === " ") && attachToJob(job.id)}
            >
              <div class="flex items-center gap-2">
                <span class="w-5 shrink-0 font-mono text-[10px] text-slate-400">#{(job.position || 0) + 1}</span>
                <span class="min-w-0 flex-1 truncate text-xs text-slate-700" title={name}>
                  {name.length > 40 ? name.slice(0, 37) + "…" : name || "(unnamed)"}
                </span>
                <button
                  onclick={(e) => {
                    e.stopPropagation();
                    cancelQueuedJob(job.id);
                  }}
                  title="Cancel"
                  class="shrink-0 text-slate-400 hover:text-red-500"
                >
                  <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              <div class="flex items-center gap-2">
                <div class="h-1 flex-1 overflow-hidden rounded-full bg-slate-200">
                  <div
                    class="h-full transition-all {isQueued ? 'bg-slate-300' : 'bg-brand'}"
                    style="width:{isQueued ? 100 : Math.max(pct, 2)}%"
                  ></div>
                </div>
                <span class="w-20 shrink-0 text-right text-[10px] text-slate-500">
                  {queueStatusLabel(job.status)}{isQueued ? "" : ` · ${pct}%`}
                </span>
              </div>
            </div>
          {/each}
        </div>
      </div>
    {/if}
    <button
      onclick={() => toggleQueuePanel()}
      class="flex items-center gap-2 rounded-full bg-brand px-3.5 py-2 text-sm font-medium text-white shadow-lg transition hover:bg-brand-hover focus:outline-none focus:ring-2 focus:ring-brand focus:ring-offset-2"
    >
      <span>{queueWidget.jobs.length}</span>
      <span>{queueWidget.jobs.length === 1 ? "job" : "jobs"}</span>
    </button>
  </div>
{/if}
