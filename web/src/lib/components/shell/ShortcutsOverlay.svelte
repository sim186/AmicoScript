<script lang="ts">
  import { modalState, toggleShortcutsOverlay } from "$lib/modals.svelte";

  const rows: [string, string][] = [
    ["Space", "Play / pause"],
    ["J / K", "Seek −5s / +5s"],
    ["E", "Edit current segment"],
    ["?", "Toggle this overlay"],
    ["Esc", "Close overlays"],
    ["Ctrl/⌘ K", "Open the command palette"],
  ];
</script>

{#if modalState.shortcutsVisible}
  <div
    role="presentation"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50"
    onclick={() => toggleShortcutsOverlay(false)}
  >
    <div role="presentation" class="w-full max-w-sm rounded-2xl bg-white p-5 shadow-xl" onclick={(e) => e.stopPropagation()}>
      <h2 class="mb-3 text-sm font-semibold text-slate-800">Keyboard shortcuts</h2>
      <dl class="flex flex-col gap-2">
        {#each rows as [key, desc]}
          <div class="flex items-center justify-between text-sm">
            <dt class="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-mono text-xs text-slate-600">{key}</dt>
            <dd class="text-slate-500">{desc}</dd>
          </div>
        {/each}
      </dl>
    </div>
  </div>
{/if}
