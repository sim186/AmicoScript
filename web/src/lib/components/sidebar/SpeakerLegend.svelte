<script lang="ts">
  import { transcriptState, toggleSpeakerFilter, renameSpeaker, speakerColor } from "$lib/transcript.svelte";

  function onRename(name: string): void {
    const next = window.prompt(`Rename speaker "${name}" to:`, name);
    if (next && next !== name) renameSpeaker(name, next);
  }
</script>

{#if transcriptState.result?.speakers.length}
  <div>
    <h2 class="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Speakers</h2>
    <div role="list" class="space-y-1.5">
      {#each transcriptState.result.speakers as speaker (speaker)}
        <div role="listitem" class="group flex select-none items-center gap-2 rounded-lg px-2 py-1.5 transition hover:bg-slate-50">
          <button
            type="button"
            onclick={() => toggleSpeakerFilter(speaker)}
            class="h-2.5 w-2.5 shrink-0 rounded-full"
            style="background:{speakerColor(speaker)}"
            aria-label={`Filter by ${speaker}`}
          ></button>
          <button type="button" onclick={() => toggleSpeakerFilter(speaker)} class="flex-1 truncate text-left text-sm text-slate-600 transition group-hover:text-slate-900">
            {speaker}
          </button>
          <button type="button" onclick={() => onRename(speaker)} title="Rename speaker" class="p-1 text-slate-400 opacity-40 transition hover:text-brand group-hover:opacity-100">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
            </svg>
          </button>
          {#if transcriptState.activeSpeakerFilter === speaker}
            <span class="h-1.5 w-1.5 shrink-0 rounded-full bg-brand"></span>
          {/if}
        </div>
      {/each}
    </div>
  </div>
{/if}
