<script lang="ts">
  import {
    paletteState,
    openCommandPalette,
    closeCommandPalette,
    onPaletteInput,
    movePaletteSelection,
    activatePaletteRow,
    paletteEnterPressed,
  } from "$lib/commandPalette.svelte";

  let inputEl = $state<HTMLInputElement | undefined>();

  $effect(() => {
    if (paletteState.open && inputEl) {
      inputEl.focus();
      inputEl.select();
    }
  });

  function onWindowKeydown(e: KeyboardEvent) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (paletteState.open) closeCommandPalette();
      else openCommandPalette();
    }
  }

  function onInputKeydown(e: KeyboardEvent) {
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        movePaletteSelection(1);
        break;
      case "ArrowUp":
        e.preventDefault();
        movePaletteSelection(-1);
        break;
      case "Home":
        e.preventDefault();
        paletteState.activeIndex = 0;
        break;
      case "End":
        e.preventDefault();
        paletteState.activeIndex = paletteState.rows.length - 1;
        break;
      case "Enter":
        e.preventDefault();
        paletteEnterPressed();
        break;
      case "Escape":
        e.preventDefault();
        closeCommandPalette();
        break;
    }
  }

  function rowIcon(row: (typeof paletteState.rows)[number]): string {
    if (row.type === "command") return "⌘";
    if (row.type === "search-all") return "⏎";
    return "♪";
  }
</script>

<svelte:window onkeydown={onWindowKeydown} />

{#if paletteState.open}
  <div role="presentation" class="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/50 pt-24" onmousedown={(e) => { if (e.target === e.currentTarget) closeCommandPalette(); }}>
    <div class="w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-2xl">
      <input
        bind:this={inputEl}
        value={paletteState.query}
        oninput={(e) => onPaletteInput((e.target as HTMLInputElement).value)}
        onkeydown={onInputKeydown}
        placeholder="Search or run a command… (try / or @)"
        class="w-full border-b border-slate-200 px-4 py-3 text-sm text-slate-800 focus:outline-none"
      />
      <ul class="max-h-96 overflow-y-auto py-1" role="listbox">
        {#if paletteState.searching}
          <li class="px-4 py-2 text-xs text-slate-400">Searching…</li>
        {/if}
        {#if !paletteState.rows.length}
          <li class="px-4 py-6 text-center text-sm text-slate-400">
            {paletteState.query.trim() ? "Nothing matches." : "Type to search your library"}
          </li>
        {/if}
        {#each paletteState.rows as row, i (i)}
          <li
            role="option"
            aria-selected={i === paletteState.activeIndex}
            tabindex="-1"
            class="flex cursor-pointer items-center gap-2 px-4 py-2 text-sm {i === paletteState.activeIndex ? 'bg-brand-muted/40' : ''} {row.disabled ? 'cursor-not-allowed opacity-40' : ''}"
            onmousemove={() => (paletteState.activeIndex = i)}
            onclick={() => activatePaletteRow(i)}
            onkeydown={(e) => (e.key === "Enter" || e.key === " ") && activatePaletteRow(i)}
          >
            <span class="w-4 shrink-0 text-center text-slate-400">{rowIcon(row)}</span>
            <span class="min-w-0 flex-1">
              <span class="block truncate font-medium text-slate-700">{row.title}</span>
              {#if row.subtitleHtml}
                <span class="block truncate text-xs text-slate-400">{@html row.subtitleHtml}</span>
              {/if}
            </span>
            {#if row.metaHtml}
              <span class="flex shrink-0 items-center gap-1.5 text-[10px] text-slate-400">{@html row.metaHtml}</span>
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  </div>
{/if}
