// Folder tree.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { throwIfFailed } from './errors.js';
import { fetchLibrary, moveRecordingToFolder } from './library.js';
import { setLibraryAccent, state } from './state.js';
import { switchTab } from './tabs.js';
import { fetchTags } from './tags.js';
import { escHtml } from './transcript.js';

export async function fetchFolders() {
  try {
    const res = await fetch('/api/folders');
    if (!res.ok) return;
    state.library.folders = await res.json();
    renderFolderTree();
  } catch (_) { }
}

function renderFolderTree() {
  const tree = document.getElementById('folder-tree');
  const folders = state.library.folders;
  // Build a tree from flat list
  const roots = folders.filter(f => !f.parent_id);
  tree.innerHTML = '';
  roots.forEach(f => tree.appendChild(buildFolderNode(f, folders)));
}

function buildFolderNode(folder, all) {
  const children = all.filter(f => f.parent_id === folder.id);
  const hasChildren = children.length > 0;
  const wrapper = document.createElement('div');

  const item = document.createElement('div');
  item.className = 'folder-item' + (state.library.selectedFolderId === folder.id ? ' active' : '');
  // store folder id on the DOM node for reliable matching
  item.dataset.folderId = String(folder.id);
  item.setAttribute('role', 'treeitem');
  // click selects folder (ignore clicks on interactive subelements)
  item.addEventListener('click', (e) => {
    if (e.target.closest('.folder-arrow') || e.target.closest('.folder-rename') || e.target.closest('.popover') || e.target.closest('input') || e.target.closest('button')) return;
    selectFolder(folder.id);
  });
  item.addEventListener('dblclick', (e) => { e.stopPropagation(); window.showEntityDialog('folder', folder); });
  item.title = 'Double-click to rename · Right-click for options';
  item.addEventListener('contextmenu', (e) => { e.preventDefault(); showFolderMenu(e, folder); });

  // Drag & drop support: allow dropping recordings onto a folder
  item.addEventListener('dragover', (e) => { e.preventDefault(); item.classList.add('drag-over'); });
  item.addEventListener('dragleave', () => item.classList.remove('drag-over'));
  item.addEventListener('drop', (e) => {
    e.preventDefault(); item.classList.remove('drag-over');
    const recId = e.dataTransfer.getData('text/plain');
    if (recId) moveRecordingToFolder(recId, folder.id);
  });

  const arrow = hasChildren
    ? `<svg class="folder-arrow w-3 h-3 shrink-0 text-slate-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>`
    : `<span class="w-3 shrink-0"></span>`;

  // use server-provided color_code
  const useColor = folder && folder.color_code;
  const colorDot = useColor ? `<span class="w-2.5 h-2.5 rounded-full shrink-0 mr-2" style="background:${escHtml(folder.color_code)}"></span>` : '';
  const countBadge = typeof folder.count !== 'undefined' ? `<span class="text-xs text-slate-400 ml-2">(${escHtml(String(folder.count))})</span>` : '';
  item.innerHTML = `
${arrow}
<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 shrink-0 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
  <path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/>
</svg>
${colorDot}
<span class="flex-1 truncate">${escHtml(folder.name)}</span>
${countBadge}
  `;
  wrapper.appendChild(item);

  if (hasChildren) {
    const childContainer = document.createElement('div');
    childContainer.className = 'folder-children';
    children.forEach(c => childContainer.appendChild(buildFolderNode(c, all)));
    wrapper.appendChild(childContainer);

    item.querySelector('.folder-arrow')?.addEventListener('click', e => {
      e.stopPropagation();
      childContainer.classList.toggle('hidden');
      item.querySelector('.folder-arrow')?.classList.toggle('open');
    });
  }

  return wrapper;
}

export function selectFolder(folderId) {
  state.library.selectedFolderId = folderId;
  state.library.selectedTagId = null;
  // Update active state on folder items using the data attribute
  document.querySelectorAll('.folder-item').forEach(el => el.classList.remove('active'));
  document.getElementById('folder-all').classList.toggle('active', !folderId);
  if (folderId) {
    document.querySelectorAll('.folder-item').forEach(el => {
      if (el.dataset && String(el.dataset.folderId) === String(folderId)) el.classList.add('active');
    });
  }
  const labelEl = document.getElementById('library-folder-label');
  const folder = folderId ? state.library.folders.find(f => String(f.id) === String(folderId)) : null;
  labelEl.textContent = folderId ? (folder?.name || 'Folder') : 'All recordings';
  // Apply subtle background accent to the library panel and header
  if (folder && folder.color_code) setLibraryAccent(folder.color_code);
  else setLibraryAccent(null);

  fetchLibrary();
  // Refresh tag list counts when folder changes
  fetchTags();
  if (state.activeTab !== 'library') switchTab('library');
}

function showFolderMenu(e, folder) {
  document.getElementById('folder-ctx-menu')?.remove();
  const menu = document.createElement('div');
  menu.id = 'folder-ctx-menu';
  menu.className = 'absolute z-50 bg-white border border-slate-200 rounded-lg shadow-lg py-1 text-sm';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';

  const renameBtn = document.createElement('button');
  renameBtn.className = 'block w-full text-left px-4 py-1.5 hover:bg-slate-50';
  renameBtn.textContent = 'Rename / Color';
  renameBtn.addEventListener('click', () => { menu.remove(); window.showEntityDialog('folder', folder); });

  const deleteBtn = document.createElement('button');
  deleteBtn.className = 'block w-full text-left px-4 py-1.5 hover:bg-slate-50 text-red-500';
  deleteBtn.textContent = 'Delete';
  deleteBtn.addEventListener('click', () => { deleteFolderConfirm(folder.id); menu.remove(); });

  menu.appendChild(renameBtn);
  menu.appendChild(deleteBtn);
  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener('click', () => menu.remove(), { once: true }), 10);
}

export async function createFolder(name, parentId, color) {
  if (!name) return;
  const fd = new FormData();
  fd.append('name', name);
  if (parentId) fd.append('parent_id', parentId);
  if (color) fd.append('color_code', color);
  try {
    const res = await fetch('/api/folders', { method: 'POST', body: fd });
    await throwIfFailed(res, 'The request failed.');
    const newFolder = await res.json().catch(() => null);
    await fetchFolders();
    // ensure library view reflects new folder immediately
    try { await fetchLibrary(); } catch (_) { }
  } catch (err) { alert(`Create folder failed: ${err.message}`); }
}

async function deleteFolderConfirm(folderId) {
  if (!confirm('Delete folder? Recordings inside will be moved to All Recordings.')) return;
  try {
    const res = await fetch(`/api/folders/${folderId}`, { method: 'DELETE' });
    await throwIfFailed(res, 'The request failed.');
    if (state.library.selectedFolderId === folderId) selectFolder(null);
    await fetchFolders();
    fetchLibrary();
  } catch (err) { alert(`Delete folder failed: ${err.message}`); }
}
