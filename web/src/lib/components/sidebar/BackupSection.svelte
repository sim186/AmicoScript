<script lang="ts">
  import { backupState, exportLibrary, importLibrary } from "$lib/backup.svelte";

  let includeAudio = $state(true);
  let overwrite = $state(false);
  let importInput: HTMLInputElement | undefined;

  function onImportChange() {
    const file = importInput?.files?.[0];
    if (importInput) importInput.value = "";
    importLibrary(file, overwrite);
  }
</script>

<div class="flex flex-col gap-3 text-xs">
  <label class="flex items-center gap-2 text-slate-600">
    <input id="backup-include-audio" type="checkbox" bind:checked={includeAudio} class="rounded border-slate-300 text-brand focus:ring-brand" />
    Include audio files
  </label>
  <button
    id="backup-export-btn"
    onclick={() => exportLibrary(includeAudio)}
    disabled={backupState.exporting}
    class="w-full rounded-md bg-brand px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-brand-hover focus:outline-none focus:ring-2 focus:ring-brand disabled:opacity-60"
  >
    {backupState.exporting ? "Preparing…" : "Export backup"}
  </button>

  <div class="border-t border-slate-100 pt-3">
    <label class="mb-2 flex items-center gap-2 text-slate-600">
      <input id="backup-overwrite" type="checkbox" bind:checked={overwrite} class="rounded border-slate-300 text-brand focus:ring-brand" />
      Overwrite existing recordings on import
    </label>
    <input bind:this={importInput} id="backup-import-input" type="file" accept=".zip" class="hidden" onchange={onImportChange} />
    <button
      id="backup-import-btn"
      onclick={() => importInput?.click()}
      disabled={backupState.importing}
      class="w-full rounded-md border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 transition hover:border-brand hover:text-brand disabled:opacity-60"
    >
      {backupState.importing ? "Importing…" : "Import backup"}
    </button>
  </div>

  {#if backupState.feedback}
    <p id="backup-feedback" class="{backupState.tone === 'error' ? 'text-red-600' : backupState.tone === 'ok' ? 'text-green-600' : 'text-slate-500'}">
      {backupState.feedback}
    </p>
  {/if}
</div>
