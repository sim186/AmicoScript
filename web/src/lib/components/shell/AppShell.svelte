<script lang="ts">
  import { session } from "$lib/session.svelte";
  import { consoleState } from "$lib/consoleLog.svelte";
  import TabBar from "./TabBar.svelte";
  import Sidebar from "./Sidebar.svelte";
  import UpdateBanner from "./UpdateBanner.svelte";
  import ShortcutsOverlay from "./ShortcutsOverlay.svelte";
  import HelpModal from "./HelpModal.svelte";
  import ChangelogModal from "./ChangelogModal.svelte";
  import CommandPalette from "./CommandPalette.svelte";
  import ConsolePanel from "./ConsolePanel.svelte";
  import WatcherOnboardBanner from "../widgets/WatcherOnboardBanner.svelte";
  import WatcherUpdateBanner from "../widgets/WatcherUpdateBanner.svelte";
  import RecordingChip from "../widgets/RecordingChip.svelte";
  import QueueWidget from "../widgets/QueueWidget.svelte";
  import TranscribeTab from "../transcribe/TranscribeTab.svelte";
  import TranscriptTab from "../transcript/TranscriptTab.svelte";
  import LibraryTab from "../library/LibraryTab.svelte";
  import EntityEditModal from "../library/EntityEditModal.svelte";
  import AiHub from "../widgets/AiHub.svelte";

  let { hfTokenPreview, hfTokenMasked }: { hfTokenPreview: string; hfTokenMasked: boolean } = $props();

  const consoleHeight = $derived(consoleState.open ? 240 : 36);
</script>

<div class="flex h-full w-full overflow-hidden bg-slate-100">
  <Sidebar {hfTokenPreview} {hfTokenMasked} />

  <div class="flex h-full min-w-0 flex-1 flex-col">
    <UpdateBanner />
    <WatcherOnboardBanner />
    <WatcherUpdateBanner />
    <TabBar />

    {#if session.activeTab === "transcribe"}
      <TranscribeTab />
    {:else if session.activeTab === "transcript"}
      <TranscriptTab />
    {:else}
      <LibraryTab />
    {/if}
  </div>
</div>

<div class="fixed right-4 z-[60] flex flex-col items-end gap-2" style="bottom: {consoleHeight + 16}px; transition: bottom 200ms ease;">
  <RecordingChip />
  <QueueWidget />
</div>

<AiHub />
<ConsolePanel />
<ShortcutsOverlay />
<HelpModal />
<ChangelogModal />
<CommandPalette />
<EntityEditModal />
