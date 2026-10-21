<script lang="ts">
  import { settingsState, closeSettingsModal, type SettingsSection } from "$lib/settings.svelte";
  import ModalScrim from "../shell/ModalScrim.svelte";
  import CloudPowerSection from "../sidebar/CloudPowerSection.svelte";
  import MeetingCaptureSection from "../sidebar/MeetingCaptureSection.svelte";
  import LlmSettingsSection from "../sidebar/LlmSettingsSection.svelte";
  import BenchmarkSection from "../sidebar/BenchmarkSection.svelte";
  import SecuritySection from "../sidebar/SecuritySection.svelte";
  import BackupSection from "../sidebar/BackupSection.svelte";

  const NAV: [SettingsSection, string][] = [
    ["general", "General"],
    ["ai", "AI"],
    ["advanced", "Advanced"],
  ];
</script>

<ModalScrim open={settingsState.open} onClose={closeSettingsModal}>
  <div
    role="presentation"
    class="flex h-[85vh] w-[90vw] max-w-3xl overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl"
    onclick={(e) => e.stopPropagation()}
  >
    <div class="flex w-44 shrink-0 flex-col gap-1 border-r border-border bg-surface-alt p-3">
      <h2 class="mb-2 px-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">Settings</h2>
      {#each NAV as [key, label] (key)}
        <button
          type="button"
          class="rounded-lg px-3 py-2 text-left text-sm transition {settingsState.section === key
            ? 'bg-brand-dark-bg/15 text-brand-dark-bg'
            : 'text-ink-muted hover:bg-surface hover:text-ink'}"
          onclick={() => (settingsState.section = key)}
        >
          {label}
        </button>
      {/each}
      <div class="flex-1"></div>
      <button
        type="button"
        onclick={closeSettingsModal}
        class="rounded-lg px-3 py-2 text-left text-sm text-ink-muted transition hover:bg-surface hover:text-ink"
      >
        Close
      </button>
    </div>
    <div class="flex-1 overflow-y-auto p-6">
      {#if settingsState.section === "general"}
        <div class="space-y-6">
          <section>
            <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">Cloud Power</h3>
            <CloudPowerSection />
          </section>
          <section>
            <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">Meeting Capture</h3>
            <MeetingCaptureSection />
          </section>
        </div>
      {:else if settingsState.section === "ai"}
        <div class="space-y-6">
          <section>
            <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">AI / LLM</h3>
            <LlmSettingsSection />
          </section>
          <section>
            <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">Benchmark</h3>
            <BenchmarkSection />
          </section>
        </div>
      {:else}
        <div class="space-y-6">
          <section>
            <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">Security</h3>
            <SecuritySection />
          </section>
          <section>
            <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">Backup</h3>
            <BackupSection />
          </section>
        </div>
      {/if}
    </div>
  </div>
</ModalScrim>
