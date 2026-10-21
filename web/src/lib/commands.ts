import { exportLibrary } from "./backup.svelte";
import { toggleDrawer } from "./session.svelte";
import { session, switchTab } from "./session.svelte";
import { openHelpModal, fetchAndShowChangelog, toggleShortcutsOverlay } from "./modals.svelte";
import { toggleQueuePanel } from "./queueWidget.svelte";
import { consoleState } from "./consoleLog.svelte";
import { openSettingsModal } from "./settings.svelte";

const NAVIGATE = "Navigate";
const ACTIONS = "Actions";
const VIEW = "View";

export interface Command {
  id: string;
  title: string;
  hint?: string;
  section: string;
  keywords: string;
  run: () => void;
  enabled?: () => boolean;
}

// Raw DOM lookups, kept local since the palette is the only caller that needs
// them outside a component. Deferred a frame: switching tabs only flips
// session.activeTab, and the element these target (inside the tab panel
// that {#if} block mounts) doesn't exist in the DOM until Svelte's next
// reactive flush has run.
function click(id: string): void {
  requestAnimationFrame(() => document.getElementById(id)?.click());
}

function focusEl(id: string): void {
  requestAnimationFrame(() => {
    const el = document.getElementById(id);
    if (!el) return;
    el.focus();
    el.scrollIntoView({ block: "center", behavior: "smooth" });
  });
}

const COMMANDS: Command[] = [
  {
    id: "go-transcribe",
    title: "Go to Transcribe",
    hint: "Upload and start a new transcription",
    section: NAVIGATE,
    keywords: "new upload home start",
    run: () => switchTab("transcribe"),
  },
  {
    id: "go-transcript",
    title: "Go to Transcript",
    hint: "The recording currently open",
    section: NAVIGATE,
    keywords: "segments player waveform",
    enabled: () => session.transcriptEnabled,
    run: () => switchTab("transcript"),
  },
  {
    id: "go-library",
    title: "Go to Library",
    hint: "Every recording, folder and tag",
    section: NAVIGATE,
    keywords: "recordings files browse",
    run: () => switchTab("library"),
  },
  {
    id: "choose-files",
    title: "Choose files to transcribe",
    hint: "Opens the file picker",
    section: ACTIONS,
    keywords: "upload open add audio video import",
    run: () => {
      switchTab("transcribe");
      click("file-input");
    },
  },
  {
    id: "record-meeting",
    title: "Record a meeting",
    hint: "Capture what is playing on this machine",
    section: ACTIONS,
    keywords: "capture microphone system audio call zoom meet",
    run: () => {
      openSettingsModal("general");
      focusEl("meeting-capture-toggle");
    },
  },
  {
    id: "open-settings",
    title: "Open Settings",
    hint: "Cloud Power, AI/LLM, Security, Backup and more",
    section: ACTIONS,
    keywords: "preferences config options gear",
    run: () => openSettingsModal(),
  },
  {
    id: "export-backup",
    title: "Export a library backup",
    hint: "Downloads every recording, transcript and analysis",
    section: ACTIONS,
    keywords: "backup download save archive zip",
    run: () => exportLibrary(true),
  },
  {
    id: "toggle-queue",
    title: "Toggle the job queue",
    section: VIEW,
    keywords: "jobs running progress batch",
    run: () => toggleQueuePanel(),
  },
  {
    id: "toggle-console",
    title: "Toggle the console",
    hint: "Logs from the running job",
    section: VIEW,
    keywords: "logs output debug terminal",
    run: () => {
      consoleState.open = !consoleState.open;
    },
  },
  {
    id: "toggle-sidebar",
    title: "Toggle the sidebar",
    section: VIEW,
    keywords: "drawer panel hide show",
    run: () => toggleDrawer(),
  },
  {
    id: "show-shortcuts",
    title: "Keyboard shortcuts",
    section: VIEW,
    keywords: "keys help bindings",
    run: () => toggleShortcutsOverlay(true),
  },
  {
    id: "show-help",
    title: "Help & terminal UI",
    hint: "How to run AmicoScript from a terminal",
    section: VIEW,
    keywords: "tui docs about command line",
    run: () => openHelpModal(),
  },
  {
    id: "show-changelog",
    title: "What's new",
    section: VIEW,
    keywords: "changelog release version updates",
    run: () => fetchAndShowChangelog(),
  },
];

export function commandHaystack(command: Command): string {
  return `${command.title} ${command.keywords || ""} ${command.section}`;
}

export function listCommands(): (Command & { disabled: boolean })[] {
  return COMMANDS.map((command) => ({
    ...command,
    disabled: typeof command.enabled === "function" && !command.enabled(),
  }));
}
