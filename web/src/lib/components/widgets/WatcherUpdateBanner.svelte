<script lang="ts">
  import { watcherState, dismissWatcherUpdateBanner, updateWatcherNow } from "$lib/watcher.svelte";

  let updating = $state(false);
  let errorMsg = $state("");

  async function onUpdate() {
    updating = true;
    errorMsg = "";
    const result = await updateWatcherNow();
    if (!result.ok) errorMsg = result.error || "Update failed.";
    updating = false;
  }
</script>

{#if watcherState.updateBannerVisible}
  <div class="flex items-center justify-between gap-3 border-b border-amber-200 bg-amber-50 px-4 py-3 text-amber-800">
    <p id="watcher-update-text" class="text-sm">
      {errorMsg || "A newer version of the meeting-capture helper is available."}
    </p>
    <div class="flex shrink-0 items-center gap-2">
      <button
        onclick={onUpdate}
        disabled={updating}
        class="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-amber-700 disabled:opacity-60"
      >
        {updating ? "Updating…" : "Update now"}
      </button>
      <button onclick={dismissWatcherUpdateBanner} class="text-amber-500 hover:text-amber-700" aria-label="Dismiss">
        <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  </div>
{/if}
