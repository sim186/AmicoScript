// Application entry point.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { initAiAnalysis, saveLlmSettings } from './analysis.js';
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

// One failing widget used to take out every widget wired after it: init() was a
// straight line of calls, so a single throw left the rest of the page — the AI
// hub trigger among them — unbuilt, with nothing on screen to say why. Each
// step is now isolated and reports itself to the console.
function step(name, fn) {
  try {
    fn();
  } catch (err) {
    console.error(`[AmicoScript] init step "${name}" failed:`, err);
  }
}

export function init() {
  // First, not last: the AI hub is created from scratch by JS, so it is the
  // part of the UI most easily lost to an unrelated failure earlier in init.
  step('ai-hub', initAiHub);

  step('settings', restoreSettings);
  step('model-grid', () => {
    renderModelGrid();
    document.getElementById('model-select').addEventListener('change', e => selectModel(e.target.value));
  });
  step('drop-zone', initDropZone);
  step('diarize-toggle', initDiarizeToggle);
  step('cloud-power-toggle', initCloudPowerToggle);
  step('meeting-capture-toggle', initMeetingCaptureToggle);
  step('auto-summary-toggle', initAutoSummaryToggle);
  step('llm-setup', () => {
    initLlmSetup();
    // Picking a provider or granting cloud consent is a setting change like any
    // other, so persist it without making the user hunt for a save button.
    window.addEventListener('amicoscript:llm-config-changed', () => { saveLlmSettings(); });
  });
  step('audio-player', initAudioPlayer);
  step('export-buttons', initExportButtons);
  step('tag-suggest', initTagSuggest);
  step('ai-analysis', initAiAnalysis);
  step('shortcuts', initShortcuts);
  step('command-palette', initCommandPalette);

  step('language-select', () => {
    document.getElementById('language-select').addEventListener('change', e => {
      state.selectedLanguage = e.target.value;
      clientLog(`Language set: ${e.target.value || 'auto-detect'}`);
      localStorage.setItem('selectedLanguage', e.target.value);
      _saveTranscriptionDefaults();
    });
  });

  step('transcribe-buttons', () => {
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
  });

  step('console', () => {
    initConsole();
    // Initialise console height variable so .console-padded elements have
    // correct bottom padding before the user ever opens/closes the console.
    // The AI hub trigger and panel float above the console by reading it.
    requestAnimationFrame(() => {
      const h = document.getElementById('console-panel').offsetHeight;
      document.documentElement.style.setProperty('--console-h', `${h}px`);
    });
  });

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
  step('new-button', () => {
    document.getElementById('new-btn').addEventListener('click', _newBtnHandler);
  });

  step('transcript-search', () => {
    document.getElementById('search-input').addEventListener('input', e => {
      state.searchQuery = e.target.value;
      applyFilters();
    });
  });
  step('library', initLibrary);
  // fetch and display application version
  step('version', fetchVersion);
  // check for updates and show banner if available
  step('release-check', fetchLatestRelease);

  // attach changelog handlers (modal-based viewer)
  step('changelog-modal', function attachChangelogHandlers() {
    const btn = document.getElementById('changelog-view-btn');
    if (btn) btn.addEventListener('click', (e) => { e.preventDefault(); fetchAndShowChangelog(); });

    const closeBtn = document.getElementById('changelog-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', (e) => { e.preventDefault(); const modal = document.getElementById('changelog-modal'); if (modal) modal.classList.add('hidden'); });

    const overlay = document.getElementById('changelog-modal-overlay');
    if (overlay) overlay.addEventListener('click', () => { const modal = document.getElementById('changelog-modal'); if (modal) modal.classList.add('hidden'); });
  });

  // Help modal
  step('help-modal', function attachHelpHandlers() {
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
  });
}

window.addEventListener('beforeunload', () => {
  try {
    const token = state.exitToken || '';
    navigator.sendBeacon(`/api/exit?token=${encodeURIComponent(token)}`);
  } catch (e) {
    // best-effort only
  }
});
