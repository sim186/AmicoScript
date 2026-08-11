// Showing search results in the library.
//
// Typing happens in the command palette (command-palette.js), which shows the
// first few hits inline. This is the other half: taking a query and turning the
// library list into everything it matches.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { fetchFolders } from './folders.js';
import { fetchLibrary, renderLibrary } from './library.js';
import { hexToRgba, state } from './state.js';
import { switchTab } from './tabs.js';
import { fetchTags } from './tags.js';

const MAX_RESULTS = 100;

export async function showSearchResultsInLibrary(q) {
  if (!q) {
    state.library.selectedFolderId = null;
    state.library.selectedTagId = null;
    fetchLibrary();
    return;
  }
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=${MAX_RESULTS}`);
    if (!res.ok) return;
    const rows = await res.json();

    // Show the empty state immediately rather than after a round of detail
    // fetches that have nothing to fetch.
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
