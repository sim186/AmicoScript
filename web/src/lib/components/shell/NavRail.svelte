<script lang="ts">
  import { session, switchTab, type TabName } from "$lib/session.svelte";

  let { onOpenSettings }: { onOpenSettings: () => void } = $props();

  function railBtnClass(name: TabName, disabled = false): string {
    const active = session.activeTab === name;
    return `flex h-11 w-11 items-center justify-center rounded-xl transition focus:outline-none focus:ring-2 focus:ring-brand-dark-bg ${
      active ? "bg-brand-dark-bg/15 text-brand-dark-bg" : "text-ink-muted hover:bg-surface-alt hover:text-ink"
    } ${disabled ? "cursor-not-allowed opacity-40 hover:bg-transparent hover:text-ink-muted" : ""}`;
  }
</script>

<nav class="flex h-full w-16 shrink-0 flex-col items-center gap-1 border-r border-border bg-canvas py-4">
  <img src="/logo.svg" alt="AmicoScript" class="mb-4 h-7 w-7" />

  <button
    id="rail-btn-transcribe"
    title="Transcribe"
    class={railBtnClass("transcribe")}
    onclick={() => switchTab("transcribe")}
  >
    <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24">
      <path
        stroke-linecap="round"
        stroke-linejoin="round"
        d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z"
      />
    </svg>
  </button>

  <button
    id="rail-btn-transcript"
    title="Transcript"
    disabled={!session.transcriptEnabled}
    class={railBtnClass("transcript", !session.transcriptEnabled)}
    onclick={() => switchTab("transcript")}
  >
    <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24">
      <path
        stroke-linecap="round"
        stroke-linejoin="round"
        d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m5.231 13.481L15 17.25m-1.519-3.844L13.5 12l-1.519 1.406m0 0L10.5 12l-1.481 1.406m0 0L8.25 12M4.5 21h9a1.5 1.5 0 001.5-1.5V9.621a1.5 1.5 0 00-.44-1.06l-4.622-4.622A1.5 1.5 0 008.378 3.5H4.5A1.5 1.5 0 003 5v14.5A1.5 1.5 0 004.5 21z"
      />
    </svg>
  </button>

  <button id="rail-btn-library" title="Library" class={railBtnClass("library")} onclick={() => switchTab("library")}>
    <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24">
      <path
        stroke-linecap="round"
        stroke-linejoin="round"
        d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-19.5 0v6a2.25 2.25 0 002.25 2.25h15a2.25 2.25 0 002.25-2.25v-6m-19.5 0h19.5M4.5 9.75V6.75A2.25 2.25 0 016.75 4.5h4.879a1.125 1.125 0 01.79.325l1.984 1.95A1.125 1.125 0 0015.192 7H17.25a2.25 2.25 0 012.25 2.25v.5"
      />
    </svg>
  </button>

  <div class="flex-1"></div>

  <button
    id="rail-btn-settings"
    title="Settings"
    class="flex h-11 w-11 items-center justify-center rounded-xl text-ink-muted transition hover:bg-surface-alt hover:text-ink focus:outline-none focus:ring-2 focus:ring-brand-dark-bg"
    onclick={onOpenSettings}
  >
    <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="1.75" viewBox="0 0 24 24">
      <path
        stroke-linecap="round"
        stroke-linejoin="round"
        d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.213-1.281z"
      />
      <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  </button>
</nav>
