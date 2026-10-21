<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    open,
    onClose,
    align = "center",
    class: extraClass = "",
    children,
  }: {
    open: boolean;
    onClose: () => void;
    align?: "center" | "top";
    class?: string;
    children: Snippet;
  } = $props();

  function onBackdropMousedown(e: MouseEvent) {
    if (e.target === e.currentTarget) onClose();
  }

  function onWindowKeydown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  }
</script>

<svelte:window onkeydown={open ? onWindowKeydown : undefined} />

{#if open}
  <div
    role="presentation"
    class="fixed inset-0 z-50 flex justify-center bg-slate-900/50 {align === 'top' ? 'items-start pt-24' : 'items-center'} {extraClass}"
    onmousedown={onBackdropMousedown}
  >
    {@render children()}
  </div>
{/if}
