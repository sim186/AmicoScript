// Wiring for the library view: filters, sorting, pagination.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { fetchFolders, selectFolder } from './folders.js';
import { fetchLibrary, moveRecordingToFolder } from './library.js';
import { initGlobalSearch } from './search.js';
import { attachPaletteToInput } from './state.js';
import { fetchTags, filterByTag } from './tags.js';
import { renderBatchList } from './upload.js';

export function initLibrary() {
  fetchFolders();
  fetchTags();

  // Replace native color inputs with the fixed PALETTE UI (hidden input + palette buttons)
  try {
    attachPaletteToInput('new-folder-color', 28);
    attachPaletteToInput('new-tag-color', 28);
    attachPaletteToInput('edit-tag-color', 28);
  } catch (e) { /* best-effort; continue if DOM differs */ }

  // Modal controls: save/cancel/close
  const modalCloseBtn = document.getElementById('entity-edit-close');
  const modalCancelBtn = document.getElementById('entity-edit-cancel');
  const modalSaveBtn = document.getElementById('entity-edit-save');
  if (modalCloseBtn) modalCloseBtn.addEventListener('click', (e) => { e.stopPropagation(); window.hideEntityDialog(); });
  if (modalCancelBtn) modalCancelBtn.addEventListener('click', (e) => { e.stopPropagation(); window.hideEntityDialog(); });
  if (modalSaveBtn) modalSaveBtn.addEventListener('click', async (e) => { e.stopPropagation(); await window.saveEntityDialog(); });

  // keyboard: close modal with Escape, save with Enter when name input focused
  document.addEventListener('keydown', (e) => {
    const modal = document.getElementById('entity-edit-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    if (e.key === 'Escape') { e.preventDefault(); window.hideEntityDialog(); }
    if (e.key === 'Enter' && document.activeElement && document.activeElement.id === 'entity-edit-name') { e.preventDefault(); window.saveEntityDialog(); }
  });

  // Mic recording modal
  (function () {
    let mediaRecorder = null;
    let recordedChunks = [];
    let timerInterval = null;
    let startTime = 0;
    let pausedElapsed = 0;
    let pausedAt = null;

    function micModalState(state) {
      document.getElementById('mic-state-idle').classList.toggle('hidden', state !== 'idle');
      document.getElementById('mic-state-recording').classList.toggle('hidden', state !== 'recording');
      document.getElementById('mic-state-review').classList.toggle('hidden', state !== 'review');
    }

    function formatDuration(ms) {
      const total = Math.floor(ms / 1000);
      const m = Math.floor(total / 60);
      const s = total % 60;
      return `${m}:${s.toString().padStart(2, '0')}`;
    }

    function openMicModal() {
      micModalState('idle');
      document.getElementById('mic-record-modal').classList.remove('hidden');
      document.body.style.overflow = 'hidden';
    }

    function closeMicModal() {
      stopRecording(false);
      document.getElementById('mic-record-modal').classList.add('hidden');
      document.body.style.overflow = '';
    }

    function stopRecording(keepData) {
      if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
      if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        if (!keepData) {
          mediaRecorder.ondataavailable = null;
          mediaRecorder.onstop = null;
        }
        mediaRecorder.stop();
      }
      if (!keepData) {
        mediaRecorder = null;
        recordedChunks = [];
      }
      if (mediaRecorder && mediaRecorder.stream) {
        mediaRecorder.stream.getTracks().forEach(t => t.stop());
      }
    }

    async function startRecording() {
      recordedChunks = [];
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (err) {
        alert('Microphone access denied or unavailable.');
        return;
      }

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : '';
      mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
      mediaRecorder.ondataavailable = e => { if (e.data.size > 0) recordedChunks.push(e.data); };
      mediaRecorder.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        const duration = pausedElapsed + (pausedAt ? 0 : Date.now() - startTime);
        document.getElementById('mic-review-duration').textContent = formatDuration(duration);
        micModalState('review');
      };

      mediaRecorder.start();
      startTime = Date.now();
      micModalState('recording');
      document.getElementById('mic-pause-btn').textContent = 'Pause';
      document.getElementById('mic-status-label').textContent = 'Recording in progress…';
      document.getElementById('mic-ping').classList.remove('hidden');
      document.getElementById('mic-dot').className = 'w-4 h-4 rounded-full bg-red-500';

      pausedElapsed = 0;
      pausedAt = null;
      timerInterval = setInterval(() => {
        const elapsed = pausedElapsed + (pausedAt ? 0 : Date.now() - startTime);
        document.getElementById('mic-timer').textContent = formatDuration(elapsed);
      }, 500);
    }

    function useRecording() {
      const blob = new Blob(recordedChunks, { type: mediaRecorder?.mimeType || 'audio/webm' });
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const ext = (blob.type.includes('ogg') || blob.type.includes('opus')) ? 'ogg' : 'webm';
      const file = new File([blob], `recording-${ts}.${ext}`, { type: blob.type });

      const dup = state.batchQueue.some(i => i.file.name === file.name);
      if (!dup) state.batchQueue.push({ file, jobId: null, recordingId: null, status: 'pending', progress: 0, message: '' });

      state.batchMode = true;
      document.getElementById('batch-panel').classList.remove('hidden');
      document.getElementById('file-info').classList.add('hidden');
      document.getElementById('batch-summary').classList.add('hidden');
      const startBtn = document.getElementById('start-btn');
      if (startBtn) {
        startBtn.disabled = false;
        startBtn.textContent = `Transcribe ${state.batchQueue.length} file${state.batchQueue.length !== 1 ? 's' : ''}`;
      }
      renderBatchList();

      mediaRecorder = null;
      recordedChunks = [];
      closeMicModal();
    }

    document.getElementById('record-mic-btn').addEventListener('click', e => { e.stopPropagation(); openMicModal(); });
    document.getElementById('mic-modal-close').addEventListener('click', closeMicModal);
    document.getElementById('mic-modal-backdrop').addEventListener('click', closeMicModal);
    document.getElementById('mic-cancel-idle').addEventListener('click', closeMicModal);
    document.getElementById('mic-start-btn').addEventListener('click', startRecording);
    document.getElementById('mic-pause-btn').addEventListener('click', () => {
      if (!mediaRecorder) return;
      if (mediaRecorder.state === 'recording') {
        mediaRecorder.pause();
        pausedAt = Date.now();
        document.getElementById('mic-pause-btn').textContent = 'Resume';
        document.getElementById('mic-status-label').textContent = 'Paused';
        document.getElementById('mic-ping').classList.add('hidden');
        document.getElementById('mic-dot').classList.remove('bg-red-500');
        document.getElementById('mic-dot').classList.add('bg-slate-400');
      } else if (mediaRecorder.state === 'paused') {
        mediaRecorder.resume();
        pausedElapsed += Date.now() - pausedAt;
        pausedAt = null;
        document.getElementById('mic-pause-btn').textContent = 'Pause';
        document.getElementById('mic-status-label').textContent = 'Recording in progress…';
        document.getElementById('mic-ping').classList.remove('hidden');
        document.getElementById('mic-dot').classList.add('bg-red-500');
        document.getElementById('mic-dot').classList.remove('bg-slate-400');
      }
    });
    document.getElementById('mic-stop-btn').addEventListener('click', () => {
      if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
      if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
    });
    document.getElementById('mic-discard-btn').addEventListener('click', () => {
      mediaRecorder = null;
      recordedChunks = [];
      pausedElapsed = 0;
      pausedAt = null;
      micModalState('idle');
    });
    document.getElementById('mic-use-btn').addEventListener('click', useRecording);

    document.addEventListener('keydown', (e) => {
      const modal = document.getElementById('mic-record-modal');
      if (!modal || modal.classList.contains('hidden')) return;
      if (e.key === 'Escape') { e.preventDefault(); closeMicModal(); }
    });
  })();

  // New folder / tag — open dialog
  document.getElementById('new-folder-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    window.showEntityDialog('folder', null, state.library.selectedFolderId);
  });
  document.getElementById('new-tag-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    window.showEntityDialog('tag', null);
  });

  document.getElementById('lib-sort-select').addEventListener('change', e => {
    state.library.sort = e.target.value;
    fetchLibrary();
  });

  document.getElementById('lib-sort-order').addEventListener('click', () => {
    state.library.order = state.library.order === 'desc' ? 'asc' : 'desc';
    document.getElementById('lib-sort-asc-icon').classList.toggle('hidden', state.library.order !== 'asc');
    document.getElementById('lib-sort-desc-icon').classList.toggle('hidden', state.library.order !== 'desc');
    fetchLibrary();
  });

  // Delegated folder clicks (ignore clicks on arrow, inputs, popovers)
  const sidebarLib = document.getElementById('sidebar-library');
  if (sidebarLib) {
    sidebarLib.addEventListener('click', (e) => {
      // if click is on the expand arrow or on interactive elements, ignore here
      if (e.target.closest('.folder-arrow') || e.target.closest('.popover') || e.target.closest('input') || e.target.closest('button')) return;
      const item = e.target.closest('.folder-item');
      if (!item) return;
      if (item.id === 'folder-all') selectFolder(null);
      else if (item.dataset && item.dataset.folderId) selectFolder(item.dataset.folderId);
    });

    // allow dropping recordings on the root "All recordings"
    const folderAll = document.getElementById('folder-all');
    if (folderAll) {
      folderAll.addEventListener('dragover', (e) => { e.preventDefault(); folderAll.classList.add('drag-over'); });
      folderAll.addEventListener('dragleave', () => folderAll.classList.remove('drag-over'));
      folderAll.addEventListener('drop', (e) => {
        e.preventDefault(); folderAll.classList.remove('drag-over');
        const recId = e.dataTransfer.getData('text/plain');
        if (recId) moveRecordingToFolder(recId, '');
      });
    }
  }

  // Delegated tag clicks inside recording cards (capture phase to intercept before card click)
  const libraryListEl = document.getElementById('library-list');
  if (libraryListEl) {
    libraryListEl.addEventListener('click', (e) => {
      const pill = e.target.closest('.tag-pill');
      if (!pill) return;
      // ignore tag clicks coming from popover dropdowns (assign-tags UI)
      if (pill.closest('.popover')) return;
      e.stopPropagation();
      const tagId = pill.dataset.tagId;
      const tagName = pill.dataset.tagName || pill.textContent;
      filterByTag(tagId, tagName);
    }, true);
  }

  initGlobalSearch();
}

// Importing a backup replaces what the list is showing, so redraw it.
window.addEventListener('amicoscript:library-changed', () => {
  fetchLibrary();
});
