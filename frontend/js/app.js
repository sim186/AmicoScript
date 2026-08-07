// AmicoScript frontend entry point.
//
// The UI used to be one 4,800-line <script> block inside index.html. It is
// now a set of ES modules, loaded natively by the browser — still no build
// step, no bundler, no dependencies.
//
// Modules are imported in the order the corresponding sections appeared in
// the original file, so top-level side effects (event listener registration)
// still run in the same sequence.

import { initAuth } from './auth.js';
import { initBackup } from './backup.js';
import { installAuthAwareFetch } from './http.js';
import { state } from './state.js';
import './version.js';
import { switchTab, toggleAiPanel } from './tabs.js';
import './upload.js';
import './jobs.js';
import { assignSpeakerToSelected, cancelSegmentEdit, clearSegmentSelection, escHtml, openSegmentEdit, promptRenameSpeaker, resetSegment, saveSegmentEdit, toggleSegmentSelection, toggleSpeakerFilter, translateSegment } from './transcript.js';
import './exports.js';
import './prefs.js';
import { selectFolder } from './folders.js';
import { clearTagFilter } from './tags.js';
import { bulkAssignTag, bulkDelete, bulkExport, bulkMoveToFolder, deleteRecordingConfirm, moveRecordingToFolder, retryRecording, saveAlias, toggleCardSelection, togglePopover, toggleRecordingTag, toggleSelectAll } from './library.js';
import './library-init.js';
import { toggleShortcutsOverlay } from './shortcuts.js';
import { pullLlmModel, saveLlmSettings, selectLlmModel } from './analysis.js';
import { closeDrawer, toggleDrawer } from './layout.js';
import './main.js';
import './changelog.js';
import { attachToJob, cancelQueuedJob, toggleQueuePanel } from './queue.js';
import './watcher.js';

// index.html wires buttons with inline on* attributes, which resolve against
// the global scope. Module scope is not global, so these are republished.
Object.assign(window, {
  state,
  switchTab, toggleAiPanel,
  assignSpeakerToSelected, cancelSegmentEdit, clearSegmentSelection, escHtml, openSegmentEdit, promptRenameSpeaker, resetSegment, saveSegmentEdit, toggleSegmentSelection, toggleSpeakerFilter, translateSegment,
  selectFolder,
  clearTagFilter,
  bulkAssignTag, bulkDelete, bulkExport, bulkMoveToFolder, deleteRecordingConfirm, moveRecordingToFolder, retryRecording, saveAlias, toggleCardSelection, togglePopover, toggleRecordingTag, toggleSelectAll,
  toggleShortcutsOverlay,
  pullLlmModel, saveLlmSettings, selectLlmModel,
  closeDrawer, toggleDrawer,
  attachToJob, cancelQueuedJob, toggleQueuePanel,
});

// Wrap fetch before anything issues a request, so a session that expires shows
// the login form instead of a page full of silently failing panels.
installAuthAwareFetch();

document.addEventListener('DOMContentLoaded', () => {
  initAuth();
  initBackup();
});
