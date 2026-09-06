<script lang="ts">
  import { entityDialogState, closeEntityDialog, saveEntityDialog, parentFolderName } from "$lib/entityDialog.svelte";
  import { PALETTE } from "$lib/palette";

  function onKeydown(e: KeyboardEvent): void {
    if (e.key === "Escape") {
      e.preventDefault();
      closeEntityDialog();
    }
  }
</script>

<svelte:window onkeydown={entityDialogState.open ? onKeydown : undefined} />

{#if entityDialogState.open}
  <div role="presentation" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4 py-8" onclick={closeEntityDialog}>
    <div role="presentation" class="relative w-full max-w-md rounded-xl bg-white p-5 shadow-lg" onclick={(e) => e.stopPropagation()}>
      <div class="mb-3 flex items-center justify-between">
        <h3 class="text-sm font-semibold text-slate-800">
          {entityDialogState.id
            ? entityDialogState.type === "folder"
              ? "Folder Settings"
              : "Tag Settings"
            : entityDialogState.type === "folder"
              ? "New Folder"
              : "New Tag"}
        </h3>
        <button onclick={closeEntityDialog} class="rounded p-1 text-slate-400 hover:text-slate-600" aria-label="Close">
          <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
        </button>
      </div>
      {#if entityDialogState.type === "folder" && !entityDialogState.id}
        <p class="mb-2 text-xs text-slate-400">Parent: {parentFolderName()}</p>
      {/if}
      <label for="entity-edit-name" class="mb-1 block text-xs font-medium text-slate-500">
        {entityDialogState.type === "folder" ? "Folder Name" : "Tag Name"}
      </label>
      <input
        id="entity-edit-name"
        type="text"
        bind:value={entityDialogState.name}
        onkeydown={(e) => e.key === "Enter" && saveEntityDialog()}
        class="mb-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-brand"
      />
      <p class="mb-1 text-xs font-medium text-slate-500">Color</p>
      <div class="mb-3 grid grid-cols-6 gap-2">
        {#each PALETTE as color (color)}
          <button
            type="button"
            onclick={() => (entityDialogState.color = color)}
            class="h-7 w-7 rounded-full transition {entityDialogState.color === color ? 'ring-2 ring-slate-400 ring-offset-1' : ''}"
            style="background:{color}"
            aria-label={`Color ${color}`}
          ></button>
        {/each}
      </div>
      {#if entityDialogState.error}
        <p class="mb-2 text-xs text-red-500">{entityDialogState.error}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button onclick={closeEntityDialog} class="rounded-lg px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-100">Cancel</button>
        <button
          onclick={saveEntityDialog}
          disabled={entityDialogState.saving}
          class="rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-hover disabled:opacity-50"
        >
          {entityDialogState.id ? "Save Changes" : "Create"}
        </button>
      </div>
    </div>
  </div>
{/if}
