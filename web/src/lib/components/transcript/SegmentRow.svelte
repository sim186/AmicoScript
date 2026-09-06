<script lang="ts">
  import {
    transcriptState,
    toggleSegmentSelection,
    saveSegmentEdit,
    resetSegment,
    translateSegment,
    fmtDur,
    speakerTextColor,
    type TranscriptSegment,
  } from "$lib/transcript.svelte";
  import { seekToSegment } from "$lib/waveform.svelte";

  let {
    segment,
    query,
    showActions,
    onRenameSpeaker,
  }: {
    segment: TranscriptSegment;
    query: string;
    showActions: boolean;
    onRenameSpeaker: (name: string) => void;
  } = $props();

  let editing = $state(false);
  let draft = $state("");
  let translating = $state(false);
  let rowEl: HTMLDivElement | undefined;

  const isActive = $derived(transcriptState.currentSegmentId === segment.id);
  const isSelected = $derived(transcriptState.selectedSegmentIds.has(segment.id));

  $effect(() => {
    if (isActive && rowEl) rowEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  function escapeHtml(s: string): string {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function highlighted(text: string): string {
    if (!query) return escapeHtml(text);
    const eq = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return escapeHtml(text).replace(new RegExp(eq, "gi"), (m) => `<mark>${m}</mark>`);
  }

  function startEdit(): void {
    draft = segment.text;
    editing = true;
  }
  function cancelEdit(): void {
    editing = false;
  }
  async function commitEdit(): Promise<void> {
    await saveSegmentEdit(segment.id, draft);
    editing = false;
  }
  function onEditKeydown(e: KeyboardEvent): void {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      commitEdit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancelEdit();
    }
  }
  async function onTranslateClick(): Promise<void> {
    translating = true;
    await translateSegment(segment.id);
    translating = false;
  }
  function onRowClick(): void {
    if (!editing) seekToSegment(segment.start);
  }
</script>

<div
  bind:this={rowEl}
  role="listitem"
  aria-current={isActive ? "true" : undefined}
  onclick={onRowClick}
  class="group cursor-pointer rounded-r-xl border-l-[3px] bg-white py-3 pl-4 pr-5 transition {isActive
    ? 'border-brand bg-brand-muted'
    : segment.edited
      ? 'border-amber-400 hover:border-indigo-300 hover:bg-[#f8f7ff]'
      : 'border-slate-200 hover:border-indigo-300 hover:bg-[#f8f7ff]'}"
>
  <div class="mb-0.5 flex items-baseline gap-1">
    {#if showActions}
      <input
        type="checkbox"
        checked={isSelected}
        onclick={(e) => {
          e.stopPropagation();
          toggleSegmentSelection(segment.id, e.shiftKey);
        }}
        class="mr-1.5 shrink-0 cursor-pointer rounded border-slate-300 text-brand focus:ring-brand"
      />
    {/if}
    {#if segment.speaker}
      <button
        type="button"
        onclick={(e) => {
          e.stopPropagation();
          onRenameSpeaker(segment.speaker!);
        }}
        title="Rename speaker"
        class="mr-2 text-xs font-semibold hover:underline"
        style="color: {speakerTextColor(segment.speaker)}"
      >
        {segment.speaker}
      </button>
    {/if}
    <span class="font-mono text-xs text-slate-400">{fmtDur(segment.start)}</span>
    {#if showActions}
      <div role="presentation" class="ml-auto flex items-center gap-1" onclick={(e) => e.stopPropagation()}>
        {#if translating}
          <svg class="h-3.5 w-3.5 animate-spin text-brand" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            ></path>
          </svg>
        {:else if segment.edited}
          <button title="Reset to original text" onclick={() => resetSegment(segment.id)} class="p-1 text-slate-400 transition hover:text-brand">
            <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
              ><path
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              /></svg
            >
          </button>
        {:else}
          <button title="Translate segment" onclick={onTranslateClick} class="p-1 text-slate-400 transition hover:text-brand">
            <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
              ><path
                stroke-linecap="round"
                stroke-linejoin="round"
                d="M3 5h12M9 3v2m1.048 9.5A18.022 18.022 0 016.412 9m6.088 9h7M11 21l5-10 5 10M12.751 5C11.783 10.77 8.07 15.61 3 18.129"
              /></svg
            >
          </button>
        {/if}
        <button title="Edit segment text" onclick={startEdit} class="p-1 text-slate-400 transition hover:text-brand">
          <svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
            ><path
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"
            /></svg
          >
        </button>
      </div>
    {/if}
  </div>
  {#if editing}
    <textarea
      bind:value={draft}
      onkeydown={onEditKeydown}
      onclick={(e) => e.stopPropagation()}
      rows="3"
      class="mt-2 w-full rounded-md border border-brand px-2 py-1.5 text-sm focus:outline-none"
    ></textarea>
    <div role="presentation" class="mt-2 flex justify-end gap-2" onclick={(e) => e.stopPropagation()}>
      <button onclick={cancelEdit} class="rounded bg-slate-100 px-3 py-1 text-xs transition hover:bg-slate-200">Cancel</button>
      <button onclick={commitEdit} class="rounded bg-brand px-3 py-1 text-xs font-semibold text-white transition hover:bg-brand-hover">Save</button>
    </div>
  {:else}
    <p class="text-sm leading-relaxed text-slate-700">{@html highlighted(segment.text)}</p>
    {#if segment.translation}
      <p class="mt-1 text-sm italic leading-relaxed text-slate-500">{segment.translation}</p>
    {/if}
  {/if}
</div>
