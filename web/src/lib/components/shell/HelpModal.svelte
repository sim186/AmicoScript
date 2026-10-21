<script lang="ts">
  import { modalState, closeHelpModal, tuiCommand, copyTuiCommand } from "$lib/modals.svelte";
  import ModalScrim from "./ModalScrim.svelte";

  let copied = $state(false);

  async function copy() {
    copied = await copyTuiCommand();
    if (copied) setTimeout(() => (copied = false), 1500);
  }
</script>

<ModalScrim open={modalState.helpVisible} onClose={closeHelpModal}>
  <div role="presentation" class="w-full max-w-lg rounded-2xl bg-surface p-6 shadow-xl" onclick={(e) => e.stopPropagation()}>
    <div class="mb-3 flex items-center justify-between">
      <h2 class="text-sm font-semibold text-ink">Help &amp; terminal UI</h2>
      <button onclick={closeHelpModal} class="text-ink-muted hover:text-ink" aria-label="Close">
        <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
    <p class="mb-2 text-sm text-ink-muted">AmicoScript also ships a terminal UI that talks to this same backend:</p>
    <div class="flex items-center gap-2 rounded-lg border border-border bg-surface-alt px-3 py-2">
      <code class="flex-1 overflow-x-auto whitespace-nowrap text-xs text-ink">{tuiCommand()}</code>
      <button
        onclick={copy}
        class="shrink-0 rounded-md border border-border px-2 py-1 text-xs text-ink-muted transition hover:border-brand hover:text-brand"
      >
        {copied ? "Copied!" : "Copy"}
      </button>
    </div>
  </div>
</ModalScrim>
