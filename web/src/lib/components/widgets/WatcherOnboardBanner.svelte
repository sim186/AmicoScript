<script lang="ts">
  import { watcherState, dismissWatcherOnboarding, revealWatcherOnboardingSteps } from "$lib/watcher.svelte";
</script>

{#if watcherState.onboardBannerVisible}
  <div class="flex items-center justify-between gap-3 border-b border-brand/20 bg-brand-muted/30 px-4 py-3 text-ink">
    <div class="flex items-center gap-2">
      <svg class="h-5 w-5 shrink-0 text-brand" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z"
        />
      </svg>
      <p class="text-sm">
        Capture meetings automatically — install the helper once to get started.
        {#if watcherState.installer}
          <a
            id="watcher-onboard-setup"
            href={watcherState.installer.url}
            download={watcherState.installer.name}
            onclick={revealWatcherOnboardingSteps}
            class="ml-1 font-semibold text-white bg-brand hover:bg-brand-hover px-3 py-1.5 rounded-lg"
          >
            Set it up
          </a>
        {/if}
      </p>
    </div>
    <button onclick={dismissWatcherOnboarding} class="shrink-0 text-ink-muted hover:text-ink" aria-label="Dismiss">
      <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    </button>
  </div>
  {#if watcherState.onboardStepsVisible && watcherState.installer}
    <div id="watcher-onboard-steps" class="border-b border-warning/20 bg-warning/10 px-4 py-3 text-sm text-amber-400">
      Setup script downloaded. <span id="watcher-onboard-hint" class="font-semibold">{watcherState.installer.hint}</span>
      It installs the helper and starts it; this bar disappears automatically once it's running.
    </div>
  {/if}
{/if}
