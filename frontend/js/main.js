// Application entry point.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { saveLlmSettings } from './analysis.js';
import { fetchAndShowChangelog } from './changelog.js';
import { initCommandPalette } from './command-palette.js';
import { initExportButtons } from './exports.js';
import { cancelJob } from './jobs.js';
import { initAiHub } from './ai-hub.js';
import { initLibrary } from './library-init.js';
import { initLlmSetup } from './llm-setup.js';
import { _saveTranscriptionDefaults, initAutoSummaryToggle, restoreSettings } from './prefs.js';
import { initShortcuts } from './shortcuts.js';
import { state } from './state.js';
import { renderModelGrid, selectModel, switchTab } from './tabs.js';
import { initTagSuggest } from './tag-suggest.js';
import { applyFilters, initAudioPlayer } from './transcript.js';
import { clearFile, clientLog, copyTuiCommand, initCloudPowerToggle, initConsole, initDiarizeToggle, initDropZone, initMeetingCaptureToggle, renderBatchList, resetJobLogs, startTranscription, updateTuiCommand } from './upload.js';
import { fetchLatestRelease, fetchVersion } from './version.js';

export function init() {
  restoreSettings();
  renderModelGrid();
  document.getElementById('model-select').addEventListener('change', e => selectModel(e.target.value));
  initDropZone();
  initDiarizeToggle();
  initCloudPowerToggle();
  initMeetingCaptureToggle();
  initAutoSummaryToggle();
  initLlmSetup();
  // Picking a provider or granting cloud consent is a setting change like any
  // other, so persist it without making the user hunt for a save button.
  window.addEventListener('amicoscript:llm-config-changed', () => { saveLlmSettings(); });
  initAudioPlayer();
  initExportButtons();
  initTagSuggest();
  initAiHub();
  initShortcuts();
  initCommandPalette();

  document.getElementById('language-select').addEventListener('change', e => {
    state.selectedLanguage = e.target.value;
    clientLog(`Language set: ${e.target.value || 'auto-detect'}`);
    localStorage.setItem('selectedLanguage', e.target.value);
    _saveTranscriptionDefaults();
  });

  document.getElementById('start-btn').addEventListener('click', startTranscription);
  document.getElementById('cancel-btn').addEventListener('click', cancelJob);

  document.getElementById('batch-clear-all-btn').addEventListener('click', () => {
    state.batchQueue = state.batchQueue.filter(i => i.status !== 'pending');
    if (state.batchQueue.length === 0) {
      state.batchMode = false;
      document.getElementById('batch-panel').classList.add('hidden');
      const btn = document.getElementById('start-btn');
      btn.disabled = true;
      btn.textContent = 'Start transcription';
    } else {
      document.getElementById('start-btn').textContent = `Transcribe ${state.batchQueue.length} file${state.batchQueue.length !== 1 ? 's' : ''}`;
      renderBatchList();
    }
  });

  document.getElementById('batch-new-btn').addEventListener('click', () => {
    state.batchQueue = [];
    state.batchMode = false;
    document.getElementById('batch-summary').classList.add('hidden');
    const btn = document.getElementById('start-btn');
    btn.disabled = true;
    btn.textContent = 'Start transcription';
    clearFile();
  });
  initConsole();
  // Initialise console height variable so .console-padded elements have
  // correct bottom padding before the user ever opens/closes the console.
  requestAnimationFrame(() => {
    const h = document.getElementById('console-panel').offsetHeight;
    document.documentElement.style.setProperty('--console-h', `${h}px`);
  });
  // AI hub already initialized above

  // AI panel closed by default

  const _newBtnHandler = () => {
    state.result = null;
    state.jobId = null;
    state.recordingId = null;
    state.activeRecordingId = null;
    state.jobStatus = null;
    state.audioUrl = null;
    state.currentSegmentId = null;
    state.selectedFile = null;
    if (state.wavesurfer) { state.wavesurfer.destroy(); state.wavesurfer = null; }
    resetJobLogs();
    clearFile();
    const transcriptBtn = document.getElementById('tab-btn-transcript');
    transcriptBtn.disabled = true;
    switchTab('transcribe');
  };
  document.getElementById('new-btn').addEventListener('click', _newBtnHandler);

  document.getElementById('search-input').addEventListener('input', e => {
    state.searchQuery = e.target.value;
    applyFilters();
  });
  initLibrary();
  // fetch and display application version
  fetchVersion();
  // check for updates and show banner if available
  fetchLatestRelease();

  // attach changelog handlers (modal-based viewer)
  (function attachChangelogHandlers() {
    const btn = document.getElementById('changelog-view-btn');
    if (btn) btn.addEventListener('click', (e) => { e.preventDefault(); fetchAndShowChangelog(); });

    const closeBtn = document.getElementById('changelog-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', (e) => { e.preventDefault(); const modal = document.getElementById('changelog-modal'); if (modal) modal.classList.add('hidden'); });

    const overlay = document.getElementById('changelog-modal-overlay');
    if (overlay) overlay.addEventListener('click', () => { const modal = document.getElementById('changelog-modal'); if (modal) modal.classList.add('hidden'); });
  })();

  // Help modal
  (function attachHelpHandlers() {
    const btn = document.getElementById('help-view-btn');
    if (btn) btn.addEventListener('click', () => {
      clientLog('Help modal opened');
      document.getElementById('help-modal').classList.remove('hidden');
      updateTuiCommand();
    });
    const closeBtn = document.getElementById('help-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', () => document.getElementById('help-modal').classList.add('hidden'));
    const overlay = document.getElementById('help-modal-overlay');
    if (overlay) overlay.addEventListener('click', () => document.getElementById('help-modal').classList.add('hidden'));

    const copyBtn = document.getElementById('tui-copy-btn');
    if (copyBtn) copyBtn.addEventListener('click', copyTuiCommand);
  })();
}

window.addEventListener('beforeunload', () => {
  try {
    const token = state.exitToken || '';
    navigator.sendBeacon(`/api/exit?token=${encodeURIComponent(token)}`);
  } catch (e) {
    // best-effort only
  }
});
