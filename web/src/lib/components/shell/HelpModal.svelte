<script lang="ts">
  import { modalState, closeHelpModal, tuiCommand, copyTuiCommand } from "$lib/modals.svelte";

  let copied = $state(false);

  async function copy() {
    copied = await copyTuiCommand();
    if (copied) setTimeout(() => (copied = false), 1500);
  }
</script>

{#if modalState.helpVisible}
  <div role="presentation" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50" onclick={closeHelpModal}>
    <div role="presentation" class="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl" onclick={(e) => e.stopPropagation()}>
      <div class="mb-3 flex items-center justify-between">
        <h2 class="text-sm font-semibold text-slate-800">Help &amp; terminal UI</h2>
        <button onclick={closeHelpModal} class="text-slate-400 hover:text-slate-600" aria-label="Close">
          <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
      <p class="mb-2 text-sm text-slate-600">AmicoScript also ships a terminal UI that talks to this same backend:</p>
      <div class="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
        <code class="flex-1 overflow-x-auto whitespace-nowrap text-xs text-slate-700">{tuiCommand()}</code>
        <button
          onclick={copy}
          class="shrink-0 rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 transition hover:border-brand hover:text-brand"
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
    </div>
  </div>
{/if}
