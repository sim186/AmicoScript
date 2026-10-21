<script lang="ts">
  import { aiHubState, closeHub, setAnalyzeType, runAnalysis, stopAnalysis, type AnalysisType } from "$lib/aiHub.svelte";
  import { transcriptState } from "$lib/transcript.svelte";
  import { jobState } from "$lib/jobs.svelte";

  const ANALYSIS_TYPES: [AnalysisType, string][] = [
    ["summary", "Summary"],
    ["action_items", "Action Items"],
    ["translate", "Translate"],
    ["custom", "Custom"],
  ];

  const contextLabel = $derived(
    transcriptState.recordingId
      ? `Analyzing: ${transcriptState.currentRecording?.alias || transcriptState.result?.filename || "Recording"}`
      : "No recording loaded",
  );

  let messagesEl: HTMLDivElement | undefined = $state();
  $effect(() => {
    aiHubState.messages.length;
    if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
  });

  function onRunClick(): void {
    if (aiHubState.jobId) stopAnalysis();
    else runAnalysis();
  }
</script>

{#if aiHubState.open}
  <div class="flex h-full w-[340px] shrink-0 flex-col overflow-hidden border-l border-border bg-surface">
    <div class="flex shrink-0 items-center justify-between border-b border-border bg-surface-alt px-4 py-3">
      <div class="flex items-center gap-2">
        <svg class="h-4 w-4 text-brand-dark-bg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
          />
        </svg>
        <span class="text-sm font-semibold text-ink">AI Assistant</span>
      </div>
      <button onclick={closeHub} class="rounded p-1 text-ink-muted transition hover:bg-surface hover:text-ink" aria-label="Close">
        <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
      </button>
    </div>
    <div class="shrink-0 truncate border-b border-border bg-surface-alt px-4 py-1.5 text-[10px] text-ink-muted">{contextLabel}</div>
    <div bind:this={messagesEl} class="flex-1 space-y-3 overflow-y-auto bg-canvas px-4 py-3">
      {#if !aiHubState.messages.length}
        <div class="mr-auto max-w-[90%] rounded-2xl rounded-tl-sm border border-border bg-surface px-4 py-3 text-sm text-ink shadow-sm">
          <strong>Welcome.</strong><br />Open a recording, then choose an analysis type below to run it.
        </div>
      {/if}
      {#each aiHubState.messages as m (m.id)}
        <div
          class={m.role === "user"
            ? "ml-auto max-w-[85%] rounded-2xl rounded-tr-sm bg-brand-dark-bg px-4 py-2.5 text-sm text-white shadow-sm"
            : "mr-auto max-w-[90%] rounded-2xl rounded-tl-sm border border-border bg-surface px-4 py-3 text-sm text-ink shadow-sm"}
        >
          {#if m.spinner}
            <div class="flex items-center gap-2 text-ink-muted">
              <svg class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
              </svg>
              <span class="text-xs">{m.spinner}</span>
            </div>
          {:else}
            {@html m.html}
          {/if}
        </div>
      {/each}
      {#if jobState.processing && !transcriptState.recordingId}
        <p class="text-center text-[10px] text-ink-muted">Transcribing… analysis available once complete</p>
      {/if}
    </div>
    <div class="shrink-0 space-y-2 border-t border-border bg-surface px-4 py-3">
      <div class="flex flex-wrap gap-1.5">
        {#each ANALYSIS_TYPES as [value, label] (value)}
          <button
            type="button"
            onclick={() => setAnalyzeType(value)}
            class="rounded-md border px-2.5 py-1 text-xs font-medium transition {aiHubState.analyzeType === value
              ? 'border-brand-dark-bg bg-brand-dark-bg/10 text-brand-dark-bg'
              : 'border-border text-ink-muted'}"
          >
            {label}
          </button>
        {/each}
      </div>
      {#if aiHubState.analyzeType === "translate"}
        <input
          type="text"
          bind:value={aiHubState.targetLanguage}
          placeholder="Target language…"
          class="w-full rounded-md border border-border bg-surface-alt px-2.5 py-1.5 text-xs text-ink focus:outline-none focus:ring-1 focus:ring-brand-dark-bg"
        />
      {/if}
      {#if aiHubState.analyzeType === "custom"}
        <textarea
          bind:value={aiHubState.customPrompt}
          rows="2"
          placeholder="Your instructions for the LLM…"
          class="w-full resize-none rounded-md border border-border bg-surface-alt px-2.5 py-1.5 text-xs text-ink focus:outline-none focus:ring-1 focus:ring-brand-dark-bg"
        ></textarea>
      {/if}
      <input
        type="text"
        bind:value={aiHubState.outputLanguage}
        placeholder="Output language (optional)…"
        class="w-full rounded-md border border-border bg-surface-alt px-2.5 py-1.5 text-xs text-ink focus:outline-none focus:ring-1 focus:ring-brand-dark-bg"
      />
      <button
        type="button"
        onclick={onRunClick}
        disabled={!transcriptState.recordingId}
        class="flex w-full items-center justify-center gap-1.5 rounded-lg bg-brand-dark-bg px-3 py-2 text-xs font-semibold text-white transition hover:bg-brand disabled:opacity-40"
      >
        {aiHubState.jobId ? "Stop" : "Run Analysis"}
      </button>
      {#if aiHubState.pastCount > 0}
        <p class="text-center text-[10px] text-ink-muted">{aiHubState.pastCount} previous analysis{aiHubState.pastCount !== 1 ? "es" : ""} for this recording</p>
      {/if}
    </div>
  </div>
{/if}
