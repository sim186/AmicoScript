<script lang="ts">
  import { entityDialogState, closeEntityDialog, saveEntityDialog, parentFolderName } from "$lib/entityDialog.svelte";
  import { PALETTE } from "$lib/palette";
  import ModalScrim from "../shell/ModalScrim.svelte";
</script>

<ModalScrim open={entityDialogState.open} onClose={closeEntityDialog} class="px-4 py-8">
    <div role="presentation" class="relative w-full max-w-md rounded-xl bg-surface p-5 shadow-lg" onclick={(e) => e.stopPropagation()}>
      <div class="mb-3 flex items-center justify-between">
        <h3 class="text-sm font-semibold text-ink">
          {entityDialogState.id
            ? entityDialogState.type === "folder"
              ? "Folder Settings"
              : "Tag Settings"
            : entityDialogState.type === "folder"
              ? "New Folder"
              : "New Tag"}
        </h3>
        <button onclick={closeEntityDialog} class="rounded p-1 text-ink-muted hover:text-ink" aria-label="Close">
          <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
        </button>
      </div>
      {#if entityDialogState.type === "folder" && !entityDialogState.id}
        <p class="mb-2 text-xs text-ink-muted">Parent: {parentFolderName()}</p>
      {/if}
      <label for="entity-edit-name" class="mb-1 block text-xs font-medium text-ink-muted">
        {entityDialogState.type === "folder" ? "Folder Name" : "Tag Name"}
      </label>
      <input
        id="entity-edit-name"
        type="text"
        bind:value={entityDialogState.name}
        onkeydown={(e) => e.key === "Enter" && saveEntityDialog()}
        class="mb-3 w-full rounded-lg border border-border px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-brand"
      />
      <p class="mb-1 text-xs font-medium text-ink-muted">Color</p>
      <div class="mb-3 grid grid-cols-6 gap-2">
        {#each PALETTE as color (color)}
          <button
            type="button"
            onclick={() => (entityDialogState.color = color)}
            class="h-7 w-7 rounded-full transition {entityDialogState.color === color ? 'ring-2 ring-border ring-offset-1' : ''}"
            style="background:{color}"
            aria-label={`Color ${color}`}
          ></button>
        {/each}
      </div>
      {#if entityDialogState.error}
        <p class="mb-2 text-xs text-red-500">{entityDialogState.error}</p>
      {/if}
      <div class="flex justify-end gap-2">
        <button onclick={closeEntityDialog} class="rounded-lg px-3 py-1.5 text-xs text-ink-muted hover:bg-surface-alt">Cancel</button>
        <button
          onclick={saveEntityDialog}
          disabled={entityDialogState.saving}
          class="rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-hover disabled:opacity-50"
        >
          {entityDialogState.id ? "Save Changes" : "Create"}
        </button>
      </div>
    </div>
</ModalScrim>
