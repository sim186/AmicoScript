// Drop zone, file/URL intake and starting a transcription job.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { hideError, showError } from './exports.js';
import { connectSSE, loadResult } from './jobs.js';
import { _saveHfTokenDebounced, _saveTranscriptionDefaults } from './prefs.js';
import { state } from './state.js';
import { escHtml } from './transcript.js';

export function initDropZone() {
  const zone = document.getElementById('drop-zone');
  const input = document.getElementById('file-input');
  const overlay = document.getElementById('drop-overlay');

  zone.addEventListener('click', () => input.click());
  zone.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') input.click(); });
  input.addEventListener('change', () => {
    if (!input.files.length) return;
    if (input.files.length === 1) handleFile(input.files[0]);
    else handleFiles(Array.from(input.files));
  });

  zone.addEventListener('dragover', e => {
    e.preventDefault();
    zone.classList.add('border-brand', 'bg-brand-muted/20');

    if (overlay) {
      overlay.style.opacity = '1';
      overlay.style.transform = 'scale(1)';
    }
  });
  zone.addEventListener('dragleave', e => {
    if (!zone.contains(e.relatedTarget)) {
      zone.classList.remove('border-brand', 'bg-brand-muted/20');

      if (overlay) {
        overlay.style.opacity = '0';
        overlay.style.transform = 'scale(0.98)';
      }
    }
  });
  zone.addEventListener('drop', async e => {
    e.preventDefault();
    zone.classList.remove('border-brand', 'bg-brand-muted/20');

    if (overlay) {
      overlay.style.opacity = '0';
      overlay.style.transform = 'scale(0.98)';
    }

    zone.classList.add('ring-2', 'ring-green-400');
    setTimeout(() => {
      zone.classList.remove('ring-2', 'ring-green-400');
    }, 300);

    const droppedText = (e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain') || '').trim();
    if (!e.dataTransfer.files.length && isLikelyUrl(droppedText)) {
      startUrlTranscription(droppedText);
      return;
    }

    const items = Array.from(e.dataTransfer.items);
    // Check if any item is a directory entry
    const hasEntry = items.length && items[0].webkitGetAsEntry;
    if (hasEntry) {
      const entries = items.map(i => i.webkitGetAsEntry()).filter(Boolean);
      const hasDir = entries.some(en => en.isDirectory);
      if (hasDir) {
        // Recursively collect all files from all entries
        const files = await collectFilesFromEntries(entries);
        if (files.length === 1) handleFile(files[0]);
        else handleFiles(files);
        return;
      }
    }
    const files = Array.from(e.dataTransfer.files);
    if (files.length === 1) handleFile(files[0]);
    else if (files.length > 1) handleFiles(files);
  });

  zone.addEventListener('paste', e => {
    const text = (e.clipboardData?.getData('text/plain') || '').trim();
    if (isLikelyUrl(text)) {
      e.preventDefault();
      startUrlTranscription(text);
    }
  });

  document.getElementById('folder-btn').addEventListener('click', e => {
    e.stopPropagation();
    document.getElementById('folder-input').click();
  });
  document.getElementById('paste-link-btn').addEventListener('click', e => {
    e.stopPropagation();
    const value = window.prompt('Paste a video URL (YouTube, TikTok, Instagram, Facebook, X, Vimeo, Twitch...)', '');
    if (!value) return;
    startUrlTranscription(value);
  });
  document.getElementById('folder-input').addEventListener('change', () => {
    const files = Array.from(document.getElementById('folder-input').files);
    if (!files.length) return;
    if (files.length === 1) handleFile(files[0]);
    else handleFiles(files);
    document.getElementById('folder-input').value = '';
  });

  document.getElementById('clear-file-btn').addEventListener('click', e => {
    e.stopPropagation();
    clearFile();
  });
}

function collectFilesFromEntries(entries) {
  return new Promise(resolve => {
    const files = [];
    let pending = 0;

    function readEntry(entry) {
      if (entry.isFile) {
        pending++;
        entry.file(file => {
          files.push(file);
          pending--;
          if (pending === 0) resolve(files);
        });
      } else if (entry.isDirectory) {
        pending++;
        const reader = entry.createReader();
        function readBatch() {
          reader.readEntries(batch => {
            if (batch.length === 0) {
              pending--;
              if (pending === 0) resolve(files);
              return;
            }
            batch.forEach(readEntry);
            readBatch(); // keep reading until exhausted (>100 entries)
          });
        }
        readBatch();
      }
    }

    if (entries.length === 0) { resolve(files); return; }
    entries.forEach(readEntry);
  });
}

function handleFile(file) {
  // If batch mode is already active, append this file to the batch
  if (state.batchMode) { handleFiles([file]); return; }
  // If a file is already staged, switch to batch keeping both
  if (state.selectedFile) {
    const prev = state.selectedFile;
    state.selectedFile = null;
    document.getElementById('file-info').classList.add('hidden');
    handleFiles([prev, file]);
    return;
  }
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['mp3', 'wav', 'm4a', 'ogg', 'flac', 'mp4', 'mov', 'mkv', 'opus'].includes(ext)) {
    clientLog(`Unsupported file: ${file.name}`, 'WARN');
    showError(`Unsupported file type: .${ext}`);
    return;
  }
  clientLog(`File selected: ${file.name} (${fmtBytes(file.size)})`);
  hideError();
  state.selectedFile = file;
  document.getElementById('file-name').textContent = file.name;
  document.getElementById('file-size').textContent = fmtBytes(file.size);
  document.getElementById('file-info').classList.remove('hidden');
  document.getElementById('start-btn').disabled = false;
}

function handleFiles(files) {
  const ALLOWED = ['mp3', 'wav', 'm4a', 'ogg', 'flac', 'mp4', 'mov', 'mkv', 'opus'];
  const valid = files.filter(f => ALLOWED.includes(f.name.split('.').pop().toLowerCase()));
  const skipped = files.length - valid.length;
  if (valid.length === 0) { clientLog('Batch: no supported files found', 'WARN'); showError('No supported files found.'); return; }
  clientLog(`Batch: ${valid.length} file(s) queued${skipped ? `, ${skipped} skipped` : ''}`);
  hideError();
  if (skipped > 0) showError(`${skipped} file(s) skipped — unsupported format.`);

  state.batchMode = true;
  valid.forEach(file => {
    const dup = state.batchQueue.some(i => i.file.name === file.name && i.file.size === file.size);
    if (!dup) state.batchQueue.push({ file, jobId: null, recordingId: null, status: 'pending', progress: 0, message: '' });
  });

  document.getElementById('file-info').classList.add('hidden');
  document.getElementById('batch-summary').classList.add('hidden');
  document.getElementById('batch-panel').classList.remove('hidden');
  const btn = document.getElementById('start-btn');
  btn.disabled = false;
  btn.textContent = `Transcribe ${state.batchQueue.length} file${state.batchQueue.length !== 1 ? 's' : ''}`;
  renderBatchList();
}

export function renderBatchList() {
  const ul = document.getElementById('batch-list');
  const label = document.getElementById('batch-count-label');
  const total = state.batchQueue.length;
  const pending = state.batchQueue.filter(i => i.status === 'pending').length;
  label.textContent = `${total} file${total !== 1 ? 's' : ''} \u00b7 ${pending} pending`;

  const statusColors = {
    pending: 'bg-slate-100 text-slate-500',
    uploading: 'bg-blue-100 text-blue-600',
    queued: 'bg-amber-100 text-amber-700',
    processing: 'bg-brand-muted text-brand',
    done: 'bg-emerald-100 text-emerald-700',
    error: 'bg-red-100 text-red-600',
  };

  const frag = document.createDocumentFragment();
  state.batchQueue.forEach((item, idx) => {
    const pct = Math.round(item.progress * 100);
    const uploadPct = item.status === 'uploading' ? Math.round((item.progress / 0.05)) : 0;
    const label = item.status === 'processing' ? `${pct}%` :
      item.status === 'uploading' ? `↑ ${uploadPct}%` :
        item.status.charAt(0).toUpperCase() + item.status.slice(1);
    const li = document.createElement('li');
    li.className = 'flex items-center gap-2 bg-white rounded-lg border border-slate-200 px-3 py-2 text-sm';
    li.innerHTML = `
      <span class="flex-1 truncate font-medium text-slate-700" title="${escHtml(item.file.name)}">${escHtml(item.file.name)}</span>
      <span class="text-xs text-slate-400 shrink-0">${fmtBytes(item.file.size)}</span>
      <span class="shrink-0 px-1.5 py-0.5 rounded text-xs font-medium ${statusColors[item.status] || statusColors.pending}">${label}</span>
      ${item.status === 'uploading' ? `<div class="shrink-0 w-14 bg-slate-100 rounded-full h-1"><div class="bg-blue-400 h-1 rounded-full transition-all" style="width:${uploadPct}%"></div></div>` : ''}
      ${item.status === 'processing' ? `<div class="shrink-0 w-14 bg-slate-100 rounded-full h-1"><div class="bg-brand h-1 rounded-full transition-all" style="width:${pct}%"></div></div>` : ''}
      ${item.status === 'done' ? `<a href="#" data-batch-view="${idx}" class="shrink-0 text-xs text-brand hover:underline focus:outline-none">View</a>` : ''}
      ${item.status === 'pending' ? `<button data-batch-remove="${idx}" class="shrink-0 text-slate-300 hover:text-red-400 transition focus:outline-none" aria-label="Remove">
        <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
        </svg>
      </button>` : ''}
    `;
    frag.appendChild(li);
  });
  ul.innerHTML = '';
  ul.appendChild(frag);

  ul.querySelectorAll('[data-batch-remove]').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.batchRemove, 10);
      state.batchQueue.splice(idx, 1);
      if (state.batchQueue.length === 0) {
        state.batchMode = false;
        document.getElementById('batch-panel').classList.add('hidden');
        const sb = document.getElementById('start-btn');
        sb.disabled = true;
        sb.textContent = 'Start transcription';
      } else {
        document.getElementById('start-btn').textContent = `Transcribe ${state.batchQueue.length} file${state.batchQueue.length !== 1 ? 's' : ''}`;
        renderBatchList();
      }
    });
  });

  ul.querySelectorAll('[data-batch-view]').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      const item = state.batchQueue[parseInt(a.dataset.batchView, 10)];
      if (item?.jobId) loadResult(item.jobId, item.recordingId);
    });
  });
}

export function clearFile() {
  state.selectedFile = null;
  if (state.batchMode) {
    state.batchMode = false;
    state.batchQueue = [];
    document.getElementById('batch-panel').classList.add('hidden');
    document.getElementById('batch-summary').classList.add('hidden');
  }
  document.getElementById('file-info').classList.add('hidden');
  const btn = document.getElementById('start-btn');
  btn.disabled = true;
  btn.textContent = 'Start transcription';
  document.getElementById('file-input').value = '';
  hideError();
}

function fmtBytes(b) {
  return b < 1048576 ? (b / 1024).toFixed(0) + ' KB' : (b / 1048576).toFixed(1) + ' MB';
}

function isLikelyUrl(value) {
  if (!value) return false;
  try {
    const u = new URL(value.trim());
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch (_) {
    return false;
  }
}

export function initDiarizeToggle() {
  const btn = document.getElementById('diarize-toggle');
  if (btn) {
    btn.addEventListener('click', () => {
      state.diarize = !state.diarize;
      clientLog(`Diarization ${state.diarize ? 'enabled' : 'disabled'}`);
      btn.setAttribute('aria-checked', String(state.diarize));
      const dot = btn.querySelector('span');
      if (dot) dot.style.transform = state.diarize ? 'translateX(16px)' : 'translateX(0)';
      btn.classList.toggle('bg-brand', state.diarize);
      btn.classList.toggle('bg-slate-200', !state.diarize);
      const hfSection = document.getElementById('hf-section');
      if (hfSection) hfSection.classList.toggle('hidden', !state.diarize);
      localStorage.setItem('diarize', String(state.diarize));
      _saveTranscriptionDefaults();
    });
  }

  const hfInput = document.getElementById('hf-token-input');
  if (hfInput) {
    hfInput.addEventListener('input', e => {
      state.hfToken = e.target.value;
      _saveHfTokenDebounced(e.target.value);
    });
  }

  const hfEyeBtn = document.getElementById('hf-eye-btn');
  if (hfEyeBtn) {
    hfEyeBtn.addEventListener('click', () => {
      const inp = document.getElementById('hf-token-input');
      if (!inp) return;
      const show = inp.type === 'password';
      inp.type = show ? 'text' : 'password';
      const openEye = document.getElementById('hf-eye-open');
      const closedEye = document.getElementById('hf-eye-closed');
      if (openEye) openEye.classList.toggle('hidden', show);
      if (closedEye) closedEye.classList.toggle('hidden', !show);
    });
  }

  ['num-speakers-input', 'min-speakers-input', 'max-speakers-input'].forEach(id => {
    const inp = document.getElementById(id);
    if (inp) {
      inp.addEventListener('input', e => {
        const prop = id.split('-')[0] + id.split('-')[1].charAt(0).toUpperCase() + id.split('-')[1].slice(1);
        // wait, state properties are numSpeakers, minSpeakers, maxSpeakers
        if (id === 'num-speakers-input') state.numSpeakers = e.target.value;
        if (id === 'min-speakers-input') state.minSpeakers = e.target.value;
        if (id === 'max-speakers-input') state.maxSpeakers = e.target.value;
      });
    }
  });
}

export function initCloudPowerToggle() {
  const btn = document.getElementById('cloud-power-toggle');
  if (!btn) return;
  btn.addEventListener('click', () => {
    state.cloudPower = !state.cloudPower;
    btn.setAttribute('aria-checked', String(state.cloudPower));
    const dot = btn.querySelector('span');
    if (dot) dot.style.transform = state.cloudPower ? 'translateX(16px)' : 'translateX(0)';
    btn.classList.toggle('bg-brand', state.cloudPower);
    btn.classList.toggle('bg-slate-200', !state.cloudPower);
    const section = document.getElementById('cloud-power-section');
    if (section) section.classList.toggle('hidden', !state.cloudPower);
    localStorage.setItem('cloudPower', String(state.cloudPower));
  });

  const urlInput = document.getElementById('colab-url-input');
  if (urlInput) {
    urlInput.addEventListener('input', e => {
      state.colabUrl = e.target.value.trim();
      localStorage.setItem('colabUrl', state.colabUrl);
    });
  }
}

export function setMeetingCaptureToggle(on) {
  const btn = document.getElementById('meeting-capture-toggle');
  if (!btn) return;
  btn.setAttribute('aria-checked', String(on));
  const dot = btn.querySelector('span');
  if (dot) dot.style.transform = on ? 'translateX(16px)' : 'translateX(0)';
  btn.classList.toggle('bg-brand', on);
  btn.classList.toggle('bg-slate-200', !on);
}

export async function ensureSessionToken() {
  if (state.exitToken) return state.exitToken;
  try {
    const res = await fetch('/api/settings');
    if (res.ok) {
      const data = await res.json();
      if (data.exit_token) state.exitToken = data.exit_token;
    }
  } catch (_) {}
  return state.exitToken || '';
}

export function initMeetingCaptureToggle() {
  const btn = document.getElementById('meeting-capture-toggle');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const next = btn.getAttribute('aria-checked') !== 'true';
    try {
      const token = await ensureSessionToken();
      setMeetingCaptureToggle(next);
      const fd = new FormData();
      fd.append('enabled', String(next));
      fd.append('token', token);
      const res = await fetch('/api/settings/meeting-capture', { method: 'POST', body: fd });
      if (!res.ok) throw new Error('save failed');
    } catch (_) {
      setMeetingCaptureToggle(!next); // revert on failure
    }
  });
}

function uploadWithProgress(fd, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/transcribe');
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); }
        catch { reject(new Error('Invalid server response')); }
      } else {
        try { reject(new Error(JSON.parse(xhr.responseText).detail || xhr.statusText)); }
        catch { reject(new Error(xhr.statusText || `HTTP ${xhr.status}`)); }
      }
    };
    xhr.onerror = () => reject(new Error('Network error — check your connection'));
    xhr.send(fd);
  });
}

function makeUrlFormData(urlValue) {
  const fd = new FormData();
  fd.append('source_url', urlValue);
  fd.append('allow_playlist', 'true');
  fd.append('model', state.selectedModel);
  fd.append('language', state.selectedLanguage);
  fd.append('diarize', state.diarize ? 'true' : 'false');
  if (state.cloudPower) fd.append('colab_url', state.colabUrl);
  fd.append('hf_token', state.hfToken);
  fd.append('num_speakers', state.numSpeakers);
  fd.append('min_speakers', state.minSpeakers);
  fd.append('max_speakers', state.maxSpeakers);
  return fd;
}

async function startUrlTranscription(urlArg = '') {
  const urlValue = String(urlArg || '').trim();
  if (!urlValue) {
    showError('Paste a video URL first.');
    return;
  }

  hideError();
  clearFile();
  setProcessing(true);
  document.getElementById('proc-filename').textContent = urlValue;
  document.getElementById('proc-message').textContent = 'Queueing URL...';
  document.getElementById('progress-bar').style.width = '0%';
  document.getElementById('proc-percent').textContent = '0%';
  document.getElementById('progress-bar-wrap').setAttribute('aria-valuenow', '0');

  clientLog(`Queueing online source: ${urlValue}`);

  try {
    const res = await fetch('/api/transcribe/url', { method: 'POST', body: makeUrlFormData(urlValue) });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(payload.detail || `HTTP ${res.status}`);
    }

    const count = Number(payload.count || 0);
    if (!payload.first_job_id) {
      throw new Error('No job returned by backend');
    }

    state.jobId = payload.first_job_id;
    state.recordingId = payload.first_recording_id || null;
    state.activeRecordingId = null;

    if (count > 1) {
      clientLog(`Playlist import queued ${count} jobs. Showing progress for the first item.`);
    } else {
      clientLog(`Source queued — job ${payload.first_job_id}`);
    }

    connectSSE(payload.first_job_id);
  } catch (err) {
    setProcessing(false);
    showError(`URL import failed: ${err.message}`);
  }
}

export async function startTranscription() {
  if (state.batchMode) { startBatchTranscription(); return; }
  if (!state.selectedFile) return;

  // Capture and immediately clear the staged file so dropping a new file
  // after this job completes does not create an unwanted batch.
  const file = state.selectedFile;
  state.selectedFile = null;
  document.getElementById('file-info').classList.add('hidden');
  document.getElementById('start-btn').disabled = true;

  const dest = state.cloudPower ? `Colab (${state.colabUrl})` : 'local';
  clientLog(`Uploading ${file.name} → ${dest} [model=${state.selectedModel}, lang=${state.selectedLanguage || 'auto'}, diarize=${state.diarize}]`);
  hideError();

  const fd = new FormData();
  fd.append('file', file);
  fd.append('model', state.selectedModel);
  fd.append('language', state.selectedLanguage);
  fd.append('diarize', state.diarize ? 'true' : 'false');
  if (state.cloudPower) fd.append('colab_url', state.colabUrl);
  fd.append('hf_token', state.hfToken);
  fd.append('num_speakers', state.numSpeakers);
  fd.append('min_speakers', state.minSpeakers);
  fd.append('max_speakers', state.maxSpeakers);

  setProcessing(true);
  document.getElementById('proc-filename').textContent = file.name;
  document.getElementById('proc-message').textContent = 'Uploading…';

  let lastMilestone = -1;
  try {
    const data = await uploadWithProgress(fd, pct => {
      const p = Math.round(pct * 100);
      document.getElementById('proc-message').textContent = p < 100 ? `Uploading… ${p}%` : 'Queued…';
      document.getElementById('progress-bar').style.width = `${p}%`;
      document.getElementById('proc-percent').textContent = `${p}%`;
      document.getElementById('progress-bar-wrap').setAttribute('aria-valuenow', String(p));
      const milestone = Math.floor(p / 25) * 25;
      if (milestone > lastMilestone) { clientLog(`Upload ${milestone}%`); lastMilestone = milestone; }
    });
    clientLog(`Upload complete — job ${data.job_id}`);
    // Reset progress bar so SSE-driven transcription progress starts from 0
    document.getElementById('progress-bar').style.width = '0%';
    document.getElementById('proc-percent').textContent = '0%';
    document.getElementById('progress-bar-wrap').setAttribute('aria-valuenow', '0');
    state.jobId = data.job_id;
    state.recordingId = data.recording_id || null;
    state.activeRecordingId = null;
    connectSSE(data.job_id);
  } catch (err) {
    setProcessing(false);
    showError(`Upload failed: ${err.message}`);
  }
}

function makeBatchFormData(file) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('model', state.selectedModel);
  fd.append('language', state.selectedLanguage);
  fd.append('diarize', state.diarize ? 'true' : 'false');
  if (state.cloudPower) fd.append('colab_url', state.colabUrl);
  fd.append('hf_token', state.hfToken);
  fd.append('num_speakers', state.numSpeakers);
  fd.append('min_speakers', state.minSpeakers);
  fd.append('max_speakers', state.maxSpeakers);
  return fd;
}

async function startBatchTranscription() {
  const pending = state.batchQueue.filter(i => i.status === 'pending');
  if (pending.length === 0) return;
  const btn = document.getElementById('start-btn');
  btn.disabled = true;
  btn.textContent = 'Submitting\u2026';
  resetJobLogs();
  startLogPolling();
  await Promise.allSettled(pending.map(item => submitBatchItem(item)));
  // Button stays disabled while jobs are running; re-enabled via checkBatchComplete
}

async function submitBatchItem(item) {
  item.status = 'uploading';
  item.progress = 0;
  item.message = 'Uploading…';
  renderBatchList();
  clientLog(`Uploading ${item.file.name}…`);
  try {
    const data = await uploadWithProgress(makeBatchFormData(item.file), pct => {
      item.progress = pct * 0.05; // upload counts as first 5% of total progress
      item.message = `Uploading… ${Math.round(pct * 100)}%`;
      renderBatchList();
    });
    clientLog(`Upload complete: ${item.file.name} — job ${data.job_id}`);
    item.jobId = data.job_id;
    item.recordingId = data.recording_id || null;
    item.status = 'queued';
    item.progress = 0;
    item.message = '';
    renderBatchList();
    connectBatchSSE(item);
  } catch (err) {
    item.status = 'error';
    item.message = err.message;
    renderBatchList();
    checkBatchComplete();
  }
}

function connectBatchSSE(item) {
  const es = new EventSource(`/api/jobs/${item.jobId}/stream`);
  es.onmessage = e => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    if (data.heartbeat) return;

    if (data.status === 'done') item.status = 'done';
    else if (data.status === 'error' || data.status === 'cancelled') item.status = 'error';
    else item.status = data.progress > 0 ? 'processing' : 'queued';
    item.progress = data.progress || 0;
    item.message = data.message || '';
    renderBatchList();

    if (data.status === 'done' || data.status === 'error' || data.status === 'cancelled') {
      es.close();
      checkBatchComplete();
    }
  };
  let retried = false;
  es.onerror = () => {
    es.close();
    if (!retried && item.status !== 'done' && item.status !== 'error') {
      retried = true;
      setTimeout(() => connectBatchSSE(item), 2000);
    }
  };
}

function checkBatchComplete() {
  const terminal = ['done', 'error'];
  if (!state.batchQueue.every(i => terminal.includes(i.status))) return;
  stopLogPolling();

  document.getElementById('batch-panel').classList.add('hidden');
  const summaryList = document.getElementById('batch-summary-list');
  summaryList.innerHTML = state.batchQueue.map((item, idx) => {
    const ok = item.status === 'done';
    return `<li class="flex items-center gap-2">
      <span class="${ok ? 'text-emerald-500' : 'text-red-400'}">${ok ? '\u2713' : '\u2717'}</span>
      <span class="flex-1 truncate text-slate-700">${escHtml(item.file.name)}</span>
      ${ok
        ? `<a href="#" data-summary-view="${idx}" class="text-xs text-brand hover:underline focus:outline-none">View transcript</a>`
        : `<span class="text-xs text-red-500">${escHtml(item.message || 'Failed')}</span>`
      }
    </li>`;
  }).join('');

  summaryList.querySelectorAll('[data-summary-view]').forEach(a => {
    a.addEventListener('click', e => {
      e.preventDefault();
      const item = state.batchQueue[parseInt(a.dataset.summaryView, 10)];
      if (item?.jobId) loadResult(item.jobId, item.recordingId);
    });
  });

  document.getElementById('batch-summary').classList.remove('hidden');
}

export function setProcessing(active) {
  document.getElementById('upload-state').classList.remove('hidden');
  const transcriptBtn = document.getElementById('tab-btn-transcript');
  const activeBar = document.getElementById('active-job-bar');

  if (active) {
    transcriptBtn.disabled = false;
    transcriptBtn.classList.add('animate-pulse');
    if (activeBar) activeBar.classList.remove('hidden');
    state.result = null;
    document.getElementById('segments-list').innerHTML = '';
    resetJobLogs();
    startLogPolling();
  } else {
    stopLogPolling();
    transcriptBtn.classList.remove('animate-pulse');
    if (activeBar) activeBar.classList.add('hidden');
    if (!state.result) {
      transcriptBtn.disabled = true;
    }
  }
}

function formatJobLogEntry(entry) {
  const ts = entry?.ts ? new Date(entry.ts * 1000).toLocaleTimeString() : '--:--:--';
  const level = entry?.level || 'INFO';
  const message = entry?.message || '';
  return `[${ts}] ${level} ${message}`;
}

export function clientLog(msg, level = 'INFO') {
  const ts = new Date().toLocaleTimeString();
  state.clientLogs.push(`[${ts}] ${level} ${msg}`);
  if (state.clientLogs.length > 500) state.clientLogs = state.clientLogs.slice(-500);
  renderJobLogs();
  document.getElementById('console-activity-dot').classList.remove('hidden');
}

function renderJobLogs() {
  const pre = document.getElementById('proc-log');
  if (!pre) return;
  const all = [];
  if (state.clientLogs.length) all.push(...state.clientLogs);
  if (state.clientLogs.length && state.jobLogs.length) all.push('──── Backend ────');
  if (state.jobLogs.length) all.push(...state.jobLogs);
  if (!all.length) { pre.textContent = 'No logs yet.'; return; }
  pre.textContent = all.join('\n');
  pre.scrollTop = pre.scrollHeight;
}

export function resetJobLogs() {
  state.jobLogs = [];
  renderJobLogs();
}

export function appendJobLogLine(line) {
  if (!line) return;
  state.jobLogs.push(line);
  if (state.jobLogs.length > 500) state.jobLogs = state.jobLogs.slice(-500);
  renderJobLogs();
}

function getActiveJobId() {
  if (state.batchMode) {
    const processing = state.batchQueue.find(i => i.status === 'processing');
    if (processing?.jobId) return processing.jobId;
    // Fall back to the most recently submitted job
    const submitted = state.batchQueue.filter(i => i.jobId).slice(-1)[0];
    return submitted?.jobId || null;
  }
  return state.jobId;
}

export async function refreshJobLogs() {
  if (state.batchMode && state.batchQueue.length > 0) {
    const jobItems = state.batchQueue.filter(i => i.jobId);
    if (!jobItems.length) return;
    const rawLogs = [];
    await Promise.allSettled(jobItems.map(async item => {
      try {
        const res = await fetch(`/api/jobs/${item.jobId}/logs?limit=500`);
        if (!res.ok) return;
        const payload = await res.json();
        const name = item.file?.name || item.jobId;
        (payload.logs || []).forEach(e => rawLogs.push({ ...e, _name: name }));
      } catch (_) { }
    }));
    rawLogs.sort((a, b) => (a.ts || 0) - (b.ts || 0));
    state.jobLogs = rawLogs.map(e => {
      const ts = e.ts ? new Date(e.ts * 1000).toLocaleTimeString() : '--:--:--';
      return `[${ts}] [${e._name}] ${e.level || 'INFO'} ${e.message || ''}`;
    });
    if (state.jobLogs.length > 1000) state.jobLogs = state.jobLogs.slice(-1000);
    renderJobLogs();
    return;
  }
  const jobId = getActiveJobId();
  if (!jobId) return;
  try {
    const res = await fetch(`/api/jobs/${jobId}/logs?limit=500`);
    if (!res.ok) return;
    const payload = await res.json();
    const lines = (payload.logs || []).map(formatJobLogEntry);
    state.jobLogs = lines;
    renderJobLogs();
  } catch (_) {
    // best-effort diagnostics only
  }
}

function startLogPolling() {
  stopLogPolling();
  refreshJobLogs();
  state.logPollTimer = setInterval(refreshJobLogs, 2000);
  document.getElementById('console-activity-dot').classList.remove('hidden');
}

function stopLogPolling() {
  if (state.logPollTimer) {
    clearInterval(state.logPollTimer);
    state.logPollTimer = null;
  }
  document.getElementById('console-activity-dot').classList.add('hidden');
}

export function initConsole() {
  const toggle = document.getElementById('console-toggle');
  const body = document.getElementById('console-body');
  const chevron = document.getElementById('console-chevron');
  const actions = document.getElementById('console-actions');

  function setConsoleHeight() {
    const h = document.getElementById('console-panel').offsetHeight;
    document.documentElement.style.setProperty('--console-h', `${h}px`);
  }

  function openConsole() {
    body.classList.remove('hidden');
    actions.classList.remove('hidden');
    chevron.style.transform = 'rotate(180deg)';
    toggle.setAttribute('aria-expanded', 'true');
    setConsoleHeight();
    // Scroll log to bottom when opening
    const pre = document.getElementById('proc-log');
    pre.scrollTop = pre.scrollHeight;
  }

  function closeConsole() {
    body.classList.add('hidden');
    actions.classList.add('hidden');
    chevron.style.transform = '';
    toggle.setAttribute('aria-expanded', 'false');
    setConsoleHeight();
  }

  toggle.addEventListener('click', () => {
    const isOpen = toggle.getAttribute('aria-expanded') === 'true';
    if (isOpen) closeConsole(); else openConsole();
  });
  toggle.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle.click(); }
  });

  document.getElementById('refresh-log-btn').addEventListener('click', e => {
    e.stopPropagation();
    refreshJobLogs();
  });
  document.getElementById('copy-log-btn').addEventListener('click', e => {
    e.stopPropagation();
    copyLogsToClipboard();
  });
}

async function copyLogsToClipboard() {
  const content = state.jobLogs.join('\n');
  if (!content) return;
  try {
    await navigator.clipboard.writeText(content);
  } catch (_) {
    // clipboard may be blocked by browser policy
  }
}

function tuiCommand() {
  const origin = window.location.origin;
  const isDefault = origin === 'http://127.0.0.1:8002' || origin === 'http://localhost:8002';
  const urlFlag = isDefault ? '' : ` --api-url ${origin}`;
  return `pip install -r tui/requirements.txt && python -m tui${urlFlag}`;
}

export function updateTuiCommand() {
  const el = document.getElementById('tui-command');
  if (el) el.textContent = tuiCommand();
}

export async function copyTuiCommand() {
  const btn = document.getElementById('tui-copy-btn');
  try {
    await navigator.clipboard.writeText(tuiCommand());
    if (btn) {
      const original = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = original; }, 1500);
    }
  } catch (_) {
    // clipboard may be blocked by browser policy
  }
}
