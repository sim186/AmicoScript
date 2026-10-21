<script lang="ts">
  import { transcribeOptions, selectModel, selectLanguage, MODELS } from "$lib/transcribeOptions.svelte";

  const LANGUAGES: [string, string][] = [
    ["", "Auto-detect"],
    ["en", "English"],
    ["it", "Italian"],
    ["es", "Spanish"],
    ["fr", "French"],
    ["de", "German"],
    ["pt", "Portuguese"],
    ["nl", "Dutch"],
    ["pl", "Polish"],
    ["ru", "Russian"],
    ["ja", "Japanese"],
    ["zh", "Chinese"],
    ["ko", "Korean"],
    ["ar", "Arabic"],
    ["tr", "Turkish"],
    ["sv", "Swedish"],
    ["da", "Danish"],
    ["fi", "Finnish"],
    ["no", "Norwegian"],
    ["uk", "Ukrainian"],
  ];
</script>

<div class="flex flex-col gap-3">
  <div>
    <label for="model-select" class="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink-muted">Model</label>
    <select
      id="model-select"
      value={transcribeOptions.selectedModel}
      onchange={(e) => selectModel((e.target as HTMLSelectElement).value)}
      class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
    >
      {#each MODELS as m}
        <option value={m.id}>{m.name} ({m.params}) — {m.note}</option>
      {/each}
    </select>
  </div>

  <div>
    <label for="language-select" class="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink-muted">Language</label>
    <select
      id="language-select"
      value={transcribeOptions.selectedLanguage}
      onchange={(e) => selectLanguage((e.target as HTMLSelectElement).value)}
      class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
    >
      {#each LANGUAGES as [code, label]}
        <option value={code}>{label}</option>
      {/each}
    </select>
  </div>
</div>
