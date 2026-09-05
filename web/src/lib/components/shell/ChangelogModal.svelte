<script lang="ts">
  import { modalState, closeChangelogModal, changelogGithubUrl } from "$lib/modals.svelte";
</script>

{#if modalState.changelogVisible}
  <div role="presentation" class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50" onclick={closeChangelogModal}>
    <div role="presentation" class="flex max-h-[80vh] w-full max-w-2xl flex-col rounded-2xl bg-white p-6 shadow-xl" onclick={(e) => e.stopPropagation()}>
      <div class="mb-3 flex items-center justify-between">
        <h2 class="text-sm font-semibold text-slate-800">What's new</h2>
        <div class="flex items-center gap-3">
          <a href={changelogGithubUrl()} target="_blank" rel="noopener" class="text-xs text-brand hover:underline">
            View on GitHub ↗
          </a>
          <button onclick={closeChangelogModal} class="text-slate-400 hover:text-slate-600" aria-label="Close">
            <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>
      <pre class="flex-1 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-600">{modalState.changelogContent}</pre>
    </div>
  </div>
{/if}
