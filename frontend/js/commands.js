// What the command palette can do.
//
// One entry per action, kept apart from the palette widget for the same reason
// the TUI keeps commands.py apart from palette.py: this is a list of things the
// app can be asked to do, and it should be readable as one.
//
// Each command is { id, title, hint, section, keywords, run, enabled }.
// `enabled` (optional) is asked at open time — a command that cannot run right
// now is shown greyed rather than hidden, so the palette stays a map of the app
// instead of a list that changes shape under the user.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { exportLibrary } from './backup.js';
import { fetchAndShowChangelog } from './changelog.js';
import { selectFolder } from './folders.js';
import { toggleDrawer } from './layout.js';
import { fetchLibrary } from './library.js';
import { toggleQueuePanel } from './queue.js';
import { toggleShortcutsOverlay } from './shortcuts.js';
import { state } from './state.js';
import { switchTab, toggleAiPanel } from './tabs.js';
import { clearTagFilter } from './tags.js';
import { updateTuiCommand } from './upload.js';

const NAVIGATE = 'Navigate';
const ACTIONS = 'Actions';
const VIEW = 'View';

function click(id) {
  document.getElementById(id)?.click();
}

function focus(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.focus();
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

const COMMANDS = [
  {
    id: 'go-transcribe',
    title: 'Go to Transcribe',
    hint: 'Upload and start a new transcription',
    section: NAVIGATE,
    keywords: 'new upload home start',
    run: () => switchTab('transcribe'),
  },
  {
    id: 'go-transcript',
    title: 'Go to Transcript',
    hint: 'The recording currently open',
    section: NAVIGATE,
    keywords: 'segments player waveform',
    enabled: () => !!state.result,
    run: () => switchTab('transcript'),
  },
  {
    id: 'go-library',
    title: 'Go to Library',
    hint: 'Every recording, folder and tag',
    section: NAVIGATE,
    keywords: 'recordings files browse',
    run: () => switchTab('library'),
  },
  {
    id: 'ask-library',
    title: 'Ask your library',
    hint: 'A question answered from every transcript, with citations',
    section: ACTIONS,
    keywords: 'chat question llm ai answer',
    run: () => {
      switchTab('library');
      focus('lib-chat-input');
    },
  },
  {
    id: 'choose-files',
    title: 'Choose files to transcribe',
    hint: 'Opens the file picker',
    section: ACTIONS,
    keywords: 'upload open add audio video import',
    run: () => {
      switchTab('transcribe');
      click('file-input');
    },
  },
  {
    id: 'record-meeting',
    title: 'Record a meeting',
    hint: 'Capture what is playing on this machine',
    section: ACTIONS,
    keywords: 'capture microphone system audio call zoom meet',
    run: () => {
      switchTab('transcribe');
      focus('meeting-capture-toggle');
    },
  },
  {
    id: 'clear-filters',
    title: 'Show all recordings',
    hint: 'Clears the folder, tag and search filters',
    section: ACTIONS,
    keywords: 'reset clear filter folder tag everything',
    run: () => {
      clearTagFilter();
      selectFolder(null);
      fetchLibrary();
      switchTab('library');
    },
  },
  {
    id: 'export-backup',
    title: 'Export a library backup',
    hint: 'Downloads every recording, transcript and analysis',
    section: ACTIONS,
    keywords: 'backup download save archive zip',
    run: () => exportLibrary(),
  },
  {
    id: 'toggle-ai',
    title: 'Toggle the AI analysis panel',
    section: VIEW,
    keywords: 'summary summarize translate llm',
    enabled: () => state.activeTab === 'transcript',
    run: () => toggleAiPanel(),
  },
  {
    id: 'toggle-queue',
    title: 'Toggle the job queue',
    section: VIEW,
    keywords: 'jobs running progress batch',
    run: () => toggleQueuePanel(),
  },
  {
    id: 'toggle-console',
    title: 'Toggle the console',
    hint: 'Logs from the running job',
    section: VIEW,
    keywords: 'logs output debug terminal',
    run: () => click('console-toggle'),
  },
  {
    id: 'toggle-sidebar',
    title: 'Toggle the sidebar',
    section: VIEW,
    keywords: 'drawer panel hide show',
    run: () => toggleDrawer(),
  },
  {
    id: 'show-shortcuts',
    title: 'Keyboard shortcuts',
    section: VIEW,
    keywords: 'keys help bindings',
    run: () => toggleShortcutsOverlay(true),
  },
  {
    id: 'show-help',
    title: 'Help & terminal UI',
    hint: 'How to run AmicoScript from a terminal',
    section: VIEW,
    keywords: 'tui docs about command line',
    run: () => {
      document.getElementById('help-modal')?.classList.remove('hidden');
      updateTuiCommand();
    },
  },
  {
    id: 'show-changelog',
    title: "What's new",
    section: VIEW,
    keywords: 'changelog release version updates',
    run: () => fetchAndShowChangelog(),
  },
];

// The text a query is matched against: everything the user might reasonably
// type to mean this command.
export function commandHaystack(command) {
  return `${command.title} ${command.keywords || ''} ${command.section}`;
}

export function listCommands() {
  return COMMANDS.map(command => ({
    ...command,
    disabled: typeof command.enabled === 'function' && !command.enabled(),
  }));
}
