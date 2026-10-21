<script lang="ts">
  import { benchmarkState, runBenchmark, shareBenchmarkUrl } from "$lib/llm.svelte";

  function onShare() {
    const url = shareBenchmarkUrl();
    if (url) window.open(url, "_blank", "noopener");
  }
</script>

<div class="flex flex-col gap-3 text-xs">
  <button
    id="benchmark-run-btn"
    onclick={runBenchmark}
    disabled={benchmarkState.running}
    class="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white transition hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-50"
  >
    {#if benchmarkState.running}
      <svg id="benchmark-spin-icon" class="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"></path>
      </svg>
    {/if}
    <span id="benchmark-run-label">{benchmarkState.running ? "Running… (1–3 min)" : "Run Benchmark"}</span>
  </button>

  {#if benchmarkState.running || (benchmarkState.status && !benchmarkState.data)}
    <p id="benchmark-status" class="text-ink-muted">{benchmarkState.status}</p>
  {/if}

  {#if benchmarkState.data}
    <div id="benchmark-results" class="flex flex-col gap-2">
      <div id="benchmark-sys-info" class="grid grid-cols-1 gap-0.5 text-ink-muted">
        <div><span class="font-medium text-ink-muted">CPU:</span> {benchmarkState.data.system.cpu || "—"} ({benchmarkState.data.system.cpu_cores || "?"} cores)</div>
        <div><span class="font-medium text-ink-muted">RAM:</span> {benchmarkState.data.system.ram_gb != null ? benchmarkState.data.system.ram_gb + " GB" : "—"}</div>
        <div><span class="font-medium text-ink-muted">GPU:</span> {benchmarkState.data.system.cuda ? benchmarkState.data.system.gpu || "GPU (CUDA)" : "CPU only"}</div>
        <div><span class="font-medium text-ink-muted">OS:</span> {benchmarkState.data.system.os || "—"} ({benchmarkState.data.system.arch || "—"})</div>
      </div>
      <table class="w-full text-left">
        <thead>
          <tr class="text-ink-muted">
            <th class="py-1 font-normal">Model</th>
            <th class="py-1 font-normal">Load</th>
            <th class="py-1 font-normal">Infer</th>
            <th class="py-1 font-normal">Elapsed</th>
            <th class="py-1 font-normal">RTF</th>
          </tr>
        </thead>
        <tbody id="benchmark-table-body">
          {#each benchmarkState.data.results as r}
            <tr>
              <td class="py-1 font-medium">{r.model}</td>
              {#if r.error}
                <td colspan="4" class="py-1 text-red-500">{r.error}</td>
              {:else}
                <td class="py-1 text-ink-muted">{r.load_time_s}s</td>
                <td class="py-1 text-ink-muted">{r.transcribe_time_s}s</td>
                <td class="py-1 text-ink-muted">{r.elapsed_s}s</td>
                <td class="py-1 {(r.rtf ?? 0) < 1 ? 'text-good' : 'text-amber-400'} font-medium">{r.rtf}x</td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
      {#if benchmarkState.data.total_elapsed_s != null}
        <p id="benchmark-total-elapsed" class="text-ink-muted">Total benchmark time: {benchmarkState.data.total_elapsed_s}s</p>
      {/if}
      <button
        id="benchmark-share-btn"
        onclick={onShare}
        class="w-full rounded-lg border border-border px-3 py-1.5 text-xs text-ink-muted transition hover:border-brand hover:text-brand"
      >
        Share results
      </button>
    </div>
  {/if}
</div>
