<script lang="ts">
  import { transcribeOptions, setDiarize, saveHfTokenDebounced } from "$lib/transcribeOptions.svelte";
  import Toggle from "$lib/components/Toggle.svelte";

  let { hfTokenPreview, hfTokenMasked }: { hfTokenPreview: string; hfTokenMasked: boolean } = $props();

  let hfInputEl = $state<HTMLInputElement | undefined>();
  let masked = $state(false);
  let showToken = $state(false);
  let saved = $state(false);

  $effect(() => {
    masked = hfTokenMasked;
  });

  $effect(() => {
    if (hfInputEl && hfTokenPreview) hfInputEl.value = hfTokenPreview;
  });

  function onFocus() {
    if (masked) {
      transcribeOptions.hfToken = "";
      if (hfInputEl) hfInputEl.value = "";
      masked = false;
    }
  }

  function onInput(e: Event) {
    const value = (e.target as HTMLInputElement).value;
    transcribeOptions.hfToken = value;
    saved = false;
    saveHfTokenDebounced(value, () => {
      saved = true;
      setTimeout(() => (saved = false), 2000);
    });
  }
</script>

<div class="flex flex-col gap-3">
  <div class="flex items-center gap-2">
    <Toggle
      id="diarize-toggle"
      checked={transcribeOptions.diarize}
      onchange={setDiarize}
      label="Enable speaker diarization"
    />
    <span class="text-sm text-slate-700">Speaker diarization</span>
  </div>

  {#if transcribeOptions.diarize}
    <div id="hf-section" class="flex flex-col gap-3">
      <div>
        <label for="hf-token-input" class="mb-1 block text-xs text-slate-500">HuggingFace Token</label>
        <div class="relative">
          <input
            bind:this={hfInputEl}
            type={showToken ? "text" : "password"}
            id="hf-token-input"
            placeholder="hf_…"
            autocomplete="off"
            onfocus={onFocus}
            oninput={onInput}
            class="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 pr-9 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
          />
          <button
            id="hf-eye-btn"
            type="button"
            aria-label="Toggle token visibility"
            onclick={() => (showToken = !showToken)}
            class="absolute inset-y-0 right-0 flex items-center px-2.5 text-slate-400 hover:text-slate-600"
          >
            {showToken ? "🙈" : "👁"}
          </button>
        </div>
        <p id="hf-saved-indicator" class="mt-1 text-[10px] text-emerald-600" class:hidden={!saved}>Saved</p>
        <a href="https://huggingface.co/settings/tokens" target="_blank" rel="noopener" class="text-[10px] text-brand hover:underline">
          Get token ↗
        </a>
        ·
        <a href="https://huggingface.co/pyannote/speaker-diarization-3.1" target="_blank" rel="noopener" class="text-[10px] text-brand hover:underline">
          Accept license ↗
        </a>
      </div>

      <div class="grid grid-cols-3 gap-2">
        <div>
          <label for="num-speakers-input" class="mb-1 block text-[10px] text-slate-500">Exact</label>
          <input
            id="num-speakers-input"
            type="number"
            min="1"
            bind:value={transcribeOptions.numSpeakers}
            class="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
          />
        </div>
        <div>
          <label for="min-speakers-input" class="mb-1 block text-[10px] text-slate-500">Min</label>
          <input
            id="min-speakers-input"
            type="number"
            min="1"
            bind:value={transcribeOptions.minSpeakers}
            class="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
          />
        </div>
        <div>
          <label for="max-speakers-input" class="mb-1 block text-[10px] text-slate-500">Max</label>
          <input
            id="max-speakers-input"
            type="number"
            min="1"
            bind:value={transcribeOptions.maxSpeakers}
            class="w-full rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
          />
        </div>
      </div>
    </div>
  {/if}
</div>
