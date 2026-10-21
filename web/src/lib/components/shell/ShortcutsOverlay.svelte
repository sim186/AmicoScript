<script lang="ts">
  import { modalState, toggleShortcutsOverlay } from "$lib/modals.svelte";
  import ModalScrim from "./ModalScrim.svelte";

  const rows: [string, string][] = [
    ["Space", "Play / pause"],
    ["J / K", "Seek −5s / +5s"],
    ["E", "Edit current segment"],
    ["?", "Toggle this overlay"],
    ["Esc", "Close overlays"],
    ["Ctrl/⌘ K", "Open the command palette"],
  ];
</script>

<ModalScrim open={modalState.shortcutsVisible} onClose={() => toggleShortcutsOverlay(false)}>
  <div role="presentation" class="w-full max-w-sm rounded-2xl bg-surface p-5 shadow-xl" onclick={(e) => e.stopPropagation()}>
    <h2 class="mb-3 text-sm font-semibold text-ink">Keyboard shortcuts</h2>
    <dl class="flex flex-col gap-2">
      {#each rows as [key, desc]}
        <div class="flex items-center justify-between text-sm">
          <dt class="rounded border border-border bg-surface-alt px-1.5 py-0.5 font-mono text-xs text-ink-muted">{key}</dt>
          <dd class="text-ink-muted">{desc}</dd>
        </div>
      {/each}
    </dl>
  </div>
</ModalScrim>
