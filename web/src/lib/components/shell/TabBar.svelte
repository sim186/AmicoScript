<script lang="ts">
  import { session, switchTab, toggleDrawer, type TabName } from "$lib/session.svelte";
  import { openCommandPalette } from "$lib/commandPalette.svelte";
  import { toggleShortcutsOverlay } from "$lib/modals.svelte";

  const isMac = /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);

  function tabClass(name: TabName) {
    const active = session.activeTab === name;
    return `tab-btn px-4 py-3 text-sm font-medium border-b-2 -mb-px focus:outline-none ${
      active ? "border-brand text-brand" : "border-transparent text-slate-500"
    }`;
  }
</script>

<div class="flex items-center border-b border-slate-200 bg-white px-2 md:px-4">
  <button
    onclick={toggleDrawer}
    aria-label="Toggle sidebar"
    class="mb-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-500 transition hover:bg-brand-muted/30 hover:text-brand focus:outline-none"
  >
    <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  </button>

  <button id="tab-btn-transcribe" class={tabClass("transcribe")} onclick={() => switchTab("transcribe")}>
    Transcribe
  </button>
  <button
    id="tab-btn-transcript"
    class={tabClass("transcript")}
    disabled={!session.transcriptEnabled}
    class:opacity-40={!session.transcriptEnabled}
    onclick={() => switchTab("transcript")}
  >
    Transcript
  </button>
  <button id="tab-btn-library" class={tabClass("library")} onclick={() => switchTab("library")}>
    Library
  </button>

  <div class="ml-auto flex items-center gap-2 py-1.5">
    <button
      onclick={() => toggleShortcutsOverlay(true)}
      title="Keyboard Shortcuts (?)"
      class="rounded-lg p-1.5 text-slate-400 transition hover:text-brand"
    >
      <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9.5 9a2.5 2.5 0 115 0c0 1.5-2.5 2-2.5 3.5M12 17h.01" />
      </svg>
    </button>
    <button
      onclick={() => openCommandPalette()}
      class="flex w-9 items-center justify-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-xs text-slate-400 transition hover:border-brand hover:text-brand focus:outline-none focus:ring-2 focus:ring-brand md:w-56 md:justify-start md:px-3"
    >
      <svg class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" />
      </svg>
      <span class="hidden md:inline">Search or run a command…</span>
      <span class="ml-auto hidden rounded border border-slate-200 px-1 font-mono text-[10px] md:inline">
        {isMac ? "⌘K" : "Ctrl K"}
      </span>
    </button>
  </div>
</div>
