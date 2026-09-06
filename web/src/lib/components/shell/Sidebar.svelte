<script lang="ts">
  import { session, closeDrawer, viewport } from "$lib/session.svelte";
  import { versionState } from "$lib/version.svelte";
  import { toggleShortcutsOverlay, openHelpModal, fetchAndShowChangelog } from "$lib/modals.svelte";
  import TranscribeSidebar from "../sidebar/TranscribeSidebar.svelte";
  import TranscriptSidebar from "../sidebar/TranscriptSidebar.svelte";
  import LibrarySidebar from "../library/LibrarySidebar.svelte";

  let { hfTokenPreview, hfTokenMasked }: { hfTokenPreview: string; hfTokenMasked: boolean } = $props();
</script>

{#if viewport.mobile && session.drawerOpen}
  <div
    id="sidebar-backdrop"
    role="presentation"
    class="active fixed inset-0 z-40 bg-slate-900/40"
    onclick={closeDrawer}
  ></div>
{/if}

<aside
  id="sidebar"
  class="flex h-full w-72 shrink-0 flex-col overflow-hidden border-r border-slate-200 bg-white transition-transform md:relative md:translate-x-0 {session.drawerOpen
    ? 'translate-x-0'
    : '-translate-x-full md:hidden'} {viewport.mobile ? 'fixed inset-y-0 left-0 z-50' : ''}"
>
  <div class="border-b border-slate-100 px-5 py-5">
    <div class="mb-1 flex items-center gap-2">
      <img src="/logo.svg" alt="AmicoScript Logo" class="h-6 w-6" />
      <h1 class="text-lg font-bold tracking-tight text-slate-900">AmicoScript</h1>
    </div>
    <p class="mt-0.5 text-xs text-slate-400">Local audio transcription</p>
  </div>

  <div class="flex-1 overflow-y-auto">
    {#if session.activeTab === "transcribe"}
      <TranscribeSidebar {hfTokenPreview} {hfTokenMasked} />
    {:else if session.activeTab === "transcript"}
      <TranscriptSidebar />
    {:else}
      <LibrarySidebar />
    {/if}
  </div>

  <div class="space-y-2 border-t border-slate-100 px-4 py-3 text-xs">
    <div class="flex items-center justify-between">
      <span class="text-slate-400">v<span id="app-version" class="font-medium text-slate-600">{versionState.version || "—"}</span></span>
      <a id="github-link" href={versionState.githubUrl} target="_blank" rel="noopener" class="flex items-center gap-1 text-slate-400 transition hover:text-slate-700">
        <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor">
          <path
            d="M12 2C6.477 2 2 6.477 2 12c0 4.418 2.865 8.166 6.839 9.489.5.092.682-.217.682-.482 0-.237-.009-.868-.013-1.703-2.782.604-3.369-1.34-3.369-1.34-.454-1.156-1.11-1.463-1.11-1.463-.908-.62.069-.608.069-.608 1.003.07 1.531 1.03 1.531 1.03.892 1.529 2.341 1.087 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.11-4.555-4.943 0-1.091.39-1.984 1.029-2.683-.103-.253-.446-1.27.098-2.647 0 0 .84-.269 2.75 1.025A9.578 9.578 0 0112 6.836a9.59 9.59 0 012.504.337c1.909-1.294 2.747-1.025 2.747-1.025.546 1.377.202 2.394.1 2.647.64.699 1.028 1.592 1.028 2.683 0 3.842-2.339 4.687-4.566 4.935.359.309.678.919.678 1.852 0 1.336-.012 2.415-.012 2.741 0 .267.18.578.688.48C19.138 20.163 22 16.418 22 12c0-5.523-4.477-10-10-10z"
          />
        </svg>
        GitHub
      </a>
    </div>
    <div class="flex items-center justify-between">
      <button id="shortcuts-view-btn" type="button" onclick={() => toggleShortcutsOverlay(true)} class="border-0 bg-transparent p-0 text-slate-500 transition hover:text-brand">
        Shortcuts
      </button>
      <span class="text-slate-200">·</span>
      <button id="changelog-view-btn" type="button" onclick={() => fetchAndShowChangelog()} class="border-0 bg-transparent p-0 text-slate-500 transition hover:text-slate-700">
        Changelog
      </button>
      <span class="text-slate-200">·</span>
      <button id="help-view-btn" type="button" onclick={openHelpModal} class="border-0 bg-transparent p-0 text-slate-500 transition hover:text-slate-700">
        Help
      </button>
      <span class="text-slate-200">·</span>
      <a href="https://github.com/sim186/AmicoScript/issues/new/choose" target="_blank" rel="noopener" class="text-slate-500 transition hover:text-brand">
        Feedback
      </a>
    </div>
  </div>
</aside>
