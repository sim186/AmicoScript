<script lang="ts">
  import { transcribeOptions, setCloudPower, setColabUrl } from "$lib/transcribeOptions.svelte";
  import Toggle from "$lib/components/Toggle.svelte";
</script>

<div class="flex flex-col gap-3">
  <div class="flex items-center gap-2">
    <Toggle id="cloud-power-toggle" checked={transcribeOptions.cloudPower} onchange={setCloudPower} label="Enable Cloud Power" />
    <span class="text-sm text-ink">Cloud Power (Colab GPU)</span>
  </div>

  {#if transcribeOptions.cloudPower}
    <div id="cloud-power-section">
      <label for="colab-url-input" class="mb-1 block text-xs text-ink-muted">ngrok URL from colab_bridge.ipynb</label>
      <input
        id="colab-url-input"
        type="text"
        placeholder="https://xxxx.ngrok-free.app"
        value={transcribeOptions.colabUrl}
        oninput={(e) => setColabUrl((e.target as HTMLInputElement).value)}
        class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
      />
    </div>
  {/if}
</div>
