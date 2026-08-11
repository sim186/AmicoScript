// The command palette — one box for finding anything and doing anything.
//
// It answers from four places at once: the commands this app can run, the
// library search endpoint (transcripts, LLM summaries, names), and the folders
// and tags already loaded in state. Commands, folders and tags are matched here
// and appear as you type; recordings come from /api/search, which is debounced
// because it reads the whole library.
//
// A leading character narrows the palette to one of those places, the same
// prefixes the terminal UI uses:
//
//     /  commands        @  recordings        #  folders and tags
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { commandHaystack, listCommands } from './commands.js';
import { selectFolder } from './folders.js';
import { rank } from './fuzzy.js';
import { openRecording } from './library.js';
import { showSearchResultsInLibrary } from './search.js';
import { state } from './state.js';
import { switchTab } from './tabs.js';
import { filterByTag } from './tags.js';
import { escHtml, fmtDur } from './transcript.js';
import { clientLog } from './upload.js';

const SEARCH_DEBOUNCE_MS = 180;
const MAX_COMMANDS = 6;
const MAX_MIXED_COMMANDS = 3;
const MAX_RECORDINGS = 8;
const MAX_ENTITIES = 5;

// Where a hit came from, as the palette says it. The keys are the `kind` and
// `matched_in` values of /api/search.
const KIND_LABEL = {
  transcript: 'Transcript',
  summary: 'Summary',
  title: 'Name',
  tag: 'Tag',
  folder: 'Folder',
};

const MODES = {
  '/': 'commands',
  '@': 'recordings',
  '#': 'entities',
};

let rows = [];
let activeIndex = 0;
let searchTimer = null;
// Answers can arrive out of order; only the newest request may paint.
let requestSeq = 0;
let remote = { query: null, results: [], pending: false };

function el(id) {
  return document.getElementById(id);
}

function isPaletteOpen() {
  return !el('palette-overlay')?.classList.contains('hidden');
}

function openCommandPalette(prefill = '') {
  const overlay = el('palette-overlay');
  if (!overlay) return;
  const input = el('palette-input');
  overlay.classList.remove('hidden');
  input.value = prefill;
  activeIndex = 0;
  remote = { query: null, results: [], pending: false };
  render();
  input.focus();
  input.select();
  if (queryOf(input.value)) scheduleSearch();
}

function closeCommandPalette() {
  el('palette-overlay')?.classList.add('hidden');
  clearTimeout(searchTimer);
  searchTimer = null;
  // Nothing in flight may paint into a closed palette.
  requestSeq++;
}

// --- reading the input ------------------------------------------------------

function modeOf(raw) {
  return MODES[raw.trim()[0]] || 'all';
}

// The query without its mode prefix — "@board" searches for "board".
function queryOf(raw) {
  const trimmed = raw.trim();
  return (MODES[trimmed[0]] ? trimmed.slice(1) : trimmed).trim();
}

// --- the four sources -------------------------------------------------------

// `loose` is for command mode, where the user has already said commands are
// what they want and a subsequence match is welcome. The mixed list is stricter:
// "anna" is a subsequence of "Ask your library", and a recording of an
// interview with Anna should not be buried under commands nobody asked for.
function commandRows(query, loose = false) {
  const needle = query.toLowerCase();
  const matches = rank(query, listCommands(), commandHaystack).filter(
    ({ item }) => loose || !needle || commandHaystack(item).toLowerCase().includes(needle)
  );
  // Nothing typed means the palette is being read as a menu, so show all of it.
  const limit = !needle ? matches.length : loose ? MAX_COMMANDS : MAX_MIXED_COMMANDS;
  return matches
    .slice(0, limit)
    .map(({ item }) => ({
      type: 'command',
      section: item.section,
      title: item.title,
      subtitle: item.hint || '',
      disabled: item.disabled,
      run: item.run,
    }));
}

function entityRows(query) {
  if (!query) return [];
  const folders = (state.library.folders || []).map(f => ({ kind: 'folder', ...f }));
  const tags = (state.library.tags || []).map(t => ({ kind: 'tag', ...t }));
  return rank(query, [...folders, ...tags], e => e.name || '')
    .slice(0, MAX_ENTITIES)
    .map(({ item }) => ({
      type: item.kind,
      section: 'Folders & tags',
      title: item.name,
      subtitle: item.kind === 'folder' ? 'Folder' : 'Tag',
      color: item.color_code,
      id: item.id,
    }));
}

function recordingRows() {
  return remote.results.slice(0, MAX_RECORDINGS).map(hit => ({
    type: 'recording',
    section: 'Library',
    title: hit.alias || hit.filename,
    filename: hit.filename,
    snippet: hit.snippet || '',
    kind: hit.kind,
    matchedIn: hit.matched_in || [],
    duration: hit.duration,
    status: hit.status,
    id: hit.recording_id,
  }));
}

function searchAllRow(query) {
  return {
    type: 'search-all',
    section: 'Library',
    title: `Show every result for “${query}” in the library`,
    query,
  };
}

// --- assembling -------------------------------------------------------------

// Ranking pays no attention to which section a row belongs to, so a ranked
// list can leave "Actions" appearing three times with a "View" between. Each
// section keeps the position of its best row, and its rows keep their order.
function groupBySection(rows) {
  const sections = new Map();
  for (const row of rows) {
    if (!sections.has(row.section)) sections.set(row.section, []);
    sections.get(row.section).push(row);
  }
  return [...sections.values()].flat();
}

function buildRows() {
  const raw = el('palette-input').value;
  const mode = modeOf(raw);
  const query = queryOf(raw);

  if (mode === 'commands') return groupBySection(commandRows(query, true));
  if (mode === 'entities') return entityRows(query);
  if (mode === 'recordings') {
    return query ? [...recordingRows(), searchAllRow(query)] : [];
  }

  // With nothing typed the palette is a menu of what the app can do.
  if (!query) return groupBySection(commandRows('', true));
  return groupBySection([
    ...commandRows(query),
    ...recordingRows(),
    ...entityRows(query),
    searchAllRow(query),
  ]);
}

// --- searching --------------------------------------------------------------

function scheduleSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, SEARCH_DEBOUNCE_MS);
}

async function runSearch() {
  const raw = el('palette-input').value;
  const query = queryOf(raw);
  const mode = modeOf(raw);
  if (!query || mode === 'commands' || mode === 'entities') {
    remote = { query, results: [], pending: false };
    render();
    return;
  }

  const seq = ++requestSeq;
  remote.pending = true;
  render();
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=${MAX_RECORDINGS}`);
    if (seq !== requestSeq) return;
    remote = { query, results: res.ok ? await res.json() : [], pending: false };
  } catch (_) {
    if (seq !== requestSeq) return;
    remote = { query, results: [], pending: false };
  }
  render();
}

// --- rendering --------------------------------------------------------------

// A snippet is library text with <mark> around the match. Escaping it whole
// and putting only those two tags back keeps a transcript that talks about
// <script> from being run by the browser that displays it.
function markedSnippet(text) {
  return escHtml(String(text || ''))
    .replaceAll('&lt;mark&gt;', '<mark>')
    .replaceAll('&lt;/mark&gt;', '</mark>');
}

function rowIcon(row) {
  if (row.type === 'command') return '⌘';
  if (row.type === 'folder') return '▸';
  if (row.type === 'tag') return '#';
  if (row.type === 'search-all') return '⏎';
  return '♪';
}

function rowMeta(row) {
  if (row.type !== 'recording') return '';
  const badges = (row.matchedIn.length ? row.matchedIn : [row.kind])
    .map(kind => KIND_LABEL[kind] || kind)
    .map(label => `<span class="palette-badge">${escHtml(label)}</span>`)
    .join('');
  const dur = row.duration ? `<span class="palette-dur">${fmtDur(row.duration)}</span>` : '';
  return badges + dur;
}

function rowSubtitle(row) {
  if (row.type !== 'recording') return escHtml(row.subtitle || '');
  // The snippet of a name match is the name, which the row already shows. The
  // file behind a renamed recording is worth saying instead; a file that was
  // never renamed has nothing left to add, and the NAME badge says why it is
  // in the list.
  if (row.kind === 'title') {
    return row.title === row.filename ? '' : escHtml(row.filename);
  }
  return markedSnippet(row.snippet);
}

function render() {
  rows = buildRows();
  if (activeIndex >= rows.length) activeIndex = Math.max(0, rows.length - 1);

  const list = el('palette-results');
  const status = el('palette-status');
  status.textContent = remote.pending ? 'Searching…' : '';

  if (!rows.length) {
    const query = queryOf(el('palette-input').value);
    list.innerHTML = `<li class="palette-empty">${
      query ? `Nothing matches “${escHtml(query)}”` : 'Type to search your library'
    }</li>`;
    return;
  }

  // "Show every result" is always offered, so a query that matched nothing
  // would otherwise look like a query with one hit.
  const nothingMatched =
    !remote.pending && rows.length === 1 && rows[0].type === 'search-all';
  const note = nothingMatched
    ? `<li class="palette-empty">Nothing matches “${escHtml(queryOf(el('palette-input').value))}”</li>`
    : '';

  let lastSection = null;
  list.innerHTML = note + rows
    .map((row, i) => {
      const header =
        row.section !== lastSection
          ? `<li class="palette-section" role="presentation">${escHtml(row.section)}</li>`
          : '';
      lastSection = row.section;
      const subtitle = rowSubtitle(row);
      const swatch = row.color
        ? `<span class="palette-swatch" style="background:${escHtml(row.color)}"></span>`
        : `<span class="palette-icon">${rowIcon(row)}</span>`;
      return `${header}
        <li id="palette-row-${i}" class="palette-row${i === activeIndex ? ' active' : ''}${
        row.disabled ? ' disabled' : ''
      }" role="option" aria-selected="${i === activeIndex}" data-index="${i}">
          ${swatch}
          <span class="palette-text">
            <span class="palette-title">${escHtml(row.title)}</span>
            ${subtitle ? `<span class="palette-subtitle">${subtitle}</span>` : ''}
          </span>
          <span class="palette-meta">${rowMeta(row)}</span>
        </li>`;
    })
    .join('');

  el('palette-input').setAttribute('aria-activedescendant', `palette-row-${activeIndex}`);
  list.querySelector('.palette-row.active')?.scrollIntoView({ block: 'nearest' });
}

// --- activation -------------------------------------------------------------

function move(delta) {
  if (!rows.length) return;
  activeIndex = (activeIndex + delta + rows.length) % rows.length;
  render();
}

function activate(index) {
  const row = rows[index];
  if (!row || row.disabled) return;
  closeCommandPalette();

  switch (row.type) {
    case 'command':
      clientLog(`Palette: ${row.title}`);
      row.run();
      break;
    case 'recording':
      // openRecording says no — with a reason — to a recording that is still
      // transcribing, which is why /api/search reports the status at all.
      openRecording({
        id: row.id,
        filename: row.filename,
        status: row.status,
        duration: row.duration,
        tags: [],
      });
      break;
    case 'folder':
      switchTab('library');
      selectFolder(row.id);
      break;
    case 'tag':
      switchTab('library');
      filterByTag(row.id, row.title);
      break;
    case 'search-all':
      showSearchResultsInLibrary(row.query);
      break;
  }
}

// --- wiring -----------------------------------------------------------------

export function initCommandPalette() {
  const overlay = el('palette-overlay');
  if (!overlay) return;
  const input = el('palette-input');
  const list = el('palette-results');

  // The trigger in the header is where the search box used to be, so say which
  // key opens it on this platform rather than guessing at ⌘.
  const isMac = /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);
  const key = el('palette-trigger-key');
  if (key) key.textContent = isMac ? '⌘K' : 'Ctrl K';
  el('palette-trigger')?.addEventListener('click', () => openCommandPalette());

  overlay.addEventListener('mousedown', e => {
    if (e.target === overlay) closeCommandPalette();
  });

  input.addEventListener('input', () => {
    activeIndex = 0;
    render();
    scheduleSearch();
  });

  input.addEventListener('keydown', e => {
    switch (e.key) {
      case 'ArrowDown': e.preventDefault(); move(1); break;
      case 'ArrowUp': e.preventDefault(); move(-1); break;
      case 'Home': e.preventDefault(); activeIndex = 0; render(); break;
      case 'End': e.preventDefault(); activeIndex = rows.length - 1; render(); break;
      case 'Enter':
        e.preventDefault();
        // Enter before the debounce has fired should search, not act on a
        // stale list — but only when there is nothing else sensible to do.
        if (!rows.length) { clearTimeout(searchTimer); runSearch(); return; }
        activate(activeIndex);
        break;
      case 'Escape': e.preventDefault(); closeCommandPalette(); break;
    }
  });

  list.addEventListener('click', e => {
    const row = e.target.closest('.palette-row');
    if (row) activate(Number(row.dataset.index));
  });

  list.addEventListener('mousemove', e => {
    const row = e.target.closest('.palette-row');
    const index = row ? Number(row.dataset.index) : -1;
    if (index >= 0 && index !== activeIndex) {
      activeIndex = index;
      render();
    }
  });

  // Ctrl/Cmd+K works from anywhere, including from inside a text field: the
  // palette is meant to be reachable mid-sentence, which is the whole point of
  // not making it a plain shortcut in shortcuts.js.
  window.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (isPaletteOpen()) closeCommandPalette();
      else openCommandPalette();
    }
  });
}
