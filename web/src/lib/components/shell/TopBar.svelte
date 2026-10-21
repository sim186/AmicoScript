<script lang="ts">
  import { session, toggleDrawer, type TabName } from "$lib/session.svelte";
  import { openCommandPalette } from "$lib/commandPalette.svelte";
  import { toggleShortcutsOverlay } from "$lib/modals.svelte";
  import { aiHubState, toggleHub } from "$lib/aiHub.svelte";
  import RecordingChip from "../widgets/RecordingChip.svelte";
  import QueueWidget from "../widgets/QueueWidget.svelte";

  const isMac = /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);

  const breadcrumb: Record<TabName, string> = {
    transcribe: "Transcribe",
    transcript: "Transcript",
    library: "Library",
  };
</script>

<div class="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-surface px-4">
  <button
    onclick={toggleDrawer}
    aria-label="Toggle sidebar"
    class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-ink-muted transition hover:bg-surface-alt hover:text-ink focus:outline-none"
  >
    <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h16M4 18h16" />
    </svg>
  </button>

  <span class="text-sm font-medium text-ink">{breadcrumb[session.activeTab]}</span>

  <div class="ml-auto flex items-center gap-2">
    <RecordingChip />
    <QueueWidget />

    <button
      onclick={toggleHub}
      title="AI Assistant"
      class="flex h-9 w-9 items-center justify-center rounded-lg transition focus:outline-none {aiHubState.open
        ? 'bg-brand-dark-bg/15 text-brand-dark-bg'
        : 'text-ink-muted hover:bg-surface-alt hover:text-ink'}"
    >
      <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
        />
      </svg>
    </button>

    <button
      onclick={() => toggleShortcutsOverlay(true)}
      title="Keyboard Shortcuts (?)"
      class="rounded-lg p-1.5 text-ink-muted transition hover:text-ink"
    >
      <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9.5 9a2.5 2.5 0 115 0c0 1.5-2.5 2-2.5 3.5M12 17h.01" />
      </svg>
    </button>
    <button
      onclick={() => openCommandPalette()}
      class="flex w-9 items-center justify-center gap-2 rounded-lg border border-border bg-surface-alt px-2 py-1.5 text-xs text-ink-muted transition hover:border-brand-dark-bg hover:text-brand-dark-bg focus:outline-none focus:ring-2 focus:ring-brand-dark-bg md:w-56 md:justify-start md:px-3"
    >
      <svg class="h-4 w-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z" />
      </svg>
      <span class="hidden md:inline">Search or run a command…</span>
      <span class="ml-auto hidden rounded border border-border px-1 font-mono text-[10px] md:inline">
        {isMac ? "⌘K" : "Ctrl K"}
      </span>
    </button>
  </div>
</div>
