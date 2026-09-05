<script lang="ts">
  import { jobState } from "$lib/jobs.svelte";
</script>

<div id="tab-transcript" class="flex flex-1 flex-col items-center justify-center gap-2 px-4 py-8 text-center text-slate-400" role="tabpanel">
  <p class="text-sm">Transcript view lands in the next ticket.</p>
  {#if jobState.recordingId}
    <p class="font-mono text-xs text-slate-500">Recording {jobState.recordingId}</p>
  {/if}
</div>
