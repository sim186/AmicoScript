<script lang="ts">
  import {
    llmCatalog,
    llmSettings,
    modelsBrowser,
    getProvider,
    providerNotes,
    applyProvider,
    detectServers,
    useServer,
    saveLlmSettings,
    openModelsBrowser,
    refreshModelsBrowser,
    selectLlmModel,
    pullLlmModel,
    testLlmConnection,
    popularModels,
    type DetectedServer,
  } from "$lib/llm.svelte";

  let detecting = $state(false);
  let detectStatus = $state("");
  let detectTone = $state<"info" | "ok" | "error">("info");
  let detectResults = $state<DetectedServer[]>([]);
  let showApiKey = $state(false);
  let saveTimer: ReturnType<typeof setTimeout> | undefined;

  const provider = $derived(getProvider(llmSettings.provider));
  const apiKeyRequirement = $derived(
    provider?.api_key === "required"
      ? `(required${provider.key_hint ? ` — ${provider.key_hint}` : ""})`
      : provider?.api_key === "none"
        ? "(not needed)"
        : "(optional)",
  );

  function debouncedSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveLlmSettings, 600);
  }

  function onProviderChange(e: Event) {
    applyProvider((e.target as HTMLSelectElement).value, { fillUrl: true });
    debouncedSave();
  }

  async function onDetect() {
    detecting = true;
    detectStatus = "Checking the usual ports…";
    detectTone = "info";
    detectResults = [];
    const result = await detectServers();
    detectResults = result.servers;
    detectStatus = result.status;
    detectTone = result.tone;
    detecting = false;
  }

  function onUseServer(server: DetectedServer) {
    useServer(server);
    detectStatus = server.needs_api_key ? `Selected ${server.label}. Paste its API key below, then save.` : `Selected ${server.label}.`;
    detectTone = "ok";
  }

  async function onBrowseModels() {
    if (modelsBrowser.open) modelsBrowser.open = false;
    else await openModelsBrowser();
  }
</script>

<div class="flex flex-col gap-3 text-xs">
  <div>
    <label for="llm-provider-select" class="mb-1 block font-semibold uppercase tracking-wide text-slate-500">Provider</label>
    <select
      id="llm-provider-select"
      value={llmSettings.provider}
      onchange={onProviderChange}
      class="w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
    >
      {#each llmCatalog.providers as p}
        <option value={p.id}>{p.label}</option>
      {/each}
    </select>
    {#if provider}
      <p id="llm-provider-notes" class="mt-1 text-slate-500">{providerNotes(provider)}</p>
      {#if provider.docs_url}
        <a id="llm-provider-docs" href={provider.docs_url} target="_blank" rel="noopener" class="text-brand hover:underline">Docs ↗</a>
      {/if}
    {/if}
  </div>

  <div>
    <button
      id="llm-detect-btn"
      onclick={onDetect}
      disabled={detecting}
      class="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 transition hover:border-brand hover:text-brand"
    >
      {detecting ? "Scanning…" : "Auto-detect a running server"}
    </button>
    {#if detectStatus}
      <p id="llm-detect-status" class="mt-1 {detectTone === 'error' ? 'text-red-600' : detectTone === 'ok' ? 'text-green-600' : 'text-slate-500'}">
        {detectStatus}
      </p>
    {/if}
    {#if detectResults.length}
      <div id="llm-detect-results" class="mt-1 flex flex-col gap-1.5">
        {#each detectResults as server}
          <div class="flex items-center justify-between gap-2 rounded-lg border border-slate-200 p-1.5">
            <div class="min-w-0">
              <p class="truncate text-xs font-semibold text-slate-700">{server.label}</p>
              <p class="truncate text-[10px] text-slate-400">
                {server.base_url} · {server.needs_api_key ? "needs an API key" : `${server.model_count || 0} model(s)`}
              </p>
            </div>
            <button onclick={() => onUseServer(server)} class="shrink-0 rounded-md bg-brand px-2 py-1 text-[10px] text-white transition hover:bg-brand-hover">
              Use
            </button>
          </div>
        {/each}
      </div>
    {/if}
  </div>

  <div>
    <label for="llm-base-url" class="mb-1 block text-slate-500">Base URL</label>
    <input
      id="llm-base-url"
      type="text"
      placeholder={provider?.base_url || "https://your-server.example.com"}
      bind:value={llmSettings.baseUrl}
      oninput={debouncedSave}
      class="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
    />
    {#if llmSettings.urlNote}
      <p id="llm-url-note" class="mt-1 text-slate-500">{llmSettings.urlNote}</p>
    {/if}
  </div>

  <div class="relative">
    <label for="llm-model-input" class="mb-1 block text-slate-500">Model</label>
    <div class="flex gap-1">
      <input
        id="llm-model-input"
        type="text"
        bind:value={llmSettings.modelName}
        oninput={debouncedSave}
        class="flex-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
      />
      <button
        id="llm-browse-models-btn"
        onclick={onBrowseModels}
        title={provider?.supports_pull ? "Browse and download models" : "Browse the models this server has loaded"}
        class="shrink-0 rounded-lg border border-slate-200 px-2 py-1.5 text-slate-500 transition hover:border-brand hover:text-brand"
      >
        Browse
      </button>
    </div>

    {#if modelsBrowser.open}
      <div id="llm-models-panel" class="absolute z-10 mt-1 w-full rounded-lg border border-slate-200 bg-white shadow-lg">
        <div class="flex items-center justify-between border-b border-slate-100 px-3 py-1.5">
          <span class="font-medium text-slate-600">Models</span>
          <button id="llm-refresh-models-btn" onclick={() => refreshModelsBrowser()} class="text-slate-400 hover:text-brand">↻ Refresh</button>
        </div>
        {#if modelsBrowser.installed.length}
          <div id="llm-installed-section">
            <div id="llm-installed-list">
              {#each modelsBrowser.installed as m}
                <div class="flex items-center px-3 py-1.5 text-xs hover:bg-slate-50">
                  <button onclick={() => selectLlmModel(m.id)} class="flex-1 truncate text-left font-medium text-slate-700 transition hover:text-brand">
                    {m.id}
                  </button>
                  <span class="shrink-0 text-slate-400">installed</span>
                </div>
              {/each}
            </div>
          </div>
        {/if}
        <div id="llm-popular-list">
          {#each popularModels() as m}
            <div class="flex items-center justify-between gap-2 px-3 py-1.5 text-xs hover:bg-slate-50">
              <button onclick={() => selectLlmModel(m.id)} class="flex-1 truncate text-left text-slate-700 transition hover:text-brand">{m.label}</button>
              <button
                onclick={() => pullLlmModel(m.id)}
                disabled={modelsBrowser.pulling[m.id] === "pulling"}
                class="shrink-0 rounded border border-slate-200 px-2 py-0.5 text-[10px] text-slate-500 transition hover:border-brand hover:text-brand"
              >
                {modelsBrowser.pulling[m.id] === "pulling" ? "…" : modelsBrowser.pulling[m.id] === "ok" ? "✓" : modelsBrowser.pulling[m.id] === "error" ? "✗" : "Pull"}
              </button>
            </div>
          {/each}
        </div>
      </div>
    {/if}
  </div>

  <div>
    <label for="llm-api-key" class="mb-1 block text-slate-500">API key {apiKeyRequirement}</label>
    <div class="relative">
      <input
        id="llm-api-key"
        type={showApiKey ? "text" : "password"}
        bind:value={llmSettings.apiKey}
        onfocus={() => {
          if (llmSettings.apiKeyMasked) {
            llmSettings.apiKey = "";
            llmSettings.apiKeyMasked = false;
          }
        }}
        oninput={debouncedSave}
        class="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 pr-8 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
      />
      <button
        id="llm-key-eye"
        type="button"
        onclick={() => (showApiKey = !showApiKey)}
        class="absolute inset-y-0 right-0 flex items-center px-2 text-slate-400 hover:text-slate-600"
      >
        {showApiKey ? "🙈" : "👁"}
      </button>
    </div>
  </div>

  {#if provider?.cloud}
    <label id="llm-cloud-consent" class="flex items-center gap-2 text-slate-600">
      <input
        id="llm-allow-cloud"
        type="checkbox"
        bind:checked={llmSettings.allowCloud}
        onchange={debouncedSave}
        class="rounded border-slate-300 text-brand focus:ring-brand"
      />
      I consent to sending transcript data to this cloud provider.
    </label>
  {/if}

  <div>
    <label for="llm-context-tokens" class="mb-1 block text-slate-500">Context budget (tokens)</label>
    <input
      id="llm-context-tokens"
      type="number"
      bind:value={llmSettings.contextTokens}
      oninput={debouncedSave}
      class="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-brand"
    />
  </div>

  <div class="flex items-center justify-between gap-2">
    <button
      id="llm-test-btn"
      onclick={testLlmConnection}
      class="flex-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-slate-600 transition hover:border-brand hover:text-brand"
    >
      Test Connection
    </button>
  </div>
  {#if llmSettings.testStatus}
    <p id="llm-test-status" class="{llmSettings.testTone === 'ok' ? 'text-emerald-500' : llmSettings.testTone === 'error' ? 'text-red-500' : 'text-slate-400'}">
      {llmSettings.testStatus}
    </p>
  {/if}
</div>
