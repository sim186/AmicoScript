<script lang="ts">
  import { transcriptState, type TranscriptSegment } from "$lib/transcript.svelte";
  import SegmentRow from "./SegmentRow.svelte";

  let {
    onRenameSpeaker,
    segments = transcriptState.filteredSegments,
    liveMode = false,
  }: {
    onRenameSpeaker: (name: string) => void;
    segments?: TranscriptSegment[];
    liveMode?: boolean;
  } = $props();

  const showActions = $derived(!liveMode && !!transcriptState.recordingId);
</script>

<div id="segments-list" role="list" aria-label="Transcript segments" class="space-y-1.5">
  {#each segments as segment (segment.id)}
    <SegmentRow {segment} query={transcriptState.searchQuery} {showActions} {onRenameSpeaker} />
  {/each}
</div>
