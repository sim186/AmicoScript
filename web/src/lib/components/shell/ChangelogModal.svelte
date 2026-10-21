<script lang="ts">
  import { modalState, closeChangelogModal, changelogGithubUrl } from "$lib/modals.svelte";
  import ModalScrim from "./ModalScrim.svelte";
</script>

<ModalScrim open={modalState.changelogVisible} onClose={closeChangelogModal}>
  <div role="presentation" class="flex max-h-[80vh] w-full max-w-2xl flex-col rounded-2xl bg-surface p-6 shadow-xl" onclick={(e) => e.stopPropagation()}>
    <div class="mb-3 flex items-center justify-between">
      <h2 class="text-sm font-semibold text-ink">What's new</h2>
      <div class="flex items-center gap-3">
        <a href={changelogGithubUrl()} target="_blank" rel="noopener" class="text-xs text-brand hover:underline">
          View on GitHub ↗
        </a>
        <button onclick={closeChangelogModal} class="text-ink-muted hover:text-ink" aria-label="Close">
          <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
    <pre class="flex-1 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-ink-muted">{modalState.changelogContent}</pre>
  </div>
</ModalScrim>
