<script lang="ts">
  import { consoleState, combinedLogLines } from "$lib/consoleLog.svelte";
  import { refreshJobLogs } from "$lib/jobs.svelte";
  import { session, viewport } from "$lib/session.svelte";

  let preEl = $state<HTMLPreElement | undefined>();

  const lines = $derived(combinedLogLines());
  const text = $derived(lines.length ? lines.join("\n") : "No logs yet.");
  const leftOffset = $derived(!viewport.mobile && session.drawerOpen ? "18rem" : "0");

  $effect(() => {
    text;
    if (consoleState.open && preEl) preEl.scrollTop = preEl.scrollHeight;
  });

  async function copyLogs() {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* clipboard may be blocked by browser policy */
    }
  }
</script>

<div
  id="console-panel"
  class="fixed bottom-0 right-0 z-50 border-t border-slate-700 bg-slate-800 shadow-2xl"
  style="left: {leftOffset}; transition: left 220ms ease;"
>
  <div
    id="console-toggle"
    role="button"
    tabindex="0"
    aria-expanded={consoleState.open}
    aria-controls="console-body"
    onclick={() => (consoleState.open = !consoleState.open)}
    onkeydown={(e) => (e.key === "Enter" || e.key === " ") && (consoleState.open = !consoleState.open)}
    class="flex h-9 cursor-pointer select-none items-center gap-2 bg-slate-700 px-4 text-slate-50 transition focus:outline-none focus:ring-1 focus:ring-inset focus:ring-brand"
  >
    <svg class="h-3.5 w-3.5 shrink-0 text-slate-50" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
    </svg>
    <span class="text-xs font-semibold uppercase tracking-wider text-slate-50">Console</span>
    {#if lines.length}
      <span id="console-activity-dot" class="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-brand"></span>
    {/if}
    <div class="flex-1"></div>
    {#if consoleState.open}
      <div id="console-actions" class="flex items-center gap-1">
        <button
          id="refresh-log-btn"
          onclick={(e) => {
            e.stopPropagation();
            refreshJobLogs();
          }}
          class="rounded px-1.5 py-0.5 text-xs text-slate-400 transition hover:text-slate-200 focus:outline-none"
        >
          Refresh
        </button>
        <button
          id="copy-log-btn"
          onclick={(e) => {
            e.stopPropagation();
            copyLogs();
          }}
          class="rounded px-1.5 py-0.5 text-xs text-slate-400 transition hover:text-slate-200 focus:outline-none"
        >
          Copy
        </button>
      </div>
    {/if}
    <svg
      id="console-chevron"
      class="h-3.5 w-3.5 shrink-0 text-slate-500 transition-transform"
      style={consoleState.open ? "transform:rotate(180deg)" : ""}
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      viewBox="0 0 24 24"
    >
      <path stroke-linecap="round" stroke-linejoin="round" d="M5 15l7-7 7 7" />
    </svg>
  </div>
  {#if consoleState.open}
    <div id="console-body">
      <pre bind:this={preEl} id="proc-log" class="h-48 overflow-y-auto whitespace-pre-wrap bg-slate-900 px-4 py-3 font-mono text-[11px] leading-relaxed text-slate-300">{text}</pre>
    </div>
  {/if}
</div>
