// Floating widget listing active transcription jobs.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { connectSSE } from './jobs.js';
import { fetchLibrary } from './library.js';
import { state } from './state.js';
import { switchTab } from './tabs.js';
import { setProcessing } from './upload.js';
import { _maybeOpenLatestWatcherRecording, _watcherAutoOpenPending, _watcherWasRecording } from './watcher.js';

const QUEUE_STATUS_LABEL = {
  queued: 'Queued',
  downloading: 'Downloading',
  postprocessing: 'Processing',
  preparing: 'Preparing',
  transcribing: 'Transcribing',
  diarizing: 'Diarizing',
  warning: 'Warning',
};

export function toggleQueuePanel(force) {
  const panel = document.getElementById('queue-panel');
  if (!panel) return;
  const shouldOpen = (typeof force === 'boolean') ? force : panel.classList.contains('hidden');
  panel.classList.toggle('hidden', !shouldOpen);
}

window.toggleQueuePanel = toggleQueuePanel;

let _queueLastSig = '';

function _hideQueueWidget() {
  const widget = document.getElementById('queue-widget');
  const rows = document.getElementById('queue-panel-rows');
  if (widget) widget.classList.add('hidden');
  const panel = document.getElementById('queue-panel');
  if (panel) panel.classList.add('hidden');
  if (rows) rows.innerHTML = '';
  _queueLastSig = '';
}

window._hideQueueWidget = _hideQueueWidget;

function _renderQueueWidget(jobs) {
  const widget = document.getElementById('queue-widget');
  const rows = document.getElementById('queue-panel-rows');
  const count = document.getElementById('queue-pill-count');
  const label = document.getElementById('queue-pill-label');
  if (!widget || !rows) return;
  const hasJobs = jobs && jobs.length > 0;
  if (!hasJobs) { _hideQueueWidget(); return; }
  // Skip re-render if nothing relevant changed — keeps DOM stable.
  const sig = jobs.map(j => `${j.id}:${j.status}:${Math.round((j.progress || 0) * 100)}`).join('|');
  if (sig === _queueLastSig) return;
  _queueLastSig = sig;
  widget.classList.remove('hidden');
  if (count) count.textContent = String(jobs.length);
  if (label) label.textContent = jobs.length === 1 ? 'job' : 'jobs';
  rows.innerHTML = jobs.map(j => {
    const pct = Math.round((j.progress || 0) * 100);
    const lbl = QUEUE_STATUS_LABEL[j.status] || j.status;
    const name = (j.filename || j.source_url || j.id || '').toString();
    const displayName = name.length > 40 ? name.slice(0, 37) + '…' : (name || '(unnamed)');
    const isQueued = j.status === 'queued';
    const barColor = isQueued ? 'bg-slate-300' : 'bg-brand';
    const barWidth = isQueued ? 100 : Math.max(pct, 2);
    const safeName = name.replace(/"/g, '&quot;');
    return `
      <div class="px-3 py-2 flex flex-col gap-1.5 hover:bg-slate-50 cursor-pointer" data-job-id="${j.id}" onclick="attachToJob('${j.id}')">
        <div class="flex items-center gap-2">
          <span class="font-mono text-[10px] text-slate-400 w-5 shrink-0">#${(j.position || 0) + 1}</span>
          <span class="flex-1 min-w-0 truncate text-xs text-slate-700" title="${safeName}">${displayName}</span>
          <button onclick="event.stopPropagation();cancelQueuedJob('${j.id}')" title="Cancel" class="text-slate-400 hover:text-red-500 shrink-0">
            <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="flex items-center gap-2">
          <div class="flex-1 h-1 bg-slate-200 rounded-full overflow-hidden">
            <div class="${barColor} h-full transition-all" style="width:${barWidth}%"></div>
          </div>
          <span class="text-[10px] text-slate-500 shrink-0 w-20 text-right">${lbl}${isQueued ? '' : ' · ' + pct + '%'}</span>
        </div>
      </div>`;
  }).join('');
}

export async function cancelQueuedJob(jobId) {
  // Optimistic UI: remove row immediately and hide widget if it was last.
  const rowsEl = document.getElementById('queue-panel-rows');
  const row = rowsEl ? rowsEl.querySelector(`[data-job-id="${jobId}"]`) : null;
  if (row) row.remove();
  if (rowsEl && rowsEl.children.length === 0) _hideQueueWidget();
  _queueLastSig = '';  // force next poll to re-render
  try { await fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' }); } catch (_) {}
  refreshQueueWidget();
}

window.cancelQueuedJob = cancelQueuedJob;

export function attachToJob(jobId) {
  if (!jobId) return;
  if (state.jobId === jobId) {
    switchTab('transcript');
    toggleQueuePanel(false);
    return;
  }
  state.jobId = jobId;
  state.result = null;
  state.recordingId = null;
  state.activeRecordingId = null;
  setProcessing(true);
  connectSSE(jobId);
  switchTab('transcript');
  toggleQueuePanel(false);
}

window.attachToJob = attachToJob;

const _queueSeenDone = new Set();

async function refreshQueueWidget() {
  try {
    const res = await fetch('/api/jobs');
    if (!res.ok) return;
    const data = await res.json();
    const jobs = data.jobs || [];
    _renderQueueWidget(jobs);
    // A job can finish without the UI having started it (e.g. the meeting
    // watcher posting to /api/transcribe in the background) — refresh the
    // Library so a newly finished recording shows up without a manual reload.
    let justFinished = false;
    for (const j of jobs) {
      if (j.status === 'done' && !_queueSeenDone.has(j.id)) {
        _queueSeenDone.add(j.id);
        justFinished = true;
      }
    }
    if (justFinished && state.activeTab === 'library') fetchLibrary();
    // Prune the seen-done set once the queue drains so it can't grow
    // unbounded over a long-lived session (only tracked across consecutive
    // polls with active jobs).
    if (jobs.length === 0) _queueSeenDone.clear();
    if (_watcherAutoOpenPending && !_watcherWasRecording) {
      _maybeOpenLatestWatcherRecording();
    }
  } catch (_) {}
}

setInterval(refreshQueueWidget, 2000);

document.addEventListener('DOMContentLoaded', () => { refreshQueueWidget(); });
