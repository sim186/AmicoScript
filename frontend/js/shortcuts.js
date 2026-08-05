// Keyboard shortcuts.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { state } from './state.js';
import { openSegmentEdit } from './transcript.js';

export function toggleShortcutsOverlay(show) {
  const overlay = document.getElementById('shortcuts-overlay');
  if (typeof show === 'boolean') {
    overlay.classList.toggle('hidden', !show);
    return;
  }
  overlay.classList.toggle('hidden');
}

export function initShortcuts() {
  window.addEventListener('keydown', e => {
    // Ignore global shortcuts when a text or form control has focus
    const active = document.activeElement;
    if (active && (
      active.tagName === 'INPUT' ||
      active.tagName === 'TEXTAREA' ||
      active.tagName === 'SELECT' ||
      active.tagName === 'BUTTON' ||
      active.isContentEditable
    )) {
      return;
    }

    const isTranscriptTab = state.activeTab === 'transcript' && state.result;

    switch (e.key.toLowerCase()) {
      case ' ':
        e.preventDefault();
        if (state.wavesurfer) state.wavesurfer.playPause();
        break;
      case 'j':
        if (!isTranscriptTab || !state.wavesurfer) return;
        state.wavesurfer.setTime(Math.max(0, state.wavesurfer.getCurrentTime() - 5));
        break;
      case 'k':
        if (!isTranscriptTab || !state.wavesurfer) return;
        state.wavesurfer.setTime(Math.min(state.wavesurfer.getDuration() || 0, state.wavesurfer.getCurrentTime() + 5));
        break;
      case 'e':
        if (!isTranscriptTab || !state.currentSegmentId) return;
        e.preventDefault();
        openSegmentEdit(state.currentSegmentId);
        break;
      case '?':
        toggleShortcutsOverlay();
        break;
      case 'escape':
        toggleShortcutsOverlay(false);
        break;
    }
  });
}
