<script lang="ts">
  import { jobState, startTranscription, hideError } from "$lib/jobs.svelte";
  import DropZone from "./DropZone.svelte";
  import BatchPanel from "./BatchPanel.svelte";
  import ActiveJobBar from "./ActiveJobBar.svelte";

  const startLabel = $derived(
    jobState.batchMode
      ? `Transcribe ${jobState.batchQueue.length} file${jobState.batchQueue.length !== 1 ? "s" : ""}`
      : "Start transcription",
  );
  const startDisabled = $derived(
    jobState.batchMode
      ? jobState.batchQueue.filter((i) => i.status === "pending").length === 0
      : !jobState.selectedFile,
  );
</script>

<div id="tab-transcribe" class="flex flex-1 flex-col overflow-y-auto px-4 py-4 md:px-8 md:py-8" role="tabpanel">
  <ActiveJobBar />

  {#if jobState.errorMessage}
    <div id="error-msg" class="mb-4 flex items-center justify-between rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
      <span>{jobState.errorMessage}</span>
      <button onclick={hideError} class="text-red-400 hover:text-red-600" aria-label="Dismiss">✕</button>
    </div>
  {/if}

  <DropZone />

  {#if !jobState.processing || jobState.batchMode}
    <button
      id="start-btn"
      disabled={startDisabled}
      onclick={() => startTranscription()}
      class="mt-3 w-full rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white transition hover:bg-brand-hover focus:outline-none focus:ring-2 focus:ring-brand disabled:cursor-not-allowed disabled:opacity-50"
    >
      {startLabel}
    </button>
  {/if}

  <BatchPanel />
</div>
