<script lang="ts">
  import { jobState } from "$lib/jobs.svelte";
  import {
    transcriptState,
    setSearchQuery,
    clearSegmentSelection,
    assignSpeakerToSelected,
    translateAll,
    selectAllVisible,
    renameSpeaker,
    fmtDur,
  } from "$lib/transcript.svelte";
  import SegmentList from "./SegmentList.svelte";

  let assignInput = $state("");

  const selectionCount = $derived(transcriptState.selectedSegmentIds.size);
  const bulkMode = $derived(selectionCount > 0);
  const showActions = $derived(!!transcriptState.recordingId);
  const showTranslateAll = $derived(!!transcriptState.recordingId && (transcriptState.result?.language || "en") !== "en");
  const allVisibleSelected = $derived(
    transcriptState.filteredSegments.length > 0 && transcriptState.filteredSegments.every((s) => transcriptState.selectedSegmentIds.has(s.id)),
  );

  function onRenameSpeaker(oldName: string): void {
    const next = window.prompt(`Rename speaker "${oldName}" to:`, oldName);
    if (next && next !== oldName) renameSpeaker(oldName, next);
  }

  async function onAssign(): Promise<void> {
    await assignSpeakerToSelected(assignInput);
    assignInput = "";
  }
</script>

<div id="tab-transcript" class="flex h-full flex-1 flex-col overflow-hidden" role="tabpanel">
  <div class="flex shrink-0 items-center gap-2 border-b border-slate-100 bg-white px-3 py-2">
    {#if showActions}
      <input
        type="checkbox"
        checked={allVisibleSelected}
        onchange={(e) => selectAllVisible((e.target as HTMLInputElement).checked)}
        title="Select all visible"
        class="shrink-0 cursor-pointer rounded border-slate-300 text-brand focus:ring-brand"
      />
    {/if}
    {#if !bulkMode}
      <div class="relative flex-1">
        <svg class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M17 11A6 6 0 115 11a6 6 0 0112 0z" />
        </svg>
        <input
          type="search"
          value={transcriptState.searchQuery}
          oninput={(e) => setSearchQuery((e.target as HTMLInputElement).value)}
          placeholder="Search transcript…"
          aria-label="Search transcript"
          class="w-full rounded-lg border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-16 text-sm transition focus:bg-white focus:outline-none focus:ring-1 focus:ring-brand"
        />
        {#if transcriptState.searchQuery || transcriptState.activeSpeakerFilter}
          <span class="absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-slate-400">{transcriptState.filteredSegments.length} shown</span>
        {/if}
      </div>
    {:else}
      <div class="flex flex-1 items-center gap-2">
        <span class="shrink-0 text-xs text-slate-500">{selectionCount} selected</span>
        <input
          type="text"
          bind:value={assignInput}
          placeholder="Speaker name…"
          list="speaker-suggestions"
          class="min-w-0 flex-1 rounded border border-slate-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
        />
        <datalist id="speaker-suggestions">
          {#each transcriptState.result?.speakers || [] as s}<option value={s}></option>{/each}
        </datalist>
        <button onclick={onAssign} class="shrink-0 rounded bg-brand px-2.5 py-0.5 text-xs text-white transition hover:bg-brand-hover">Assign</button>
        <button onclick={clearSegmentSelection} class="shrink-0 rounded bg-slate-100 px-2.5 py-0.5 text-xs text-slate-600 transition hover:bg-slate-200">Clear</button>
      </div>
    {/if}
    {#if showTranslateAll}
      <button
        onclick={translateAll}
        title="Translate all segments directly to English"
        class="flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 transition hover:border-brand hover:text-brand"
      >
        {#if transcriptState.translateAllJobId}
          <svg class="h-4 w-4 animate-spin text-brand" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            ></path>
          </svg>
          Stop ({Math.round(transcriptState.translateAllProgress * 100)}%)
        {:else}
          <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M3 5h12M9 3v2m1.048 9.5A18.022 18.022 0 016.412 9m6.088 9h7M11 21l5-10 5 10M12.751 5C11.783 10.77 8.07 15.61 3 18.129"
            />
          </svg>
          Translate all
        {/if}
      </button>
    {/if}
  </div>

  {#if transcriptState.errorMessage}
    <div class="shrink-0 bg-red-50 px-3 py-2 text-xs text-red-600">{transcriptState.errorMessage}</div>
  {/if}

  <div class="flex-1 overflow-y-auto px-3 py-3 md:px-6 md:py-4">
    {#if transcriptState.result}
      <SegmentList {onRenameSpeaker} />
    {:else if jobState.processing && jobState.liveSegments.length}
      <div class="space-y-1.5">
        {#each jobState.liveSegments as seg (seg.id)}
          <div class="rounded-r-xl border-l-[3px] border-slate-200 bg-white py-3 pl-4 pr-5 opacity-80">
            <span class="font-mono text-xs text-slate-400">{fmtDur(seg.start)}</span>
            <p class="text-sm leading-relaxed text-slate-700">{seg.text}</p>
          </div>
        {/each}
      </div>
    {:else}
      <p class="py-8 text-center text-sm text-slate-400">No transcript loaded.</p>
    {/if}
    <div aria-hidden="true" style="height: var(--console-h, 36px)"></div>
  </div>
</div>
