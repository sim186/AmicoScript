<script lang="ts">
  import { onMount } from "svelte";
  import { installAuthAwareFetch } from "$lib/http";
  import { initAuth } from "$lib/auth.svelte";
  import { restoreDrawerState, initViewportTracking } from "$lib/session.svelte";
  import { restoreTranscriptionSettings } from "$lib/transcribeOptions.svelte";
  import { loadLlmSettingsFromServer, autoTestLlmConnection } from "$lib/llm.svelte";
  import { setMeetingCaptureToggleState, autoSummaryState, startWatcherPolling } from "$lib/watcher.svelte";
  import { fetchVersion, fetchLatestRelease } from "$lib/version.svelte";
  import { startQueuePolling } from "$lib/queueWidget.svelte";
  import { initShortcuts } from "$lib/shortcuts";
  import { transcribeOptions } from "$lib/transcribeOptions.svelte";
  import LoginOverlay from "$lib/components/LoginOverlay.svelte";
  import AppShell from "$lib/components/shell/AppShell.svelte";

  let hfTokenPreview = $state("");
  let hfTokenMasked = $state(false);

  onMount(() => {
    installAuthAwareFetch();
    initViewportTracking();
    restoreDrawerState();
    initShortcuts();
    initAuth();
    startWatcherPolling();
    startQueuePolling();
    fetchVersion();
    fetchLatestRelease();

    restoreTranscriptionSettings().then((restored) => {
      hfTokenPreview = restored.preview;
      hfTokenMasked = restored.masked;
      setMeetingCaptureToggleState(restored.meetingCaptureEnabled);
      autoSummaryState.enabled = restored.autoSummarizeMeetings;
    });

    loadLlmSettingsFromServer().then((configured) => {
      autoSummaryState.llmConfigured = configured;
    });

    setTimeout(() => autoTestLlmConnection(), 2000);

    window.addEventListener("beforeunload", () => {
      try {
        navigator.sendBeacon(`/api/exit?token=${encodeURIComponent(transcribeOptions.exitToken || "")}`);
      } catch {
        /* best-effort only */
      }
    });
  });
</script>

<AppShell {hfTokenPreview} {hfTokenMasked} />
<LoginOverlay />
