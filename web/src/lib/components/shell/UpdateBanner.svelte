<script lang="ts">
  import { updateBannerState, dismissUpdateBanner } from "$lib/version.svelte";
</script>

{#if updateBannerState.visible}
  <div id="update-banner" class="flex items-center justify-between gap-3 border-b border-warning/30 bg-warning/10 px-4 py-3 text-amber-300">
    <div class="flex items-center gap-3">
      <svg class="h-5 w-5 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3"></path>
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 2a10 10 0 100 20 10 10 0 000-20z"></path>
      </svg>
      <div id="update-banner-text" class="text-sm">{updateBannerState.text}</div>
    </div>
    <div class="flex items-center gap-2">
      <a id="update-banner-link" href={updateBannerState.url} target="_blank" rel="noopener" class="text-sm font-semibold text-amber-300 hover:underline">
        View release
      </a>
      <button id="update-banner-dismiss" onclick={dismissUpdateBanner} class="rounded border-0 bg-transparent px-2 py-1 text-sm text-amber-400 hover:bg-warning/20">
        Dismiss
      </button>
    </div>
  </div>
{/if}
