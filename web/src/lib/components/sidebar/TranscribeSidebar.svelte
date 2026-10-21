<script lang="ts">
  import ModelLanguageSection from "./ModelLanguageSection.svelte";
  import DiarizationSection from "./DiarizationSection.svelte";

  let { hfTokenPreview, hfTokenMasked }: { hfTokenPreview: string; hfTokenMasked: boolean } = $props();
</script>

<div id="sidebar-transcribe" class="flex flex-col gap-1 overflow-y-auto p-4">
  <details open class="group">
    <summary class="flex cursor-pointer list-none select-none items-center justify-between py-1 text-xs font-semibold uppercase tracking-wider text-ink-muted">
      Model &amp; language
      <svg class="h-3.5 w-3.5 text-ink-muted transition-transform group-open:rotate-180" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
      </svg>
    </summary>
    <div class="mt-2 mb-3"><ModelLanguageSection /></div>
  </details>

  <details open class="group">
    <summary class="flex cursor-pointer list-none select-none items-center justify-between py-1 text-xs font-semibold uppercase tracking-wider text-ink-muted">
      <span class="flex items-center gap-2">Speakers <span class="rounded-full bg-brand-muted/20 px-1.5 py-0.5 text-[10px] font-bold normal-case tracking-normal text-brand">BETA</span></span>
      <svg class="h-3.5 w-3.5 text-ink-muted transition-transform group-open:rotate-180" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
      </svg>
    </summary>
    <div class="mt-2 mb-3"><DiarizationSection {hfTokenPreview} {hfTokenMasked} /></div>
  </details>
</div>
