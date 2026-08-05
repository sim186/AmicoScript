// Shared application state, DOM element handles and constants.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

export const state = {
  activeTab: 'transcribe',
  selectedFile: null,
  selectedModel: 'small',
  selectedLanguage: '',
  diarize: false,
  cloudPower: false,
  colabUrl: '',
  hfToken: '',
  numSpeakers: '',
  minSpeakers: '',
  maxSpeakers: '',

  jobId: null,
  recordingId: null,       // recording_id returned by /api/transcribe
  jobStatus: null,
  jobProgress: 0,
  jobMessage: '',
  eventSource: null,
  jobLogs: [],
  clientLogs: [],           // client-side action log (persistent across jobs)
  logPollTimer: null,

  wavesurfer: null,

  batchMode: false,
  batchQueue: [],   // [{ file, jobId, recordingId, status, progress, message }]

  result: null,
  audioUrl: null,
  currentSegmentId: null,
  searchQuery: '',
  filteredSegments: [],
  activeSpeakerFilter: null,
  selectedSegmentIds: new Set(),
  lastSelectedSegmentId: null,

  // Library mode — set when opening a recording from the library
  activeRecordingId: null,

  // AI Analysis
  analysisJobId: null,
  analysisEventSource: null,
  rawAiText: '',

  // Library data
  library: {
    recordings: [],
    folders: [],
    tags: [],
    selectedFolderId: null,
    selectedTagId: null,
    sort: 'created_at',
    order: 'desc',
    selectedIds: new Set(),
    lastSelectedIdx: null,  // for Shift+click range selection
  },

  // Currently open recording (set when opening from library)
  currentRecording: null,
};

export const PALETTE = ['#6c63ff', '#f59e0b', '#10b981', '#f472b6', '#60a5fa', '#fb7185', '#a78bfa', '#fbbf24', '#16a34a', '#ef4444', '#ff0000', '#111111', '#1877f2', '#e1306c', '#25f4ee', '#1ab7ea', '#9146ff'];

window.__paletteMap = window.__paletteMap || {};

export function attachPaletteToInput(inputId, sizePx = 28) {
  const existing = document.getElementById(inputId);
  if (!existing) return;
  const currentVal = (existing.value || '#6c63ff').toLowerCase();
  const hidden = document.createElement('input');
  hidden.type = 'hidden';
  hidden.id = inputId;
  hidden.value = currentVal;
  hidden.name = existing.name || inputId;

  // compact combobox button (shows current swatch + caret)
  const container = document.createElement('div');
  container.className = 'relative inline-block';
  container.style.display = 'inline-block';

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'flex items-center gap-1 p-0 border-0 bg-transparent focus:outline-none';
  btn.style.padding = '0';
  btn.style.background = 'transparent';

  const sw = document.createElement('span');
  sw.style.display = 'inline-block';
  sw.style.width = sizePx + 'px';
  sw.style.height = sizePx + 'px';
  sw.style.borderRadius = '6px';
  sw.style.background = currentVal;
  sw.style.border = '1px solid rgba(0,0,0,0.06)';

  const caret = document.createElement('span');
  caret.className = 'text-slate-400';
  caret.style.fontSize = '12px';
  caret.style.lineHeight = '1';
  caret.textContent = '▾';

  btn.appendChild(sw);
  btn.appendChild(caret);

  // popover with palette grid (rendered as top-level overlay to avoid clipping)
  const pop = document.createElement('div');
  pop.className = 'bg-white border border-slate-200 rounded-lg shadow-lg p-2 grid gap-2';
  pop.style.display = 'none';
  pop.style.gridTemplateColumns = 'repeat(5, 1fr)';
  pop.style.minWidth = '120px';
  pop.style.maxWidth = '220px';
  pop.style.boxSizing = 'border-box';
  pop.style.position = 'absolute';
  pop.style.zIndex = '10000';

  PALETTE.forEach(c => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'rounded-full border-2 border-transparent focus:outline-none';
    b.style.width = (sizePx) + 'px';
    b.style.height = (sizePx) + 'px';
    b.style.background = c;
    b.dataset.color = c.toLowerCase();
    b.setAttribute('aria-label', `Color ${c}`);
    if (c.toLowerCase() === currentVal) b.classList.add('ring-2', 'ring-offset-1', 'ring-slate-400');
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      pop.querySelectorAll('button').forEach(x => x.classList.remove('ring-2', 'ring-offset-1', 'ring-slate-400'));
      b.classList.add('ring-2', 'ring-offset-1', 'ring-slate-400');
      hidden.value = c;
      try { hidden.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) { }
      sw.style.background = c;
      pop.style.display = 'none';
    });
    pop.appendChild(b);
  });

  // toggle popover — position it relative to the button but rendered in document.body
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (pop.style.display === 'none' || !pop.style.display) {
      // ensure pop is in body so it's not clipped by overflow
      if (!document.body.contains(pop)) document.body.appendChild(pop);
      pop.style.display = 'grid';
      // position
      const rect = btn.getBoundingClientRect();
      // temporarily make visible to measure width
      pop.style.left = '0px'; pop.style.top = '0px';
      const popW = Math.max(pop.offsetWidth || 160, 120);
      const left = Math.min(Math.max(rect.right - popW + window.scrollX, 8 + window.scrollX), Math.max(window.innerWidth - popW - 8 + window.scrollX, 8 + window.scrollX));
      const top = rect.bottom + 6 + window.scrollY;
      pop.style.left = `${left}px`;
      pop.style.top = `${top}px`;

      // close handlers
      setTimeout(() => {
        function closeHandler(ev) {
          if (!container.contains(ev.target) && !pop.contains(ev.target)) {
            pop.style.display = 'none';
            document.removeEventListener('click', closeHandler);
            window.removeEventListener('scroll', closeOnScroll);
            window.removeEventListener('resize', closeOnScroll);
          }
        }
        function closeOnScroll() { pop.style.display = 'none'; document.removeEventListener('click', closeHandler); window.removeEventListener('scroll', closeOnScroll); window.removeEventListener('resize', closeOnScroll); }
        document.addEventListener('click', closeHandler);
        window.addEventListener('scroll', closeOnScroll);
        window.addEventListener('resize', closeOnScroll);
      }, 0);
    } else {
      pop.style.display = 'none';
    }
  });

  container.appendChild(hidden);
  container.appendChild(btn);
  // do not append pop as child — it's rendered at body level to avoid clipping
  existing.replaceWith(container);
  window.__paletteMap[inputId] = { container, hidden, btn, sw, pop };
}

function setPaletteValue(inputId, color) {
  const rec = window.__paletteMap && window.__paletteMap[inputId];
  if (!rec) {
    const el = document.getElementById(inputId);
    if (el) el.value = color;
    return;
  }
  rec.hidden.value = color;
  if (rec.sw) rec.sw.style.background = color;
  if (rec.pop) {
    rec.pop.querySelectorAll('button').forEach(b => b.classList.remove('ring-2', 'ring-offset-1', 'ring-slate-400'));
    const match = Array.from(rec.pop.querySelectorAll('button')).find(b => (b.dataset.color || '').toLowerCase() === (color || '').toLowerCase());
    if (match) match.classList.add('ring-2', 'ring-offset-1', 'ring-slate-400');
    try { rec.hidden.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) { }
  }
}

export function hexToRgba(hex, alpha) {
  if (!hex) return null;
  let h = String(hex).replace('#', '').trim();
  if (h.length === 3) h = h.split('').map(c => c + c).join('');
  if (h.length !== 6) return null;
  const bigint = parseInt(h, 16);
  const r = (bigint >> 16) & 255;
  const g = (bigint >> 8) & 255;
  const b = bigint & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function setLibraryAccent(hex) {
  const panel = document.getElementById('tab-library');
  const label = document.getElementById('library-folder-label');
  if (!panel || !label) return;
  if (hex) {
    const bg = hexToRgba(hex, 0.04) || '';
    const hdr = hexToRgba(hex, 0.09) || '';
    panel.style.backgroundColor = bg;
    panel.style.transition = 'background-color 180ms ease';
    // style header label gently
    label.style.backgroundColor = hdr;
    label.style.padding = '6px 10px';
    label.style.borderRadius = '8px';
    label.style.transition = 'background-color 180ms ease, padding 120ms ease';
  } else {
    panel.style.backgroundColor = '';
    label.style.backgroundColor = '';
    label.style.padding = '';
    label.style.borderRadius = '';
  }
}
