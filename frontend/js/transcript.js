// Transcript rendering, segment editing and audio player sync.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { loadPastAnalyses } from './analysis.js';
import { loadRecording } from './library.js';
import { state } from './state.js';
import { refreshTagSuggestUI } from './tag-suggest.js';

export function fmtDur(s) {
  const t = Math.floor(s);
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
}

export function renderResults() {
  const r = state.result;

  // Meta sidebar
  const items = document.getElementById('meta-items');
  const metas = [
    ['Language', r.language ? r.language.toUpperCase() : '?'],
    ['Duration', fmtDur(r.duration)],
    ['Segments', String(r.num_segments)],
  ];
  if (r.speakers.length) metas.push(['Speakers', String(r.speakers.length)]);
  items.innerHTML = metas.map(([k, v]) => `
<div class="flex justify-between text-sm">
  <span class="text-slate-500">${k}</span>
  <span class="font-medium text-slate-700">${v}</span>
</div>
  `).join('');

  renderRecordingMeta();

  // Tag suggestions — offered per recording, so any chips from the previous
  // one are cleared here rather than left to be applied to this transcript.
  refreshTagSuggestUI();

  // Speaker legend
  renderSpeakerLegend(r.speakers);

  // Audio
  createWaveSurfer(state.audioUrl);

  // Segments
  document.getElementById('search-input').value = '';
  renderSegments(r.segments, '');

  // Past AI analyses
  const recId = state.activeRecordingId || state.recordingId;
  if (recId) loadPastAnalyses(recId);
}

/** The folder and tag pills for the open recording.
 *
 * Separate from renderResults so that changing a tag can redraw this strip on
 * its own — re-running the whole render would rebuild the waveform and lose
 * the playback position.
 */
export function renderRecordingMeta() {
  const recMeta = state.currentRecording;
  const recMetaEl = document.getElementById('rec-folder-tag-meta');
  if (recMetaEl) {
    if (recMeta) {
      const folderName = recMeta.folder_id
        ? (state.library.folders.find(f => f.id === recMeta.folder_id)?.name || null)
        : null;
      const tags = recMeta.tags || [];
      const folderHtml = folderName
        ? `<div class="flex items-center gap-1.5 text-xs text-slate-500">
            <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/>
            </svg>
            <span class="truncate">${escHtml(folderName)}</span>
          </div>`
        : '';
      const tagsHtml = tags.length
        ? `<div class="flex flex-wrap gap-1 mt-1">${tags.map(t =>
          `<span class="tag-pill" style="background:${escHtml(t.color_code)}">${escHtml(t.name)}</span>`
        ).join('')}</div>`
        : '';
      recMetaEl.innerHTML = (folderHtml || tagsHtml)
        ? `<div class="space-y-1">${folderHtml}${tagsHtml}</div>`
        : '';
      recMetaEl.classList.toggle('hidden', !folderHtml && !tagsHtml);
    } else {
      recMetaEl.innerHTML = '';
      recMetaEl.classList.add('hidden');
    }
  }
}

function renderSpeakerLegend(speakers) {
  const section = document.getElementById('speaker-section');
  const list = document.getElementById('speaker-list');
  if (!speakers.length) { section.classList.add('hidden'); return; }
  section.classList.remove('hidden');
  list.innerHTML = '';
  speakers.forEach((spk, i) => {
    const idx = i % 8;
    const div = document.createElement('div');
    div.setAttribute('role', 'listitem');
    div.className = 'flex items-center gap-2 select-none group rounded-lg px-2 py-1.5 hover:bg-slate-50 transition';
    div.innerHTML = `
  <span class="w-2.5 h-2.5 rounded-full shrink-0 spk-${idx} cursor-pointer" onclick="toggleSpeakerFilter('${escHtml(spk)}', ${idx})"></span>
  <span class="text-sm text-slate-600 group-hover:text-slate-900 transition flex-1 cursor-pointer" onclick="toggleSpeakerFilter('${escHtml(spk)}', ${idx})">${escHtml(spk)}</span>
  <button class="opacity-40 group-hover:opacity-100 p-1 hover:text-brand transition focus:outline-none" title="Rename speaker" onclick="promptRenameSpeaker('${escHtml(spk)}')">
    <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
      <path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/>
    </svg>
  </button>
  <span id="filter-dot-${idx}" class="hidden w-1.5 h-1.5 rounded-full bg-brand shrink-0"></span>
`;
    list.appendChild(div);
  });
}

export async function promptRenameSpeaker(oldName) {
  const newName = prompt(`Rename speaker "${oldName}" to:`, oldName);
  if (!newName || newName === oldName) return;

  try {
    const fd = new FormData();
    fd.append('old_name', oldName);
    fd.append('new_name', newName);

    let endpoint;
    if (state.activeRecordingId) {
      endpoint = `/api/recordings/${state.activeRecordingId}/transcript/rename-speaker`;
    } else {
      endpoint = `/api/jobs/${state.jobId}/rename-speaker`;
    }

    const res = await fetch(endpoint, { method: 'POST', body: fd });
    if (!res.ok) throw new Error('Failed to rename speaker');

    // Update local state
    if (state.result) {
      const idx = state.result.speakers.indexOf(oldName);
      if (idx !== -1) {
        state.result.speakers[idx] = newName;
        state.result.speakers.sort();
      }
      state.result.segments.forEach(seg => {
        if (seg["speaker"] === oldName) seg["speaker"] = newName;
      });
      if (state.activeSpeakerFilter === oldName) {
        state.activeSpeakerFilter = newName;
      }
    }

    renderSpeakerLegend(state.result.speakers);
    applyFilters();
  } catch (err) {
    alert(`Rename failed: ${err.message}`);
  }
}

export function toggleSpeakerFilter(spk, idx) {
  state.activeSpeakerFilter = state.activeSpeakerFilter === spk ? null : spk;
  // update indicator dots
  document.querySelectorAll('[id^="filter-dot-"]').forEach(d => d.classList.add('hidden'));
  if (state.activeSpeakerFilter) {
    document.getElementById(`filter-dot-${idx}`)?.classList.remove('hidden');
  }
  applyFilters();
}

export function applyFilters() {
  let segs = state.result.segments;
  if (state.activeSpeakerFilter) segs = segs.filter(s => s.speaker === state.activeSpeakerFilter);
  const q = state.searchQuery.toLowerCase().trim();
  if (q) {
    segs = segs.filter(s =>
      s.text.toLowerCase().includes(q) ||
      (s.translation && s.translation.toLowerCase().includes(q))
    );
  }
  state.filteredSegments = segs;
  renderSegments(segs, state.searchQuery);
  updateSelectionBar();
  const count = document.getElementById('search-count');
  count.textContent = (q || state.activeSpeakerFilter) ? `${segs.length} shown` : '';
}

export function escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function highlightText(text, q) {
  if (!q) return escHtml(text);
  const eq = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return escHtml(text).replace(new RegExp(eq, 'gi'), m => `<mark>${m}</mark>`);
}

function renderSegments(segments, query) {
  const list = document.getElementById('segments-list');
  const frag = document.createDocumentFragment();
  const hasSpeakers = state.result.speakers.length > 0;
  const q = query.toLowerCase().trim();
  const showActions = !!(state.activeRecordingId || state.recordingId);
  document.getElementById('select-all-segments').classList.toggle('hidden', !showActions);

  segments.forEach(seg => {
    const spkIdx = hasSpeakers ? (state.result.speakers.indexOf(seg.speaker) % 8) : -1;
    const div = document.createElement('div');
    div.className = 'segment group bg-white rounded-r-xl pl-4 pr-5 py-3 cursor-pointer';
    div.setAttribute('role', 'listitem');
    div.dataset.id = String(seg.id);
    div.dataset.start = String(seg.start);
    div.dataset.end = String(seg.end);

    if (seg.id === state.currentSegmentId) {
      div.classList.add('active');
      div.setAttribute('aria-current', 'true');
    }
    if (seg.edited) {
      div.classList.add('edited');
    }

    const checkboxHtml = showActions
      ? `<input type="checkbox" class="segment-checkbox mr-1.5 rounded border-slate-300 text-brand focus:ring-brand shrink-0 cursor-pointer" data-seg-id="${seg.id}" ${state.selectedSegmentIds.has(seg.id) ? 'checked' : ''} onclick="event.stopPropagation(); toggleSegmentSelection(${seg.id}, event.shiftKey)" />`
      : '';

    const spkBadge = hasSpeakers && seg.speaker
      ? `<span class="text-xs font-semibold spk-text-${spkIdx} mr-2 cursor-pointer hover:underline" title="Rename speaker" onclick="event.stopPropagation(); promptRenameSpeaker('${escHtml(seg.speaker)}')">${escHtml(seg.speaker)}</span>`
      : '';

    const translationHtml = seg.translation
      ? `<p class="seg-translation">${escHtml(seg.translation)}</p>`
      : '';

    // Action buttons
    let actionsHtml = '';
    if (showActions) {
      const resetBtn = seg.edited
        ? `<button class="seg-action-btn text-slate-400 hover:text-brand" title="Reset to original text" onclick="event.stopPropagation(); resetSegment(${seg.id})">
         <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
       </button>`
        : '';

      const translateBtn = (!seg.edited)
        ? `<button class="seg-action-btn text-slate-400 hover:text-brand" title="Translate segment" onclick="event.stopPropagation(); translateSegment(${seg.id})">
         <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 5h12M9 3v2m1.048 9.5A18.022 18.022 0 016.412 9m6.088 9h7M11 21l5-10 5 10M12.751 5C11.783 10.77 8.07 15.61 3 18.129" /></svg>
       </button>`
        : '';

      actionsHtml = `
    <div class="seg-actions-overlay ml-auto flex items-center gap-1">
      ${resetBtn}
      ${translateBtn}
      <button class="seg-action-btn text-slate-400 hover:text-brand" title="Edit segment text" onclick="event.stopPropagation(); openSegmentEdit(this, ${seg.id})">
        <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"/></svg>
      </button>
    </div>
  `;
    }

    div.innerHTML = `
  <div class="flex items-baseline gap-1 mb-0.5">
    ${checkboxHtml}
    ${spkBadge}
    <span class="text-xs text-slate-400 font-mono">${fmtDur(seg.start)}</span>
    ${actionsHtml}
  </div>
  <p class="text-sm text-slate-700 leading-relaxed seg-text">${highlightText(seg.text, q)}</p>
  ${translationHtml}
`;
    frag.appendChild(div);
  });

  list.innerHTML = '';
  list.appendChild(frag);
}

export function toggleSegmentSelection(segId, shiftKey) {
  if (shiftKey && state.lastSelectedSegmentId !== null) {
    const ids = state.filteredSegments.map(s => s.id);
    const lastIdx = ids.indexOf(state.lastSelectedSegmentId);
    const currIdx = ids.indexOf(segId);
    if (lastIdx !== -1 && currIdx !== -1) {
      const [start, end] = lastIdx < currIdx ? [lastIdx, currIdx] : [currIdx, lastIdx];
      for (let i = start; i <= end; i++) {
        state.selectedSegmentIds.add(ids[i]);
      }
    }
  } else {
    if (state.selectedSegmentIds.has(segId)) {
      state.selectedSegmentIds.delete(segId);
    } else {
      state.selectedSegmentIds.add(segId);
    }
  }
  state.lastSelectedSegmentId = segId;
  updateSelectionBar();
  document.querySelectorAll('.segment-checkbox').forEach(cb => {
    cb.checked = state.selectedSegmentIds.has(parseInt(cb.dataset.segId, 10));
  });
}

function updateSelectionBar() {
  const n = state.selectedSegmentIds.size;
  const bulkMode = n > 0;
  document.getElementById('toolbar-search').classList.toggle('hidden', bulkMode);
  document.getElementById('toolbar-bulk').classList.toggle('hidden', !bulkMode);
  document.getElementById('selection-count').textContent = `${n} selected`;
  const datalist = document.getElementById('speaker-suggestions');
  if (datalist && state.result) {
    datalist.innerHTML = (state.result.speakers || []).map(s => `<option value="${escHtml(s)}"></option>`).join('');
  }
  const selectAllCb = document.getElementById('select-all-segments');
  if (selectAllCb) {
    const visibleIds = state.filteredSegments.map(s => s.id);
    selectAllCb.checked = visibleIds.length > 0 && visibleIds.every(id => state.selectedSegmentIds.has(id));
  }
}

export function clearSegmentSelection() {
  state.selectedSegmentIds.clear();
  state.lastSelectedSegmentId = null;
  document.querySelectorAll('.segment-checkbox').forEach(cb => cb.checked = false);
  updateSelectionBar();
}

export async function assignSpeakerToSelected() {
  const input = document.getElementById('assign-speaker-input');
  const speakerName = (input.value || '').trim();
  if (!speakerName) return;
  if (state.selectedSegmentIds.size === 0) return;

  const recId = state.activeRecordingId || state.recordingId;
  if (!recId) return;

  const indices = Array.from(state.selectedSegmentIds).join(',');
  const fd = new FormData();
  fd.append('segment_indices', indices);
  fd.append('speaker_name', speakerName);

  try {
    const res = await fetch(`/api/recordings/${recId}/transcript/assign-speaker`, { method: 'POST', body: fd });
    if (!res.ok) throw new Error('Failed to assign speaker');
    const data = await res.json();

    state.selectedSegmentIds.forEach(segId => {
      const seg = state.result.segments.find(s => s.id === segId);
      if (seg) seg.speaker = speakerName;
    });
    if (data.speakers) {
      state.result.speakers = data.speakers;
    } else if (!state.result.speakers.includes(speakerName)) {
      state.result.speakers.push(speakerName);
      state.result.speakers.sort();
    }

    clearSegmentSelection();
    input.value = '';
    renderSpeakerLegend(state.result.speakers);
    applyFilters();
  } catch (err) {
    alert(`Assign speaker failed: ${err.message}`);
  }
}

document.getElementById('select-all-segments').addEventListener('change', e => {
  if (e.target.checked) {
    state.filteredSegments.forEach(s => state.selectedSegmentIds.add(s.id));
  } else {
    state.filteredSegments.forEach(s => state.selectedSegmentIds.delete(s.id));
  }
  document.querySelectorAll('.segment-checkbox').forEach(cb => {
    cb.checked = state.selectedSegmentIds.has(parseInt(cb.dataset.segId, 10));
  });
  updateSelectionBar();
});

export function openSegmentEdit(btnOrId, segIdOpt) {
  let segId = segIdOpt;
  let segEl = null;

  if (typeof btnOrId !== "object" && !segId) {
    // Called with just segId
    segId = btnOrId;
    segEl = document.querySelector(`.segment[data-id="${segId}"]`);
  } else if (btnOrId && btnOrId.closest) {
    // Called with button element
    segEl = btnOrId.closest('.segment');
    segId = segIdOpt;
  }

  if (!segEl || segId == null) return;

  // Prevent multiple editors
  if (segEl.querySelector('.segment-edit-area')) return;

  const textEl = segEl.querySelector('.seg-text');
  const segmentData = state.result.segments.find(s => s.id === segId);
  if (!segmentData) return;
  const originalText = segmentData.text;

  const area = document.createElement('textarea');
  area.className = 'segment-edit-area mt-2';
  area.value = originalText;

  const actions = document.createElement('div');
  actions.className = 'flex justify-end gap-2 mt-2';
  actions.innerHTML = `
<button class="px-3 py-1 text-xs rounded bg-slate-100 hover:bg-slate-200" onclick="event.stopPropagation(); cancelSegmentEdit(this, ${segId})">Cancel</button>
<button class="px-3 py-1 text-xs rounded bg-brand text-white font-semibold" onclick="event.stopPropagation(); saveSegmentEdit(this, ${segId})">Save</button>
  `;

  textEl.classList.add('hidden');
  segEl.appendChild(area);
  segEl.appendChild(actions);

  // CAT tool enhancement: pause player when editing
  if (state.wavesurfer) state.wavesurfer.pause();

  area.focus();
  area.select();

  // Handle keyboard submit/cancel within the editor
  area.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      saveSegmentEdit(actions.querySelector('.bg-brand'), segId);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelSegmentEdit(actions.querySelector('.bg-slate-100'), segId);
    }
  });
}

export function cancelSegmentEdit(btn, segId) {
  const segEl = btn.closest('.segment');
  segEl.querySelector('.segment-edit-area').remove();
  segEl.querySelector('.flex.justify-end').remove();
  segEl.querySelector('.seg-text').classList.remove('hidden');
}

export async function saveSegmentEdit(btn, segId) {
  const segEl = btn.closest('.segment');
  const newText = segEl.querySelector('.segment-edit-area').value.trim();
  if (!newText) return;

  const recId = state.activeRecordingId || state.recordingId;
  const fd = new FormData();
  fd.append('text', newText);

  try {
    const res = await fetch(`/api/recordings/${recId}/transcript/segments/${segId}`, {
      method: 'PATCH',
      body: fd
    });
    if (!res.ok) throw new Error('Failed to save edit');

    // Update local state
    const seg = state.result.segments.find(s => s.id === segId);
    if (!seg.original_text) seg.original_text = seg.text;
    seg.text = newText;
    seg.edited = true;

    applyFilters();
  } catch (err) {
    alert(err.message);
  }
}

export async function resetSegment(segId) {
  if (!confirm('Revert this segment to original text?')) return;
  const recId = state.activeRecordingId || state.recordingId;
  try {
    const res = await fetch(`/api/recordings/${recId}/transcript/segments/${segId}/reset`, { method: 'POST' });
    if (!res.ok) throw new Error('Failed to reset');
    const data = await res.json();

    const seg = state.result.segments.find(s => s.id === segId);
    seg.text = data.text;
    seg.edited = false;
    applyFilters();
  } catch (err) {
    alert(err.message);
  }
}

export async function translateSegment(segId) {
  const recId = state.activeRecordingId || state.recordingId;
  const seg = state.result.segments.find(s => s.id === segId);
  const container = document.querySelector(`.segment[data-id="${segId}"] .seg-actions-overlay`);

  // Show spinner
  const spin = document.createElement('div');
  spin.className = 'spinner';
  container.appendChild(spin);

  try {
    const res = await fetch(`/api/recordings/${recId}/transcript/segments/${segId}/translate`, { method: 'POST' });
    if (!res.ok) throw new Error('Translation failed');
    const data = await res.json();

    seg.translation = data.translation;
    applyFilters();
  } catch (err) {
    alert(err.message);
  } finally {
    spin.remove();
  }
}

export async function translateAll() {
  const recId = state.activeRecordingId || state.recordingId;
  if (!recId) return;

  const btn = document.getElementById('translate-all-btn');

  // Toggle: if already translating, STOP it
  if (btn.dataset.jobId) {
    if (!confirm('Stop the translation job?')) return;
    try {
      await fetch(`/api/jobs/${btn.dataset.jobId}/cancel`, { method: 'POST' });
      btn.dataset.jobId = '';
    } catch (_) { }
    return;
  }

  if (!confirm('Translate all segments to English? This may take a while.')) return;

  const originalHtml = btn.innerHTML;
  btn.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-brand" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Stop`;

  try {
    const res = await fetch(`/api/recordings/${recId}/transcript/translate-all`, { method: 'POST' });
    if (!res.ok) throw new Error('Bulk translation failed');
    const { job_id } = await res.json();

    btn.dataset.jobId = job_id;

    // Connect SSE using the existing system but with type 'translate'
    // To show progress, we also need to activate the processing view?
    // Actually, let's just keep the button as the indicator for 'translate all'.
    // BUT we need to listen for completion.

    const es = new EventSource(`/api/jobs/${job_id}/stream`);
    es.onmessage = e => {
      let data;
      try { data = JSON.parse(e.data); } catch { return; }
      if (data.heartbeat) return;

      if (data.status === 'translating') {
        const pct = Math.round(data.progress * 100);
        btn.innerHTML = `<svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-brand" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg> Stop (${pct}%)`;
      } else if (data.status === 'done') {
        es.close();
        btn.dataset.jobId = '';
        btn.innerHTML = originalHtml;
        if (state.activeRecordingId) loadRecording(state.activeRecordingId);
      } else if (data.status === 'error' || data.status === 'cancelled') {
        es.close();
        btn.dataset.jobId = '';
        btn.innerHTML = originalHtml;
        if (data.status === 'error') alert(`Translation failed: ${data.message}`);
      }
    };
  } catch (err) {
    alert(err.message);
    btn.innerHTML = originalHtml;
    btn.dataset.jobId = '';
  }
}

export function initAudioPlayer() {
  document.getElementById('speed-select').addEventListener('change', e => {
    if (state.wavesurfer) state.wavesurfer.setPlaybackRate(parseFloat(e.target.value));
  });

  document.getElementById('play-pause-btn').addEventListener('click', () => {
    if (state.wavesurfer) state.wavesurfer.playPause();
  });

  document.getElementById('segments-list').addEventListener('click', e => {
    const seg = e.target.closest('.segment');
    if (!seg || !state.wavesurfer) return;
    const t = parseFloat(seg.dataset.start);
    const dur = state.wavesurfer.getDuration();
    if (dur > 0) {
      state.wavesurfer.seekTo(t / dur);
      state.wavesurfer.play();
    }
  });
}

function fmtTime(s) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60).toString().padStart(2, '0');
  return `${m}:${sec}`;
}

function createWaveSurfer(audioUrl) {
  if (state.wavesurfer) { state.wavesurfer.destroy(); state.wavesurfer = null; }
  if (typeof WaveSurfer === 'undefined') return;

  // Reset controls to initial state
  const ppBtn = document.getElementById('play-pause-btn');
  ppBtn.disabled = true;
  document.getElementById('play-icon').classList.remove('hidden');
  document.getElementById('pause-icon').classList.add('hidden');
  document.getElementById('player-time').textContent = '0:00 / 0:00';

  document.getElementById('waveform-loading').textContent = 'Generating waveform\u2026';
  document.getElementById('waveform-loading').classList.remove('hidden');

  const ws = WaveSurfer.create({
    container: '#waveform',
    waveColor: '#c4b5fd',
    progressColor: '#6c63ff',
    height: 80,
    barWidth: 2,
    barGap: 1,
    barRadius: 2,
    cursorColor: '#6c63ff',
    cursorWidth: 2,
    normalize: true,
  });

  function setPlayPauseIcon(playing) {
    const btn = document.getElementById('play-pause-btn');
    btn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    document.getElementById('play-icon').classList.toggle('hidden', playing);
    document.getElementById('pause-icon').classList.toggle('hidden', !playing);
  }

  ws.on('ready', () => {
    document.getElementById('waveform-loading').classList.add('hidden');
    document.getElementById('play-pause-btn').disabled = false;
    document.getElementById('player-time').textContent = `0:00 / ${fmtTime(ws.getDuration())}`;
    const rate = parseFloat(document.getElementById('speed-select').value) || 1;
    ws.setPlaybackRate(rate);
  });

  ws.on('play', () => setPlayPauseIcon(true));
  ws.on('pause', () => setPlayPauseIcon(false));

  ws.on('timeupdate', currentTime => {
    document.getElementById('player-time').textContent = `${fmtTime(currentTime)} / ${fmtTime(ws.getDuration())}`;

    if (!state.result) return;
    const seg = state.result.segments.find(s => currentTime >= s.start && currentTime < s.end);
    if (!seg || seg.id === state.currentSegmentId) return;

    document.querySelector('.segment.active')?.classList.remove('active');
    document.querySelector('.segment.active')?.removeAttribute('aria-current');

    const el = document.querySelector(`.segment[data-id="${seg.id}"]`);
    if (el) {
      el.classList.add('active');
      el.setAttribute('aria-current', 'true');
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    state.currentSegmentId = seg.id;
  });

  ws.on('finish', () => setPlayPauseIcon(false));

  ws.on('error', () => {
    document.getElementById('waveform-loading').textContent = 'Waveform unavailable for this format.';
    document.getElementById('waveform-loading').classList.remove('hidden');
  });

  ws.load(audioUrl);
  state.wavesurfer = ws;
}
