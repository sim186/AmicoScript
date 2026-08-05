// Global search across the library.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { fetchFolders, selectFolder } from './folders.js';
import { fetchLibrary, openRecording, renderLibrary } from './library.js';
import { hexToRgba, state } from './state.js';
import { switchTab } from './tabs.js';
import { fetchTags } from './tags.js';
import { escHtml, fmtDur } from './transcript.js';

let _globalSearchTimer = null;

export function initGlobalSearch() {
  const input = document.getElementById('global-search-input');
  const dropdown = document.getElementById('global-search-dropdown');

  input.addEventListener('input', () => {
    clearTimeout(_globalSearchTimer);
    const q = input.value.trim();

    // reset if blank
    if (!q) {
      dropdown.classList.add('hidden');
      dropdown.innerHTML = '';
      if (state.activeTab === 'library') {
        selectFolder(null); // restore default library view and reset labels
      }
      return;
    }

    _globalSearchTimer = setTimeout(() => {
      // If we're in the library, filter the library list live
      if (state.activeTab === 'library') {
        dropdown.classList.add('hidden');
        doSearchToLibrary(q);
      } else {
        // Otherwise, show the global results dropdown
        runGlobalSearch(q);
      }
    }, 250);
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Escape') { dropdown.classList.add('hidden'); input.blur(); }
    if (e.key === 'Enter') {
      e.preventDefault();
      const q = input.value.trim();
      if (q) {
        doSearchToLibrary(q);
        dropdown.classList.add('hidden');
        input.blur();
      }
    }
  });

  document.addEventListener('click', e => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.classList.add('hidden');
    }
  });
}

async function runGlobalSearch(q) {
  const dropdown = document.getElementById('global-search-dropdown');
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=15`);
    if (!res.ok) return;
    const results = await res.json();
    if (!results.length) {
      dropdown.innerHTML = '<div class="search-dropdown-item text-xs text-slate-400">No results found</div>';
      dropdown.classList.remove('hidden');
      return;
    }
    dropdown.innerHTML = '';
    results.forEach(r => {
      const item = document.createElement('div');
      item.className = 'search-dropdown-item';
      const dur = r.duration ? fmtDur(r.duration) : '';
      item.innerHTML = `
    <p class="text-xs font-semibold text-slate-700 truncate">${escHtml(r.filename)}</p>
    <p class="text-xs text-slate-400 mt-0.5">${dur ? dur + ' · ' : ''}${r.snippet}</p>
  `;
      item.onclick = () => {
        dropdown.classList.add('hidden');
        document.getElementById('global-search-input').value = '';
        const rec = { id: r.recording_id, filename: r.filename, status: 'done', duration: r.duration, tags: [] };
        openRecording(rec);
      };
      dropdown.appendChild(item);
    });
    dropdown.classList.remove('hidden');
  } catch (_) { }
}

async function doSearchToLibrary(q) {
  if (!q) {
    state.library.selectedFolderId = null;
    state.library.selectedTagId = null;
    fetchLibrary();
    return;
  }
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=100`);
    if (!res.ok) return;
    const rows = await res.json();

    // If no results, show empty state immediately
    if (rows.length === 0) {
      state.library.recordings = [];
      renderLibrary();
      if (state.activeTab !== 'library') switchTab('library');
      return;
    }

    // Fetch recording details in parallel
    const recPromises = rows.map(r => fetch(`/api/recordings/${r.recording_id}`).then(rp => rp.ok ? rp.json() : null).catch(() => null));
    const recs = (await Promise.all(recPromises)).filter(Boolean);
    state.library.recordings = recs;

    // Clear selection so the "All recordings" header matches the filtered view
    state.library.selectedFolderId = null;
    state.library.selectedTagId = null;

    // Update breadcrumb/label
    const label = document.getElementById('library-folder-label');
    if (label) {
      label.textContent = `Search results for "${q}"`;
      label.style.backgroundColor = hexToRgba('#6c63ff', 0.09);
    }

    renderLibrary();
    if (state.activeTab !== 'library') switchTab('library');

    // Refresh counts in case names/tags were updated
    try { fetchFolders(); fetchTags(); } catch (_) { }
  } catch (err) {
    console.error('Search to library failed', err);
  }
}
