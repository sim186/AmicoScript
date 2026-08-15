// Library list, selection and bulk operations.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { throwIfFailed } from './errors.js';
import { showError } from './exports.js';
import { fetchFolders } from './folders.js';
import { state } from './state.js';
import { switchTab } from './tabs.js';
import { fetchTags } from './tags.js';
import { applyFilters, escHtml, fmtDur, renderResults } from './transcript.js';
import { clientLog } from './upload.js';

function getVisibleFolders() {
  const all = state.library.folders;
  const byParent = {};
  all.forEach(f => {
    const key = f.parent_id || '__root__';
    if (!byParent[key]) byParent[key] = [];
    byParent[key].push(f);
  });
  const visible = [];
  const queue = [...(byParent['__root__'] || [])];
  while (queue.length) {
    const f = queue.shift();
    visible.push(f);
    (byParent[f.id] || []).forEach(c => queue.push(c));
  }
  return visible;
}

export async function fetchLibrary() {
  const params = new URLSearchParams({ sort: state.library.sort, order: state.library.order, limit: '100' });
  if (state.library.selectedFolderId) params.set('folder_id', state.library.selectedFolderId);
  if (state.library.selectedTagId) params.set('tag_id', state.library.selectedTagId);
  try {
    const res = await fetch('/api/library?' + params);
    if (!res.ok) return;
    state.library.recordings = await res.json();
    renderLibrary();
  } catch (_) { }
}

function updateBulkBar() {
  const sel = state.library.selectedIds;
  const bulkBar = document.getElementById('lib-bulk-toolbar');
  const normalBar = document.getElementById('lib-normal-toolbar');
  const countEl = document.getElementById('lib-bulk-count');
  const selectAllCb = document.getElementById('lib-select-all');
  const n = sel.size;
  const total = state.library.recordings.length;

  if (n > 0) {
    bulkBar.classList.remove('hidden');
    bulkBar.classList.add('flex');
    normalBar.classList.add('hidden');
    countEl.textContent = `${n} selected`;

    // Update folder options (only reachable folders)
    const folderSel = document.getElementById('lib-bulk-folder-select');
    folderSel.innerHTML = '<option value="">— No folder —</option>' +
      getVisibleFolders().map(f => `<option value="${escHtml(String(f.id))}">${escHtml(f.name)}</option>`).join('');

    // Update tag list for bulk tag assignment
    const tagList = document.getElementById('lib-bulk-tag-list');
    const tagEmpty = document.getElementById('lib-bulk-tag-empty');
    const allTags = state.library.tags;
    if (allTags.length) {
      tagEmpty.classList.add('hidden');
      // For each tag, compute how many selected recordings have it
      const selectedRecs = state.library.recordings.filter(r => sel.has(r.id));
      tagList.innerHTML = allTags.map(t => {
        const count = selectedRecs.filter(r => (r.tags || []).some(rt => rt.id === t.id)).length;
        const allHave = count === n;
        const noneHave = count === 0;
        const stateClass = allHave ? 'text-brand font-semibold' : noneHave ? 'text-slate-600' : 'text-slate-500 italic';
        const indicator = allHave ? '✓ ' : noneHave ? '' : '− ';
        return `<button onclick="event.stopPropagation(); bulkAssignTag('${escHtml(t.id)}')"
          class="w-full flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-slate-50 text-left ${stateClass}">
          <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background:${escHtml(t.color_code)}"></span>
          ${indicator}${escHtml(t.name)}
        </button>`;
      }).join('');
    } else {
      tagList.innerHTML = '';
      tagEmpty.classList.remove('hidden');
    }
  } else {
    bulkBar.classList.add('hidden');
    bulkBar.classList.remove('flex');
    normalBar.classList.remove('hidden');
    countEl.textContent = '';
  }

  // Sync select-all checkbox state
  if (selectAllCb) {
    selectAllCb.indeterminate = n > 0 && n < total;
    selectAllCb.checked = total > 0 && n === total;
  }
}

export function toggleSelectAll(checked) {
  if (checked) {
    state.library.recordings.forEach(r => state.library.selectedIds.add(r.id));
  } else {
    state.library.selectedIds.clear();
    state.library.lastSelectedIdx = null;
  }
  // Sync individual checkboxes
  document.querySelectorAll('.rec-card-cb').forEach(cb => { cb.checked = checked; });
  // Sync card highlight
  document.querySelectorAll('.rec-card').forEach(card => {
    card.classList.toggle('ring-2', checked);
    card.classList.toggle('ring-brand', checked);
    card.classList.toggle('ring-inset', checked);
  });
  updateBulkBar();
}

export function renderLibrary() {
  const list = document.getElementById('library-list');
  const empty = document.getElementById('library-empty');
  const recs = state.library.recordings;
  // Preserve selections only for IDs still in the current result set
  const validIds = new Set(recs.map(r => r.id));
  state.library.selectedIds.forEach(id => { if (!validIds.has(id)) state.library.selectedIds.delete(id); });
  if (!recs.length) {
    list.innerHTML = '';
    empty.classList.remove('hidden');
    updateBulkBar();
    return;
  }
  empty.classList.add('hidden');
  list.innerHTML = '';
  recs.forEach((rec, idx) => {
    const card = document.createElement('div');
    const isSelected = state.library.selectedIds.has(rec.id);
    card.className = 'rec-card' + (isSelected ? ' ring-2 ring-brand ring-inset' : '');
    // Identify the row in the DOM so the card can be found without relying on
    // its position in the list.
    card.dataset.recordingId = rec.id;
    card.onclick = (e) => {
      // Shift+click: select a range from the last selected index
      if (e.shiftKey && state.library.lastSelectedIdx !== null) {
        const lo = Math.min(state.library.lastSelectedIdx, idx);
        const hi = Math.max(state.library.lastSelectedIdx, idx);
        for (let i = lo; i <= hi; i++) {
          state.library.selectedIds.add(recs[i].id);
        }
        state.library.lastSelectedIdx = idx;
        // Sync checkboxes and highlights without a full re-render
        document.querySelectorAll('.rec-card').forEach((c, ci) => {
          const inRange = ci >= lo && ci <= hi;
          if (inRange) {
            const cb = c.querySelector('.rec-card-cb');
            if (cb) cb.checked = true;
            c.classList.add('ring-2', 'ring-brand', 'ring-inset');
          }
        });
        updateBulkBar();
        return;
      }
      // Ctrl/Cmd+click: toggle individual selection without opening
      if (e.ctrlKey || e.metaKey) {
        const cb = card.querySelector('.rec-card-cb');
        const nowSelected = !state.library.selectedIds.has(rec.id);
        if (nowSelected) { state.library.selectedIds.add(rec.id); } else { state.library.selectedIds.delete(rec.id); }
        if (cb) cb.checked = nowSelected;
        card.classList.toggle('ring-2', nowSelected);
        card.classList.toggle('ring-brand', nowSelected);
        card.classList.toggle('ring-inset', nowSelected);
        state.library.lastSelectedIdx = idx;
        updateBulkBar();
        return;
      }
      openRecording(rec);
    };
    // enable drag of recordings to folders
    card.draggable = true;
    card.addEventListener('dragstart', (e) => {
      try { e.dataTransfer.setData('text/plain', rec.id); e.dataTransfer.effectAllowed = 'move'; } catch (_) { }
    });

    const statusColors = {
      done: 'bg-emerald-100 text-emerald-700',
      error: 'bg-red-100 text-red-600',
      pending: 'bg-slate-100 text-slate-500',
      queued: 'bg-amber-100 text-amber-700',
      transcribing: 'bg-blue-100 text-blue-700',
      diarizing: 'bg-purple-100 text-purple-700',
      cancelled: 'bg-slate-100 text-slate-500',
      interrupted: 'bg-orange-100 text-orange-700',
    };
    const statusCls = statusColors[rec.status] || 'bg-slate-100 text-slate-500';
    // status_detail explains a status the user cannot otherwise account for —
    // most importantly 'interrupted', which means an app restart, not a failure.
    const statusTitle = rec.status_detail ? ` title="${escHtml(rec.status_detail)}"` : '';

    // A failed or interrupted recording still has its audio on disk, so offer
    // to run it again rather than making the user delete and re-import.
    const canRetry = ['error', 'interrupted', 'cancelled'].includes(rec.status);
    const retryBtn = canRetry
      ? `<button class="p-1.5 rounded-lg text-slate-400 hover:text-brand hover:bg-brand/5 transition focus:outline-none"
                 title="Transcribe again" aria-label="Transcribe again"
                 onclick="event.stopPropagation(); retryRecording('${rec.id}')">
           <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
             <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
           </svg>
         </button>`
      : '';

    // Where the recording came from. An auto-captured call and a file you
    // dragged in used to look identical in the list.
    const sourceBadge = {
      meeting: '<span class="text-[10px] px-1.5 py-0.5 rounded bg-rose-50 text-rose-600 font-medium" title="Captured automatically from a call">meeting</span>',
      url: '<span class="text-[10px] px-1.5 py-0.5 rounded bg-sky-50 text-sky-600 font-medium" title="Imported from a link">link</span>',
    }[rec.source] || '';

    // Shown, not just hovered: on a touch screen a title attribute is invisible.
    const statusDetailLine = (rec.status_detail && canRetry)
      ? `<p class="text-[11px] text-slate-400 mt-0.5 leading-tight">${escHtml(rec.status_detail)}</p>`
      : '';

    const assignedTagIds = new Set((rec.tags || []).map(t => t.id));
    // include data attributes so tag clicks can be delegated reliably
    const tagPills = (rec.tags || []).map(t =>
      `<span class="tag-pill" data-tag-id="${escHtml(String(t.id))}" data-tag-name="${escHtml(t.name)}" style="background:${escHtml(t.color_code)}">${escHtml(t.name)}</span>`
    ).join('');

    // Folder selector options (only reachable folders)
    const folderOptions = [
      `<option value="">— No folder —</option>`,
      ...getVisibleFolders().map(f =>
        `<option value="${escHtml(f.id)}" ${rec.folder_id === f.id ? 'selected' : ''}>${escHtml(f.name)}</option>`
      ),
    ].join('');

    // Tag checkboxes for the dropdown
    const tagCheckboxes = state.library.tags.length
      ? state.library.tags.map(t => `
      <label class="flex items-center gap-2 px-3 py-1.5 hover:bg-slate-50 cursor-pointer text-xs rounded">
        <input type="checkbox" ${assignedTagIds.has(t.id) ? 'checked' : ''}
               onchange="event.stopPropagation(); toggleRecordingTag('${rec.id}', '${t.id}', this.checked)"
               class="rounded border-slate-300 text-brand focus:ring-brand" />
        <span class="tag-pill" style="background:${escHtml(t.color_code)}">${escHtml(t.name)}</span>
      </label>`).join('')
      : `<p class="px-3 py-2 text-xs text-slate-400">No tags yet — create one in the sidebar</p>`;

    const dur = rec.duration ? fmtDur(rec.duration) : '—';
    const date = new Date(rec.created_at * 1000).toLocaleDateString();
    const folderName = rec.folder_id ? (state.library.folders.find(f => f.id === rec.folder_id)?.name || '') : '';

    card.innerHTML = `
  <div class="flex items-start gap-3">
    <input type="checkbox" class="rec-card-cb mt-0.5 rounded border-slate-300 text-brand focus:ring-brand cursor-pointer shrink-0"
           ${isSelected ? 'checked' : ''}
           onclick="event.stopPropagation()"
           onchange="toggleCardSelection('${rec.id}', this.checked, this.closest('.rec-card'))" />
    <div class="flex-1 min-w-0">
      <div class="flex items-center gap-1.5 min-w-0">
        <p class="text-sm font-semibold text-slate-800 truncate" title="${escHtml(rec.filename)}">${escHtml(rec.alias || rec.filename)}</p>
        ${sourceBadge}
      </div>
      <p class="text-xs text-slate-400 mt-0.5">${date} · ${dur}${folderName ? ' · ' + escHtml(folderName) : ''}${rec.alias ? ' · ' + escHtml(rec.filename) : ''}</p>
      ${statusDetailLine}
      ${tagPills ? `<div class="flex flex-wrap gap-1 mt-2">${tagPills}</div>` : ''}
    </div>
    <div class="flex items-center gap-1.5 shrink-0" onclick="event.stopPropagation()">

      <!-- Rename / alias -->
      <div class="relative">
        <button class="p-1.5 rounded-lg text-slate-300 hover:text-brand hover:bg-brand/5 transition focus:outline-none"
                title="Rename" onclick="window.togglePopover(this)">
          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M16.862 3.487a2.25 2.25 0 113.182 3.182L7.5 19.213l-4 1 1-4 12.362-12.726z"/>
          </svg>
        </button>
        <div class="popover hidden absolute right-0 top-full mt-1 z-50 bg-white border border-slate-200 rounded-lg shadow-lg p-3 min-w-[220px]">
          <p class="text-xs text-slate-400 mb-2">Display name (alias)</p>
          <input type="text" class="w-full text-xs rounded border border-slate-200 px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-brand"
                 placeholder="${escHtml(rec.filename)}"
                 value="${escHtml(rec.alias || '')}"
                 onkeydown="if(event.key==='Enter'){saveAlias('${rec.id}',this);}" />
          <div class="flex justify-end gap-2 mt-2">
            <button class="text-xs text-slate-400 hover:text-slate-600" onclick="this.closest('.popover').classList.add('hidden')">Cancel</button>
            <button class="text-xs font-medium text-brand hover:text-brand/80" onclick="saveAlias('${rec.id}',this.closest('.popover').querySelector('input'))">Save</button>
          </div>
        </div>
      </div>

      <!-- Move to folder -->
      <div class="relative">
            <button class="p-1.5 rounded-lg text-slate-300 hover:text-brand hover:bg-brand/5 transition focus:outline-none"
              title="Move to folder"
              onclick="window.togglePopover(this)">
          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/>
          </svg>
        </button>
        <div class="popover hidden absolute right-0 top-full mt-1 z-50 bg-white border border-slate-200 rounded-lg shadow-lg p-2 min-w-[160px]">
          <p class="text-xs text-slate-400 px-1 mb-1">Move to folder</p>
          <select class="w-full text-xs rounded border border-slate-200 px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-brand bg-white"
                  onchange="moveRecordingToFolder('${rec.id}', this.value)">
            ${folderOptions}
          </select>
        </div>
      </div>

      <!-- Assign tags -->
      <div class="relative">
            <button class="p-1.5 rounded-lg text-slate-300 hover:text-brand hover:bg-brand/5 transition focus:outline-none"
              title="Assign tags"
              onclick="window.togglePopover(this)">
          <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a2 2 0 014-4z"/>
          </svg>
        </button>
        <div class="popover hidden absolute right-0 top-full mt-1 z-50 bg-white border border-slate-200 rounded-lg shadow-lg py-1 min-w-[160px]">
          <p class="text-xs text-slate-400 px-3 py-1">Assign tags</p>
          ${tagCheckboxes}
        </div>
      </div>

      <span class="px-2 py-0.5 rounded-full text-xs font-medium ${statusCls}"${statusTitle}>${rec.status}</span>
      ${retryBtn}
      <button class="p-1.5 rounded-lg text-slate-300 hover:text-red-400 transition focus:outline-none"
              title="Delete recording"
              onclick="deleteRecordingConfirm('${rec.id}')">
        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
        </svg>
      </button>
    </div>
  </div>
`;
    list.appendChild(card);
  });
  updateBulkBar();
}

export function toggleCardSelection(id, checked, cardEl) {
  if (checked) {
    state.library.selectedIds.add(id);
  } else {
    state.library.selectedIds.delete(id);
  }
  if (cardEl) {
    cardEl.classList.toggle('ring-2', checked);
    cardEl.classList.toggle('ring-brand', checked);
    cardEl.classList.toggle('ring-inset', checked);
  }
  // Track last toggled index for Shift+click range selection
  const idx = state.library.recordings.findIndex(r => r.id === id);
  if (idx !== -1) state.library.lastSelectedIdx = checked ? idx : null;
  updateBulkBar();
}

export async function bulkDelete() {
  const ids = [...state.library.selectedIds];
  if (!ids.length) return;
  if (!confirm(`Permanently delete ${ids.length} recording${ids.length > 1 ? 's' : ''} and their transcripts?`)) return;
  let failed = 0;
  for (const id of ids) {
    try {
      const res = await fetch(`/api/recordings/${id}`, { method: 'DELETE' });
      if (!res.ok) failed++;
      else {
        if (state.activeRecordingId === id) { state.activeRecordingId = null; state.result = null; }
      }
    } catch (_) { failed++; }
  }
  state.library.selectedIds.clear();
  if (failed) alert(`${failed} deletion${failed > 1 ? 's' : ''} failed.`);
  fetchLibrary();
}

export async function bulkExport(fmt) {
  document.querySelectorAll('.popover').forEach(p => p.classList.add('hidden'));
  const ids = [...state.library.selectedIds];
  if (!ids.length) return;

  if (fmt === 'md') {
    try {
      const res = await fetch('/api/recordings/bulk-export/md', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ids,
          wikilinks: !!document.getElementById('export-wikilinks')?.checked,
        }),
      });
      if (!res.ok) { alert(`Export failed: HTTP ${res.status}`); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const filename = ids.length === 1
        ? (state.library.recordings.find(r => r.id === ids[0])?.filename || 'transcript').replace(/\.[^.]+$/, '')
        : 'transcripts';
      a.href = url; a.download = `${filename}.md`; a.click();
      URL.revokeObjectURL(url);
    } catch (err) { alert(`Export failed: ${err.message}`); }
    return;
  }

  for (const id of ids) {
    try {
      const rec = state.library.recordings.find(r => r.id === id);
      const baseName = (rec?.filename || id).replace(/\.[^.]+$/, '');
      const res = await fetch(`/api/recordings/${id}/export/${fmt}`);
      if (!res.ok) { alert(`Export failed for ${baseName}: HTTP ${res.status}`); continue; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `${baseName}.${fmt}`; a.click();
      URL.revokeObjectURL(url);
      await new Promise(r => setTimeout(r, 150));
    } catch (err) { alert(`Export failed: ${err.message}`); }
  }
}

export async function saveAlias(recId, input) {
  const alias = input.value.trim();
  try {
    const fd = new FormData();
    fd.append('alias', alias);
    const res = await fetch(`/api/recordings/${recId}`, { method: 'PATCH', body: fd });
    if (!res.ok) { alert(`Rename failed: HTTP ${res.status}`); return; }
    const updated = await res.json();
    const rec = state.library.recordings.find(r => r.id === recId);
    if (rec) rec.alias = updated.alias;
    input.closest('.popover').classList.add('hidden');
    renderLibrary();
  } catch (err) { alert(`Rename failed: ${err.message}`); }
}

export async function bulkMoveToFolder(folderId) {
  document.querySelectorAll('.popover').forEach(p => p.classList.add('hidden'));
  const ids = [...state.library.selectedIds];
  if (!ids.length) return;
  let failed = 0;
  for (const id of ids) {
    try {
      const fd = new FormData();
      fd.append('folder_id', folderId);
      const res = await fetch(`/api/recordings/${id}`, { method: 'PATCH', body: fd });
      if (!res.ok) { failed++; continue; }
      const rec = state.library.recordings.find(r => r.id === id);
      if (rec) rec.folder_id = folderId || null;
    } catch (_) { failed++; }
  }
  // If viewing a specific folder, remove moved-away cards
  if (state.library.selectedFolderId && state.library.selectedFolderId !== folderId) {
    state.library.recordings = state.library.recordings.filter(r => !ids.includes(r.id));
  }
  state.library.selectedIds.clear();
  if (failed) alert(`${failed} move${failed > 1 ? 's' : ''} failed.`);
  renderLibrary();
  try { fetchFolders(); fetchTags(); } catch (_) { }
}

export async function bulkAssignTag(tagId) {
  const ids = [...state.library.selectedIds];
  if (!ids.length) return;
  const tag = state.library.tags.find(t => t.id === tagId);
  if (!tag) return;

  // Determine current state: if ALL selected have the tag → remove, otherwise → add
  const selectedRecs = state.library.recordings.filter(r => ids.includes(r.id));
  const allHave = selectedRecs.every(r => (r.tags || []).some(t => t.id === tagId));
  const method = allHave ? 'DELETE' : 'POST';

  let failed = 0;
  for (const id of ids) {
    try {
      const res = await fetch(`/api/recordings/${id}/tags/${tagId}`, { method });
      if (!res.ok) { failed++; continue; }
      const rec = state.library.recordings.find(r => r.id === id);
      if (rec) {
        if (method === 'POST') {
          if (!rec.tags) rec.tags = [];
          if (!rec.tags.find(t => t.id === tagId)) rec.tags.push(tag);
        } else {
          rec.tags = (rec.tags || []).filter(t => t.id !== tagId);
        }
      }
    } catch (_) { failed++; }
  }
  if (failed) alert(`${failed} tag operation${failed > 1 ? 's' : ''} failed.`);
  // Re-render to update tag pills and refresh the tag popover state
  renderLibrary();
  try { fetchTags(); } catch (_) { }
}

export function togglePopover(btn) {
  const popover = btn.nextElementSibling;
  const isOpen = !popover.classList.contains('hidden');
  // Close all other open popovers
  document.querySelectorAll('.popover').forEach(p => p.classList.add('hidden'));
  if (!isOpen) {
    popover.classList.remove('hidden');
    // Close on outside click
    setTimeout(() => {
      document.addEventListener('click', function close(e) {
        if (!popover.contains(e.target) && e.target !== btn) {
          popover.classList.add('hidden');
          document.removeEventListener('click', close);
        }
      });
    }, 0);
  }
}

export async function moveRecordingToFolder(recordingId, folderId) {
  const fd = new FormData();
  fd.append('folder_id', folderId);
  try {
    const res = await fetch(`/api/recordings/${recordingId}`, { method: 'PATCH', body: fd });
    await throwIfFailed(res, 'The request failed.');
    // Update local state without full refetch
    const rec = state.library.recordings.find(r => r.id === recordingId);
    if (rec) rec.folder_id = folderId || null;
    // If we're viewing a specific folder, remove the card from the current view
    if (state.library.selectedFolderId && state.library.selectedFolderId !== folderId) {
      state.library.recordings = state.library.recordings.filter(r => r.id !== recordingId);
    }
    renderLibrary();
    // Refresh folder/tag counts so badges/pills reflect the move
    try { fetchFolders(); fetchTags(); } catch (_) { }
    // Close any open popovers
    document.querySelectorAll('.popover').forEach(p => p.classList.add('hidden'));
  } catch (err) { alert(`Move failed: ${err.message}`); }
}

export async function toggleRecordingTag(recordingId, tagId, add) {
  try {
    const method = add ? 'POST' : 'DELETE';
    const res = await fetch(`/api/recordings/${recordingId}/tags/${tagId}`, { method });
    await throwIfFailed(res, 'The request failed.');
    // Update local state
    const rec = state.library.recordings.find(r => r.id === recordingId);
    if (rec) {
      if (add) {
        const tag = state.library.tags.find(t => t.id === tagId);
        if (tag && !rec.tags.find(t => t.id === tagId)) rec.tags.push(tag);
      } else {
        rec.tags = rec.tags.filter(t => t.id !== tagId);
      }
    }
    // Re-render only the tag pills on this card without rebuilding everything
    fetchLibrary();
    // Update tag counts (scoped counts) so sidebar badges stay accurate
    try { fetchTags(); } catch (_) { }
  } catch (err) { alert(`Tag update failed: ${err.message}`); }
}

export async function loadRecording(id) {
  try {
    const res = await fetch(`/api/recordings/${id}/transcript`);
    if (!res.ok) throw new Error('Transcript not found');
    const trData = await res.json();

    // Update local state
    state.result = trData.json_data;
    state.filteredSegments = trData.json_data.segments;
    state.activeRecordingId = id;

    applyFilters();
  } catch (err) {
    showError(`Failed to reload recording: ${err.message}`);
  }
}

export async function openRecording(rec, silent = false) {
  if (rec.status !== 'done') {
    if (!silent) alert(`This recording has status "${rec.status}" and cannot be opened yet.`);
    return false;
  }
  clientLog(`Opening recording: ${rec.filename}`);
  try {
    const [trRes, audioRes] = await Promise.all([
      fetch(`/api/recordings/${rec.id}/transcript`),
      fetch(`/api/recordings/${rec.id}/audio`),
    ]);
    if (!trRes.ok) throw new Error('Transcript not found');

    const trData = await trRes.json();
    const audioBlob = await audioRes.blob();

    state.activeRecordingId = rec.id;
    state.jobId = null;
    state.recordingId = rec.id;
    const result = trData.json_data;
    result.filename = rec.filename;
    state.result = result;
    state.audioUrl = URL.createObjectURL(audioBlob);
    state.filteredSegments = result.segments;
    state.activeSpeakerFilter = null;
    state.currentSegmentId = null;
    state.searchQuery = '';
    state.selectedSegmentIds.clear();
    state.lastSelectedSegmentId = null;

    // Fetch fresh recording metadata (folder, tags) so the transcript sidebar is accurate
    try {
      const recRes = await fetch(`/api/recordings/${rec.id}`);
      state.currentRecording = recRes.ok ? await recRes.json() : rec;
    } catch (_) {
      state.currentRecording = rec;
    }

    // Reset AI hub state when opening a different recording
    state.rawAiText = '';
    // Legacy panel elements may be absent since the hub replaced them
    const _legacyArea = document.getElementById('ai-result-area');
    if (_legacyArea) _legacyArea.classList.add('hidden');
    const _legacyText = document.getElementById('ai-result-text');
    if (_legacyText) _legacyText.innerHTML = '';
    const _legacyPast = document.getElementById('ai-past-analyses');
    if (_legacyPast) _legacyPast.classList.add('hidden');

    renderResults();

    const transcriptBtn = document.getElementById('tab-btn-transcript');
    transcriptBtn.disabled = false;
    switchTab('transcript');
    return true;
  } catch (err) {
    if (!silent) alert(`Failed to open recording: ${err.message}`);
    return false;
  }
}

export async function deleteRecordingConfirm(recordingId) {
  if (!confirm('Permanently delete this recording and its transcript?')) return;
  try {
    const res = await fetch(`/api/recordings/${recordingId}`, { method: 'DELETE' });
    await throwIfFailed(res, 'The request failed.');
    if (state.activeRecordingId === recordingId) {
      state.activeRecordingId = null;
      state.result = null;
    }
    fetchLibrary();
  } catch (err) { alert(`Delete failed: ${err.message}`); }
}


export async function retryRecording(recordingId) {
  try {
    const res = await fetch(`/api/recordings/${recordingId}/retry`, { method: 'POST' });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert(body.detail || 'Could not queue this recording again.');
      return;
    }
    // Follow the new job the way a fresh upload does, so the user sees progress
    // instead of a card that silently flips to "queued".
    if (body.job_id && typeof window.attachToJob === 'function') {
      window.attachToJob(body.job_id);
    }
    fetchLibrary();
  } catch (err) {
    alert(err.message);
  }
}
