<script lang="ts">
  import { transcriptState } from "$lib/transcript.svelte";
  import SegmentRow from "./SegmentRow.svelte";

  let { onRenameSpeaker }: { onRenameSpeaker: (name: string) => void } = $props();

  const showActions = $derived(!!transcriptState.recordingId);
</script>

<div id="segments-list" role="list" aria-label="Transcript segments" class="space-y-1.5">
  {#each transcriptState.filteredSegments as segment (segment.id)}
    <SegmentRow {segment} query={transcriptState.searchQuery} {showActions} {onRenameSpeaker} />
  {/each}
</div>
