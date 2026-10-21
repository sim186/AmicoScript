<script lang="ts">
  import { session } from "$lib/session.svelte";
  import NavRail from "./NavRail.svelte";
  import TopBar from "./TopBar.svelte";
  import Sidebar from "./Sidebar.svelte";
  import UpdateBanner from "./UpdateBanner.svelte";
  import ShortcutsOverlay from "./ShortcutsOverlay.svelte";
  import HelpModal from "./HelpModal.svelte";
  import ChangelogModal from "./ChangelogModal.svelte";
  import CommandPalette from "./CommandPalette.svelte";
  import ConsolePanel from "./ConsolePanel.svelte";
  import WatcherOnboardBanner from "../widgets/WatcherOnboardBanner.svelte";
  import WatcherUpdateBanner from "../widgets/WatcherUpdateBanner.svelte";
  import TranscribeTab from "../transcribe/TranscribeTab.svelte";
  import TranscriptTab from "../transcript/TranscriptTab.svelte";
  import LibraryTab from "../library/LibraryTab.svelte";
  import EntityEditModal from "../library/EntityEditModal.svelte";
  import AiHub from "../widgets/AiHub.svelte";
  import SettingsModal from "../settings/SettingsModal.svelte";
  import { openSettingsModal } from "$lib/settings.svelte";

  let { hfTokenPreview, hfTokenMasked }: { hfTokenPreview: string; hfTokenMasked: boolean } = $props();
</script>

<div class="flex h-full w-full overflow-hidden bg-canvas">
  <NavRail onOpenSettings={() => openSettingsModal()} />
  <Sidebar {hfTokenPreview} {hfTokenMasked} />

  <div class="flex h-full min-w-0 flex-1 flex-col">
    <UpdateBanner />
    <WatcherOnboardBanner />
    <WatcherUpdateBanner />
    <TopBar />

    <div class="flex min-h-0 flex-1">
      <div class="flex min-w-0 flex-1 flex-col overflow-hidden">
        {#if session.activeTab === "transcribe"}
          <TranscribeTab />
        {:else if session.activeTab === "transcript"}
          <TranscriptTab />
        {:else}
          <LibraryTab />
        {/if}
      </div>
      <AiHub />
    </div>
  </div>
</div>

<ConsolePanel />
<ShortcutsOverlay />
<HelpModal />
<ChangelogModal />
<CommandPalette />
<EntityEditModal />
<SettingsModal />
