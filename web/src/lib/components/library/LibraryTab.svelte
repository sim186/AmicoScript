<script lang="ts">
  import { onMount } from "svelte";
  import {
    libraryState,
    fetchLibrary,
    toggleSelectAll,
    bulkDelete,
    bulkMoveToFolder,
    bulkAssignTag,
    bulkExport,
    openRecording,
    showSearchResultsInLibrary,
  } from "$lib/library.svelte";
  import { visibleFolders } from "$lib/folders.svelte";
  import { tagsState, clearTagFilter } from "$lib/tags.svelte";
  import { paletteState } from "$lib/commandPalette.svelte";
  import RecordingCard from "./RecordingCard.svelte";

  const EXPORT_FORMATS: [string, string][] = [
    ["json", "JSON"],
    ["srt", "SRT"],
    ["vtt", "WebVTT"],
    ["csv", "CSV"],
    ["txt", "TXT"],
    ["md", "Markdown"],
  ];

  let bulkPopover = $state<"folder" | "tags" | "export" | null>(null);
  let wikilinks = $state(false);
  let selectAllEl: HTMLInputElement | undefined;

  const selectedCount = $derived(libraryState.selectedIds.size);
  const total = $derived(libraryState.recordings.length);
  const selectedTag = $derived(libraryState.selectedTagId ? tagsState.tags.find((t) => t.id === libraryState.selectedTagId) : null);
  const folderOptions = $derived(visibleFolders());
  const selectedRecs = $derived(libraryState.recordings.filter((r) => libraryState.selectedIds.has(r.id)));

  $effect(() => {
    if (selectAllEl) selectAllEl.indeterminate = selectedCount > 0 && selectedCount < total;
  });

  function tagBulkLabel(tagId: string): { cls: string; prefix: string } {
    const n = selectedRecs.filter((r) => r.tags.some((t) => t.id === tagId)).length;
    if (n === 0) return { cls: "text-slate-600", prefix: "" };
    if (n === selectedRecs.length) return { cls: "font-semibold text-brand", prefix: "✓ " };
    return { cls: "italic text-slate-500", prefix: "− " };
  }

  onMount(() => {
    fetchLibrary();
  });

  $effect(() => {
    const pendingId = paletteState.pendingRecordingId;
    const pendingQuery = paletteState.pendingSearchQuery;
    if (pendingId) {
      paletteState.pendingRecordingId = null;
      (async () => {
        let rec = libraryState.recordings.find((r) => r.id === pendingId);
        if (!rec) {
          const res = await fetch(`/api/recordings/${pendingId}`);
          rec = res.ok ? await res.json() : undefined;
        }
        if (rec) openRecording(rec);
      })();
    } else if (pendingQuery) {
      paletteState.pendingSearchQuery = null;
      showSearchResultsInLibrary(pendingQuery);
    }
  });
</script>

<div id="tab-library" class="flex h-full flex-1 flex-col overflow-hidden" role="tabpanel">
  <div class="flex shrink-0 items-center gap-3 border-b border-slate-100 bg-white px-3 py-2 md:px-6 md:py-3">
    <input
      bind:this={selectAllEl}
      type="checkbox"
      checked={total > 0 && selectedCount === total}
      onchange={(e) => toggleSelectAll((e.target as HTMLInputElement).checked)}
      title="Select all"
      class="shrink-0 cursor-pointer rounded border-slate-300 text-brand focus:ring-brand"
    />
    <span class="flex-1 text-sm font-semibold text-slate-700">{libraryState.headerLabel}</span>

    {#if selectedCount === 0}
      <div class="flex items-center gap-3">
        <div class="flex items-center gap-1.5 text-xs text-slate-500">
          <label for="lib-sort-select">Sort</label>
          <select
            id="lib-sort-select"
            value={libraryState.sort}
            onchange={(e) => {
              libraryState.sort = (e.target as HTMLSelectElement).value as typeof libraryState.sort;
              fetchLibrary();
            }}
            class="rounded border border-slate-200 bg-white px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
          >
            <option value="created_at">Date added</option>
            <option value="filename">Name</option>
            <option value="duration">Duration</option>
          </select>
          <button
            title="Toggle order"
            onclick={() => {
              libraryState.order = libraryState.order === "desc" ? "asc" : "desc";
              fetchLibrary();
            }}
            class="rounded p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 focus:outline-none"
          >
            {#if libraryState.order === "asc"}
              <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
                ><path stroke-linecap="round" stroke-linejoin="round" d="M3 4h13M3 8h9M3 12h5m10 4V4m0 0l-3 3m3-3l3 3" /></svg
              >
            {:else}
              <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
                ><path stroke-linecap="round" stroke-linejoin="round" d="M3 4h13M3 8h9M3 12h5m10-4V20m0 0l-3-3m3 3l3-3" /></svg
              >
            {/if}
          </button>
        </div>
        {#if selectedTag}
          <div class="flex items-center gap-1.5 text-xs">
            <span class="text-slate-400">Tag:</span>
            <span class="font-semibold text-slate-700">{selectedTag.name}</span>
            <button onclick={clearTagFilter} class="text-slate-300 transition hover:text-red-400">×</button>
          </div>
        {/if}
      </div>
    {:else}
      <div class="flex items-center gap-2">
        <span class="mr-1 text-xs text-slate-500">{selectedCount} selected</span>

        <div class="relative">
          <button
            onclick={() => (bulkPopover = bulkPopover === "folder" ? null : "folder")}
            title="Move selected to folder"
            class="flex items-center gap-1.5 rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-200 focus:outline-none"
          >
            <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"
              ><path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" /></svg
            >
            Move
          </button>
          {#if bulkPopover === "folder"}
            <div class="absolute right-0 top-full z-50 mt-1 min-w-[160px] rounded-lg border border-slate-200 bg-white p-2 shadow-lg">
              <p class="mb-1 px-1 text-xs text-slate-400">Move to folder</p>
              <select
                onchange={(e) => {
                  bulkMoveToFolder((e.target as HTMLSelectElement).value);
                  bulkPopover = null;
                }}
                class="w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
              >
                <option value="">— No folder —</option>
                {#each folderOptions as f (f.id)}<option value={f.id}>{f.name}</option>{/each}
              </select>
            </div>
          {/if}
        </div>

        <div class="relative">
          <button
            onclick={() => (bulkPopover = bulkPopover === "tags" ? null : "tags")}
            title="Assign tags to selected"
            class="flex items-center gap-1.5 rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-200 focus:outline-none"
          >
            <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"
              ><path
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a2 2 0 014-4z"
              /></svg
            >
            Tags
          </button>
          {#if bulkPopover === "tags"}
            <div class="absolute left-0 top-full z-50 mt-1 min-w-[180px] rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
              <p class="px-3 py-1 text-xs text-slate-400">Toggle tag on all selected</p>
              <div class="max-h-48 overflow-y-auto">
                {#if tagsState.tags.length}
                  {#each tagsState.tags as tag (tag.id)}
                    {@const s = tagBulkLabel(tag.id)}
                    <button onclick={() => bulkAssignTag(tag.id)} class="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-slate-50 {s.cls}">
                      <span class="h-2.5 w-2.5 shrink-0 rounded-full" style="background:{tag.color_code}"></span>
                      {s.prefix}{tag.name}
                    </button>
                  {/each}
                {:else}
                  <p class="px-3 py-2 text-xs text-slate-400">No tags yet — create one in the sidebar</p>
                {/if}
              </div>
            </div>
          {/if}
        </div>

        <div class="relative">
          <button
            onclick={() => (bulkPopover = bulkPopover === "export" ? null : "export")}
            title="Export selected"
            class="flex items-center gap-1.5 rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-200 focus:outline-none"
          >
            <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
              ><path stroke-linecap="round" stroke-linejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" /></svg
            >
            Export
          </button>
          {#if bulkPopover === "export"}
            <div class="absolute right-0 top-full z-50 mt-1 min-w-[160px] rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
              <p class="px-3 py-1 text-xs text-slate-400">Format</p>
              <label class="flex items-center gap-1.5 px-3 py-1.5 text-xs text-slate-500">
                <input type="checkbox" bind:checked={wikilinks} class="rounded border-slate-300 text-brand focus:ring-brand" />
                Wikilinks (Markdown)
              </label>
              {#each EXPORT_FORMATS as [fmt, label] (fmt)}
                <button
                  onclick={() => {
                    bulkExport(fmt, wikilinks);
                    bulkPopover = null;
                  }}
                  class="block w-full px-3 py-1.5 text-left text-xs text-slate-700 hover:bg-slate-50"
                >
                  {label}
                </button>
              {/each}
            </div>
          {/if}
        </div>

        <button
          onclick={() => {
            if (window.confirm(`Permanently delete ${selectedCount} recording${selectedCount > 1 ? "s" : ""} and their transcripts?`)) bulkDelete();
          }}
          class="flex items-center gap-1.5 rounded-md bg-red-50 px-2.5 py-1 text-xs font-medium text-red-600 transition hover:bg-red-100 focus:outline-none"
        >
          <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
            ><path
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
            /></svg
          >
          Delete
        </button>
      </div>
    {/if}
  </div>

  {#if libraryState.errorMessage}
    <div class="shrink-0 bg-red-50 px-3 py-2 text-xs text-red-600">{libraryState.errorMessage}</div>
  {/if}

  {#if bulkPopover}
    <div role="presentation" class="fixed inset-0 z-40" onclick={() => (bulkPopover = null)}></div>
  {/if}

  <div class="flex-1 overflow-y-auto px-6 py-5">
    {#if !libraryState.recordings.length}
      <div class="py-16 text-center text-slate-400">
        <svg class="mx-auto mb-3 h-12 w-12 text-slate-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
          <path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
        </svg>
        <p class="text-sm font-medium">No recordings yet</p>
        <p class="mt-1 text-xs">Transcribed files will appear here</p>
      </div>
    {:else}
      <div class="space-y-2.5">
        {#each libraryState.recordings as recording, index (recording.id)}
          <RecordingCard {recording} {index} />
        {/each}
      </div>
    {/if}
    <div aria-hidden="true" style="height: var(--console-h, 36px)"></div>
  </div>
</div>
