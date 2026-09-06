<script lang="ts">
  import {
    transcriptState,
    fmtDur,
    setRecordingFolder,
    toggleRecordingTag as toggleCurrentRecTag,
  } from "$lib/transcript.svelte";
  import { visibleFolders, fetchFolders } from "$lib/folders.svelte";
  import { tagsState, fetchTags } from "$lib/tags.svelte";
  import { fetchLibrary } from "$lib/library.svelte";
  import { resetForNewTranscription } from "$lib/jobs.svelte";
  import { switchTab } from "$lib/session.svelte";
  import PlayerSection from "./PlayerSection.svelte";
  import SpeakerLegend from "./SpeakerLegend.svelte";

  const EXPORT_FORMATS: { fmt: string; label: string; title?: string }[] = [
    { fmt: "json", label: "JSON" },
    { fmt: "srt", label: "SRT" },
    { fmt: "vtt", label: "VTT", title: "WebVTT — subtitles for the web" },
    { fmt: "txt", label: "TXT" },
    { fmt: "md", label: "MD" },
    { fmt: "csv", label: "CSV", title: "One row per segment — for spreadsheets" },
  ];

  let wikilinks = $state(false);

  const result = $derived(transcriptState.result);
  const folderOptions = $derived(visibleFolders());
  const currentTags = $derived(new Set((transcriptState.currentRecording?.tags || []).map((t) => t.id)));

  function downloadBlob(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function onExport(fmt: string): Promise<void> {
    const recId = transcriptState.recordingId;
    if (!recId) return;
    const baseName = (transcriptState.currentRecording?.alias || transcriptState.result?.filename || "transcript").replace(/\.[^.]+$/, "");
    let url = `/api/recordings/${recId}/export/${fmt}`;
    if (fmt === "md" && wikilinks) url += "?wikilinks=true";
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(await res.text());
      downloadBlob(await res.blob(), `${baseName}.${fmt}`);
    } catch (err) {
      window.alert(`Export failed: ${(err as Error).message}`);
    }
  }

  async function onFolderChange(e: Event): Promise<void> {
    const folderId = (e.target as HTMLSelectElement).value;
    await setRecordingFolder(folderId);
    fetchFolders();
    fetchLibrary();
  }

  async function onTagToggle(tagId: string, checked: boolean): Promise<void> {
    const tag = tagsState.tags.find((t) => t.id === tagId);
    await toggleCurrentRecTag(tagId, checked, tag);
    fetchTags();
    fetchLibrary();
  }

  function onNewTranscription(): void {
    resetForNewTranscription();
    switchTab("transcribe");
  }
</script>

<div class="flex flex-col gap-4 p-4 text-xs">
  <div>
    <h2 class="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Actions</h2>
    <div class="mb-2 grid grid-cols-3 gap-1.5">
      {#each EXPORT_FORMATS as f (f.fmt)}
        <button
          onclick={() => onExport(f.fmt)}
          title={f.title}
          disabled={!transcriptState.recordingId}
          class="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-200 focus:outline-none focus:ring-1 focus:ring-brand disabled:opacity-40"
        >
          {f.label}
        </button>
      {/each}
    </div>
    <label class="mb-2 flex cursor-pointer items-center gap-1.5 text-slate-500" title="Write speaker names as [[wikilinks]] so each person gets a note in your vault. Markdown only.">
      <input type="checkbox" bind:checked={wikilinks} class="rounded border-slate-300 text-brand focus:ring-brand" />
      <span>Obsidian wikilinks in MD</span>
    </label>
    <button
      onclick={onNewTranscription}
      class="w-full rounded-md bg-brand px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-brand-hover focus:outline-none focus:ring-2 focus:ring-brand focus:ring-offset-1"
    >
      + New transcription
    </button>
  </div>

  <hr class="border-slate-100" />

  <div class="space-y-2">
    <h2 class="text-xs font-semibold uppercase tracking-wider text-slate-500">File info</h2>
    {#if result}
      <div class="space-y-1.5">
        <div class="flex justify-between"><span class="text-slate-500">Language</span><span class="font-medium text-slate-700">{result.language ? result.language.toUpperCase() : "?"}</span></div>
        <div class="flex justify-between"><span class="text-slate-500">Duration</span><span class="font-medium text-slate-700">{fmtDur(result.duration)}</span></div>
        <div class="flex justify-between"><span class="text-slate-500">Segments</span><span class="font-medium text-slate-700">{result.num_segments}</span></div>
        {#if result.speakers.length}
          <div class="flex justify-between"><span class="text-slate-500">Speakers</span><span class="font-medium text-slate-700">{result.speakers.length}</span></div>
        {/if}
      </div>
    {/if}

    {#if transcriptState.recordingId}
      <div class="pt-1">
        <label for="transcript-folder-select" class="mb-1 block text-slate-500">Folder</label>
        <select
          id="transcript-folder-select"
          value={transcriptState.currentRecording?.folder_id || ""}
          onchange={onFolderChange}
          class="w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
        >
          <option value="">— No folder —</option>
          {#each folderOptions as f (f.id)}<option value={f.id}>{f.name}</option>{/each}
        </select>
      </div>

      <div class="pt-1">
        <p class="mb-1 text-slate-500">Tags</p>
        {#if tagsState.tags.length}
          <div class="flex flex-wrap gap-1.5">
            {#each tagsState.tags as tag (tag.id)}
              {@const active = currentTags.has(tag.id)}
              <label
                class="flex cursor-pointer items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold transition {active ? 'text-white' : 'border-slate-200 text-slate-500'}"
                style={active ? `background:${tag.color_code}; border-color:${tag.color_code}` : ""}
              >
                <input type="checkbox" checked={active} onchange={(e) => onTagToggle(tag.id, (e.target as HTMLInputElement).checked)} class="hidden" />
                {tag.name}
              </label>
            {/each}
          </div>
        {:else}
          <p class="text-slate-400">No tags yet — create one in the Library sidebar</p>
        {/if}
      </div>
    {/if}
  </div>

  <SpeakerLegend />

  {#if transcriptState.audioUrl}
    <hr class="border-slate-100" />
    <PlayerSection />
  {/if}
</div>
