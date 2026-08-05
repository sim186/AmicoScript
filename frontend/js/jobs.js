// Job lifecycle: SSE stream, cancellation, loading a finished result.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { showError } from './exports.js';
import { loadRecording } from './library.js';
import { state } from './state.js';
import { switchTab } from './tabs.js';
import { escHtml, fmtDur, renderResults } from './transcript.js';
import { appendJobLogLine, clientLog, refreshJobLogs, setProcessing } from './upload.js';

let _reconnectAttempts = 0;

export function connectSSE(jobId, jobType = 'transcribe') {
  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  state.eventSource = es;

  es.onmessage = e => {
    _reconnectAttempts = 0;
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    if (data.heartbeat) return;

    state.jobStatus = data.status;
    state.jobProgress = data.progress;
    state.jobMessage = data.message || '';
    appendJobLogLine(`[${new Date().toLocaleTimeString()}] EVENT ${data.status}: ${state.jobMessage}`);
    updateProcessingUI();

    if (data.status === 'transcribing' && data.data?.segment) {
      appendLiveSegment(data.data.segment);
    }

    if (data.status === 'done') {
      es.close();
      refreshJobLogs();
      if (jobType === 'transcribe') {
        clientLog('Transcription complete');
        loadResult(jobId);
      } else if (jobType === 'translate') {
        clientLog('Translation complete');
        if (state.activeRecordingId) loadRecording(state.activeRecordingId);
      }
    } else if (data.status === 'error') {
      es.close();
      refreshJobLogs();
      clientLog(`${jobType === 'translate' ? 'Translation' : 'Transcription'} error: ${data.message}`, 'ERROR');
      setProcessing(false);
      showError(`${jobType === 'translate' ? 'Translation' : 'Transcription'} error: ${data.message}`);
    } else if (data.status === 'cancelled') {
      es.close();
      refreshJobLogs();
      setProcessing(false);
    }
  };

  es.onerror = () => {
    if (_reconnectAttempts > 0 || ['done', 'error', 'cancelled'].includes(state.jobStatus)) return;
    _reconnectAttempts++;
    es.close();
    setTimeout(() => connectSSE(jobId), 2000);
  };
}

function updateProcessingUI() {
  const pct = Math.max(0, Math.min(100, Math.round(state.jobProgress * 100)));
  document.getElementById('proc-message').textContent = state.jobMessage || 'Processing…';
  document.getElementById('progress-bar').style.width = `${pct}%`;
  document.getElementById('proc-percent').textContent = `${pct}%`;
  document.getElementById('progress-bar-wrap').setAttribute('aria-valuenow', String(pct));
}

function appendLiveSegment(seg) {
  const list = document.getElementById('segments-list');
  const div = document.createElement('div');
  div.className = 'segment group bg-white rounded-r-xl pl-4 pr-5 py-3 opacity-80';
  div.setAttribute('role', 'listitem');
  div.dataset.id = String(seg.id);
  div.dataset.start = String(seg.start);
  div.dataset.end = String(seg.end);
  div.innerHTML = `
    <div class="flex items-baseline gap-1 mb-0.5">
      <span class="text-xs text-slate-400 font-mono">${fmtDur(seg.start)}</span>
    </div>
    <p class="text-sm text-slate-700 leading-relaxed">${escHtml(seg.text)}</p>
  `;
  list.appendChild(div);
  list.parentElement.scrollTop = list.parentElement.scrollHeight;
}

export async function cancelJob() {
  clientLog('Transcription cancelled by user', 'WARN');
  state.eventSource?.close();
  if (state.jobId) await fetch(`/api/jobs/${state.jobId}/cancel`, { method: 'POST' }).catch(() => { });
  setProcessing(false);
  state.jobId = null;
}

export async function loadResult(jobId, recordingId) {
  try {
    const [resultRes, audioRes] = await Promise.all([
      fetch(`/api/jobs/${jobId}/result`),
      fetch(`/api/audio/${jobId}`),
    ]);
    if (!resultRes.ok) throw new Error('Failed to fetch result');
    const result = await resultRes.json();
    const audioBlob = await audioRes.blob();

    if (recordingId) {
      state.activeRecordingId = recordingId;
      state.recordingId = recordingId;
    }
    state.result = result;
    state.audioUrl = URL.createObjectURL(audioBlob);
    state.filteredSegments = result.segments;
    state.activeSpeakerFilter = null;
    state.currentSegmentId = null;
    state.searchQuery = '';
    state.selectedSegmentIds.clear();
    state.lastSelectedSegmentId = null;

    renderResults();
    setProcessing(false);

    // enable transcript tab and switch to it
    const transcriptBtn = document.getElementById('tab-btn-transcript');
    transcriptBtn.disabled = false;
    switchTab('transcript');
  } catch (err) {
    setProcessing(false);
    showError(`Failed to load result: ${err.message}`);
  }
}
