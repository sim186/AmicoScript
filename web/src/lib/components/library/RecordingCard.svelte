<script lang="ts">
  import {
    libraryState,
    toggleCardSelection,
    selectRange,
    openRecording,
    saveAlias,
    moveRecordingToFolder,
    toggleRecordingTag,
    deleteRecording,
    retryRecording,
    type Recording,
  } from "$lib/library.svelte";
  import { foldersState, visibleFolders } from "$lib/folders.svelte";
  import { tagsState } from "$lib/tags.svelte";
  import { fmtDur } from "$lib/transcript.svelte";

  let { recording, index }: { recording: Recording; index: number } = $props();

  const isSelected = $derived(libraryState.selectedIds.has(recording.id));
  const folderOptions = $derived(visibleFolders());
  const folderName = $derived(recording.folder_id ? foldersState.folders.find((f) => f.id === recording.folder_id)?.name : "");
  const canRetry = $derived(["error", "interrupted", "cancelled"].includes(recording.status));
  const assignedTagIds = $derived(new Set(recording.tags.map((t) => t.id)));

  let openPopover = $state<"rename" | "folder" | "tags" | null>(null);
  let aliasDraft = $state(recording.alias || "");

  const STATUS_COLORS: Record<string, string> = {
    done: "bg-good/15 text-good",
    error: "bg-red-500/15 text-red-400",
    pending: "bg-surface-alt text-ink-muted",
    queued: "bg-warning/15 text-amber-400",
    transcribing: "bg-blue-100 text-blue-700",
    diarizing: "bg-purple-100 text-purple-700",
    cancelled: "bg-surface-alt text-ink-muted",
    interrupted: "bg-orange-100 text-orange-700",
  };
  const SOURCE_BADGE: Record<string, { label: string; cls: string; title: string }> = {
    meeting: { label: "meeting", cls: "bg-red-500/10 text-red-400", title: "Captured automatically from a call" },
    url: { label: "link", cls: "bg-sky-50 text-sky-600", title: "Imported from a link" },
  };

  function onCardClick(e: MouseEvent): void {
    if (e.shiftKey && libraryState.lastSelectedIdx !== null) {
      selectRange(libraryState.lastSelectedIdx, index);
      libraryState.lastSelectedIdx = index;
      return;
    }
    if (e.ctrlKey || e.metaKey) {
      toggleCardSelection(recording.id, !isSelected);
      return;
    }
    openRecording(recording);
  }

  function onCheckbox(e: Event): void {
    toggleCardSelection(recording.id, (e.target as HTMLInputElement).checked);
  }

  function onDragStart(e: DragEvent): void {
    e.dataTransfer?.setData("text/plain", recording.id);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
  }

  async function saveAliasClick(): Promise<void> {
    await saveAlias(recording.id, aliasDraft);
    openPopover = null;
  }

  function onDelete(): void {
    if (window.confirm("Permanently delete this recording and its transcript?")) deleteRecording(recording.id);
  }
</script>

<div
  role="presentation"
  class="cursor-pointer rounded-xl border border-border bg-surface p-3.5 px-4 transition hover:border-brand hover:shadow-[0_2px_8px_0_rgba(108,99,255,0.08)] {isSelected
    ? 'ring-2 ring-brand ring-inset'
    : ''}"
  draggable="true"
  onclick={onCardClick}
  ondragstart={onDragStart}
>
  <div class="flex items-start gap-3">
    <input
      type="checkbox"
      checked={isSelected}
      onclick={(e) => e.stopPropagation()}
      onchange={onCheckbox}
      class="mt-0.5 shrink-0 cursor-pointer rounded border-border text-brand focus:ring-brand"
    />
    <div class="min-w-0 flex-1">
      <div class="flex min-w-0 items-center gap-1.5">
        <p class="truncate text-sm font-semibold text-ink" title={recording.filename}>{recording.alias || recording.filename}</p>
        {#if recording.source && SOURCE_BADGE[recording.source]}
          <span class="rounded px-1.5 py-0.5 text-[10px] font-medium {SOURCE_BADGE[recording.source].cls}" title={SOURCE_BADGE[recording.source].title}>
            {SOURCE_BADGE[recording.source].label}
          </span>
        {/if}
      </div>
      <p class="mt-0.5 text-xs text-ink-muted">
        {new Date(recording.created_at * 1000).toLocaleDateString()} · {recording.duration ? fmtDur(recording.duration) : "—"}{folderName
          ? ` · ${folderName}`
          : ""}{recording.alias ? ` · ${recording.filename}` : ""}
      </p>
      {#if recording.status_detail && canRetry}
        <p class="mt-0.5 text-[11px] leading-tight text-ink-muted">{recording.status_detail}</p>
      {/if}
      {#if recording.tags.length}
        <div class="mt-2 flex flex-wrap gap-1">
          {#each recording.tags as tag (tag.id)}
            <span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold text-white" style="background:{tag.color_code}">{tag.name}</span>
          {/each}
        </div>
      {/if}
    </div>
    <div role="presentation" class="flex shrink-0 items-center gap-1.5" onclick={(e) => e.stopPropagation()}>
      <!-- Rename -->
      <div class="relative">
        <button
          type="button"
          title="Rename"
          onclick={() => (openPopover = openPopover === "rename" ? null : "rename")}
          class="rounded-lg p-1.5 text-ink-muted transition hover:bg-brand/5 hover:text-brand focus:outline-none"
        >
          <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"
            ><path stroke-linecap="round" stroke-linejoin="round" d="M16.862 3.487a2.25 2.25 0 113.182 3.182L7.5 19.213l-4 1 1-4 12.362-12.726z" /></svg
          >
        </button>
        {#if openPopover === "rename"}
          <div class="absolute right-0 top-full z-50 mt-1 min-w-[220px] rounded-lg border border-border bg-surface p-3 shadow-lg">
            <p class="mb-2 text-xs text-ink-muted">Display name (alias)</p>
            <input
              type="text"
              bind:value={aliasDraft}
              placeholder={recording.filename}
              onkeydown={(e) => e.key === "Enter" && saveAliasClick()}
              class="w-full rounded border border-border px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
            />
            <div class="mt-2 flex justify-end gap-2">
              <button onclick={() => (openPopover = null)} class="text-xs text-ink-muted hover:text-ink">Cancel</button>
              <button onclick={saveAliasClick} class="text-xs font-medium text-brand hover:text-brand/80">Save</button>
            </div>
          </div>
        {/if}
      </div>
      <!-- Move to folder -->
      <div class="relative">
        <button
          type="button"
          title="Move to folder"
          onclick={() => (openPopover = openPopover === "folder" ? null : "folder")}
          class="rounded-lg p-1.5 text-ink-muted transition hover:bg-brand/5 hover:text-brand focus:outline-none"
        >
          <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"
            ><path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" /></svg
          >
        </button>
        {#if openPopover === "folder"}
          <div class="absolute right-0 top-full z-50 mt-1 min-w-[160px] rounded-lg border border-border bg-surface p-2 shadow-lg">
            <p class="mb-1 px-1 text-xs text-ink-muted">Move to folder</p>
            <select
              value={recording.folder_id || ""}
              onchange={(e) => {
                moveRecordingToFolder(recording.id, (e.target as HTMLSelectElement).value);
                openPopover = null;
              }}
              class="w-full rounded border border-border bg-surface px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
            >
              <option value="">— No folder —</option>
              {#each folderOptions as f (f.id)}<option value={f.id}>{f.name}</option>{/each}
            </select>
          </div>
        {/if}
      </div>
      <!-- Assign tags -->
      <div class="relative">
        <button
          type="button"
          title="Assign tags"
          onclick={() => (openPopover = openPopover === "tags" ? null : "tags")}
          class="rounded-lg p-1.5 text-ink-muted transition hover:bg-brand/5 hover:text-brand focus:outline-none"
        >
          <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"
            ><path
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a2 2 0 014-4z"
            /></svg
          >
        </button>
        {#if openPopover === "tags"}
          <div class="absolute right-0 top-full z-50 mt-1 min-w-[160px] rounded-lg border border-border bg-surface py-1 shadow-lg">
            <p class="px-3 py-1 text-xs text-ink-muted">Assign tags</p>
            {#if tagsState.tags.length}
              {#each tagsState.tags as tag (tag.id)}
                <label class="flex cursor-pointer items-center gap-2 rounded px-3 py-1.5 text-xs hover:bg-surface-alt">
                  <input
                    type="checkbox"
                    checked={assignedTagIds.has(tag.id)}
                    onchange={(e) => toggleRecordingTag(recording.id, tag.id, (e.target as HTMLInputElement).checked)}
                    class="rounded border-border text-brand focus:ring-brand"
                  />
                  <span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold text-white" style="background:{tag.color_code}">{tag.name}</span>
                </label>
              {/each}
            {:else}
              <p class="px-3 py-2 text-xs text-ink-muted">No tags yet — create one in the sidebar</p>
            {/if}
          </div>
        {/if}
      </div>

      <span class="rounded-full px-2 py-0.5 text-xs font-medium {STATUS_COLORS[recording.status] || STATUS_COLORS.pending}" title={recording.status_detail || undefined}>
        {recording.status}
      </span>
      {#if canRetry}
        <button
          type="button"
          title="Transcribe again"
          aria-label="Transcribe again"
          onclick={() => retryRecording(recording.id)}
          class="rounded-lg p-1.5 text-ink-muted transition hover:bg-brand/5 hover:text-brand focus:outline-none"
        >
          <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
            ><path
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            /></svg
          >
        </button>
      {/if}
      <button type="button" title="Delete recording" onclick={onDelete} class="rounded-lg p-1.5 text-ink-muted transition hover:text-red-300 focus:outline-none">
        <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
          ><path
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
          /></svg
        >
      </button>
    </div>
  </div>
</div>
{#if openPopover}
  <div role="presentation" class="fixed inset-0 z-40" onclick={() => (openPopover = null)}></div>
{/if}
