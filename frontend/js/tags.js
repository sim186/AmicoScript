// Tag management and assignment.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { createFolder, fetchFolders } from './folders.js';
import { fetchLibrary } from './library.js';
import { PALETTE, setLibraryAccent, state } from './state.js';
import { switchTab } from './tabs.js';

export async function fetchTags() {
  try {
    const params = new URLSearchParams();
    if (state.library.selectedFolderId) params.set('folder_id', state.library.selectedFolderId);
    const res = await fetch('/api/tags' + (params.toString() ? ('?' + params.toString()) : ''));
    if (!res.ok) return;
    state.library.tags = await res.json();
    renderTagSidebar();
  } catch (_) { }
}

function renderTagSidebar() {
  const list = document.getElementById('tag-list');
  list.innerHTML = '';
  state.library.tags.forEach(tag => {
    const container = document.createElement('span');
    container.className = 'inline-flex items-center gap-2';

    const pill = document.createElement('span');
    pill.className = 'tag-pill' + (state.library.selectedTagId === tag.id ? ' ring-2 ring-offset-1 ring-slate-400' : '');
    pill.dataset.tagId = tag.id;
    pill.dataset.tagName = tag.name;
    pill.style.background = tag.color_code;
    pill.textContent = tag.name;
    pill.title = 'Click to filter · Double-click to edit · Right-click for options';

    const count = typeof tag.count !== 'undefined' ? Number(tag.count) : null;
    const disabled = state.library.selectedFolderId && (count === 0 || count === null);
    if (disabled) {
      pill.classList.add('opacity-40', 'cursor-not-allowed');
      pill.onclick = (e) => { e.stopPropagation(); /* disabled */ };
    } else {
      pill.classList.add('cursor-pointer');
      pill.onclick = () => filterByTag(tag.id, tag.name);
    }
    // double-click will open the modal editor
    pill.ondblclick = (e) => { e.stopPropagation(); window.showEntityDialog('tag', tag); };
    pill.oncontextmenu = (e) => { e.preventDefault(); showTagMenu(e, tag); };

    container.appendChild(pill);
    if (count !== null) {
      const badge = document.createElement('span');
      badge.className = 'text-xs text-slate-400';
      badge.textContent = count;
      container.appendChild(badge);
    }
    list.appendChild(container);
  });
}

export function filterByTag(tagId, tagName) {
  // Toggle behavior: clicking the active tag clears the filter
  if (state.library.selectedTagId === tagId) {
    clearTagFilter();
    return;
  }
  state.library.selectedTagId = tagId;
  renderTagSidebar();
  const filterEl = document.getElementById('lib-tag-filter');
  const filterName = document.getElementById('lib-tag-filter-name');
  filterEl.classList.remove('hidden');
  filterName.textContent = tagName;
  fetchLibrary();
  if (state.activeTab !== 'library') switchTab('library');
}

export function clearTagFilter() {
  state.library.selectedTagId = null;
  renderTagSidebar();
  document.getElementById('lib-tag-filter').classList.add('hidden');
  fetchLibrary();
}

async function deleteTagConfirm(tag) {
  if (!confirm(`Delete tag "${tag.name}"?`)) return;
  try {
    const res = await fetch(`/api/tags/${tag.id}`, { method: 'DELETE' });
    if (!res.ok) { const t = await res.text().catch(() => ''); throw new Error(`HTTP ${res.status}${t ? ': ' + t.slice(0, 200) : ''}`); }
    if (state.library.selectedTagId === tag.id) clearTagFilter();
    await fetchTags();
    fetchLibrary();
  } catch (err) { alert(`Delete tag failed: ${err.message}`); }
}

async function createTag(name, color) {
  if (!name) return;
  const fd = new FormData();
  fd.append('name', name);
  fd.append('color_code', color || '#6c63ff');
  try {
    const res = await fetch('/api/tags', { method: 'POST', body: fd });
    if (!res.ok) { const t = await res.text().catch(() => ''); throw new Error(`HTTP ${res.status}${t ? ': ' + t.slice(0, 200) : ''}`); }
    await fetchTags();
    // ensure library cards (popovers) get updated with the new tag list
    try { await fetchLibrary(); } catch (_) { }
  } catch (err) { alert(`Create tag failed: ${err.message}`); }
}

function showTagMenu(e, tag) {
  document.getElementById('tag-ctx-menu')?.remove();
  const menu = document.createElement('div');
  menu.id = 'tag-ctx-menu';
  menu.className = 'absolute z-50 bg-white border border-slate-200 rounded-lg shadow-lg py-1 text-sm';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';

  const renameBtn = document.createElement('button');
  renameBtn.className = 'block w-full text-left px-4 py-1.5 hover:bg-slate-50';
  renameBtn.textContent = 'Rename / Color';
  renameBtn.addEventListener('click', () => { menu.remove(); window.showEntityDialog('tag', tag); });

  const deleteBtn = document.createElement('button');
  deleteBtn.className = 'block w-full text-left px-4 py-1.5 hover:bg-slate-50 text-red-500';
  deleteBtn.textContent = 'Delete';
  deleteBtn.addEventListener('click', () => { menu.remove(); deleteTagConfirm(tag); });

  menu.appendChild(renameBtn);
  menu.appendChild(deleteBtn);
  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener('click', () => menu.remove(), { once: true }), 10);
}

window._entityEditContext = null;

window.showEntityDialog = function (type, obj, parentId) {
  try {
    if (typeof obj === 'string') obj = JSON.parse(obj);
  } catch (_) { }
  const modal = document.getElementById('entity-edit-modal');
  if (!modal) return;
  const isNew = !obj || !obj.id;
  window._entityEditContext = { type, id: isNew ? null : obj.id, parentId: parentId || null };

  const title = document.getElementById('entity-edit-title');
  const sub = document.getElementById('entity-edit-sub');
  const nameLabel = document.getElementById('entity-edit-name-label');
  const saveBtn = document.getElementById('entity-edit-save');
  const parentInfo = document.getElementById('entity-edit-parent-info');

  if (isNew) {
    title.textContent = type === 'folder' ? 'New Folder' : 'New Tag';
    sub.textContent = type === 'folder' ? 'Give your folder a name and choose a color.' : 'Give your tag a name and choose a color.';
    if (saveBtn) saveBtn.textContent = 'Create';
  } else {
    title.textContent = type === 'folder' ? 'Folder Settings' : 'Tag Settings';
    sub.textContent = type === 'folder' ? 'Change the appearance of your folder.' : 'Change the appearance of your tag.';
    if (saveBtn) saveBtn.textContent = 'Save Changes';
  }
  if (nameLabel) nameLabel.textContent = type === 'folder' ? 'Folder Name' : 'Tag Name';
  if (parentInfo) {
    if (type === 'folder' && isNew) {
      const pid = parentId || null;
      const parentName = pid ? (state.library.folders.find(f => f.id === pid)?.name || 'Folder') : 'All recordings';
      parentInfo.textContent = 'Parent: ' + parentName;
      parentInfo.classList.remove('hidden');
    } else {
      parentInfo.classList.add('hidden');
    }
  }

  const nameEl = document.getElementById('entity-edit-name');
  nameEl.value = (obj && obj.name) ? obj.name : '';

  // palette: render inline circular buttons
  const selectedColor = (obj && (obj.color_code || obj.color)) ? (obj.color_code || obj.color) : (PALETTE[0] || '#6c63ff');
  const hiddenColor = document.getElementById('entity-edit-color');
  if (hiddenColor) hiddenColor.value = selectedColor;
  const sw = document.getElementById('entity-edit-color-swatch');
  if (sw) sw.style.background = selectedColor;

  const pal = document.getElementById('entity-edit-palette');
  if (pal) {
    pal.innerHTML = '';
    PALETTE.forEach(c => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'ent-palette-btn';
      b.style.background = c;
      b.dataset.color = c;
      b.title = c;
      const dot = document.createElement('span'); dot.className = 'ent-dot';
      b.appendChild(dot);
      if (c.toLowerCase() === (selectedColor || '').toLowerCase()) b.classList.add('selected');
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        pal.querySelectorAll('.ent-palette-btn').forEach(x => x.classList.remove('selected'));
        b.classList.add('selected');
        if (hiddenColor) hiddenColor.value = c;
        if (sw) sw.style.background = c;
      });
      pal.appendChild(b);
    });
  }

  modal.classList.remove('hidden');
  // trap focus minimally
  document.body.style.overflow = 'hidden';
  nameEl.focus(); nameEl.select();
};

window.hideEntityDialog = function () {
  const modal = document.getElementById('entity-edit-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  document.body.style.overflow = '';
  window._entityEditContext = null;
};

window.saveEntityDialog = async function () {
  const ctx = window._entityEditContext;
  if (!ctx) return;
  const name = (document.getElementById('entity-edit-name').value || '').trim();
  const color = (document.getElementById('entity-edit-color') && document.getElementById('entity-edit-color').value) || '#6c63ff';
  if (!name) return alert('Name required');
  try {
    const fd = new FormData();
    fd.append('name', name);
    fd.append('color_code', color);
    if (ctx.type === 'folder') {
      if (ctx.id) {
        const res = await fetch(`/api/folders/${ctx.id}`, { method: 'PATCH', body: fd });
        if (!res.ok) { const t = await res.text().catch(() => ''); throw new Error(t || 'HTTP ' + res.status); }
        await fetchFolders();
        fetchTags();
        fetchLibrary();
        if (String(state.library.selectedFolderId) === String(ctx.id)) setLibraryAccent(color);
      } else {
        await createFolder(name, ctx.parentId, color);
      }
    } else if (ctx.type === 'tag') {
      if (ctx.id) {
        const res = await fetch(`/api/tags/${ctx.id}`, { method: 'PATCH', body: fd });
        if (!res.ok) { const t = await res.text().catch(() => ''); throw new Error(t || 'HTTP ' + res.status); }
        await fetchTags();
        await fetchLibrary();
      } else {
        await createTag(name, color);
      }
    }
    hideEntityDialog();
  } catch (err) { alert('Save failed: ' + (err.message || err)); }
};
