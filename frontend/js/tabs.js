// Top-level tab switching and Whisper model cards.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { closeDrawer, isMobile } from './layout.js';
import { fetchLibrary } from './library.js';
import { _saveTranscriptionDefaults } from './prefs.js';
import { state } from './state.js';
import { clientLog } from './upload.js';

export function switchTab(name) {
  clientLog(`Tab switched → ${name}`);
  state.activeTab = name;
  // Auto-close the drawer when switching tabs on mobile
  if (isMobile()) closeDrawer();

  document.querySelectorAll('.tab-btn').forEach(btn => {
    const active = btn.id === `tab-btn-${name}`;
    btn.setAttribute('aria-selected', String(active));
    btn.classList.toggle('border-brand', active);
    btn.classList.toggle('text-brand', active);
    btn.classList.toggle('border-transparent', !active);
    btn.classList.toggle('text-slate-500', !active);
  });

  document.querySelectorAll('.tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `tab-${name}`);
  });

  document.querySelectorAll('.sidebar-panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `sidebar-${name}`);
  });

  document.getElementById('export-bar').classList.toggle('hidden', name !== 'transcript');
  // Close AI hub when leaving transcript tab
  if (name !== 'transcript') {
    // Hub auto-closes via its own logic if needed
  }
  if (name === 'library') fetchLibrary();
}

document.getElementById('tab-btn-transcribe').addEventListener('click', () => switchTab('transcribe'));

document.getElementById('tab-btn-transcript').addEventListener('click', () => {
  if (!document.getElementById('tab-btn-transcript').disabled) switchTab('transcript');
});

document.getElementById('tab-btn-library').addEventListener('click', () => switchTab('library'));

const MODELS = [
  { id: 'tiny', name: 'Tiny', params: '~39M', speed: 5, note: 'Fastest · lowest accuracy' },
  { id: 'base', name: 'Base', params: '~74M', speed: 4, note: 'Fast' },
  { id: 'small', name: 'Small', params: '~244M', speed: 3, note: 'Balanced' },
  { id: 'medium', name: 'Medium', params: '~769M', speed: 2, note: 'High accuracy' },
  { id: 'large-v2', name: 'Large v2', params: '~1.5B', speed: 1, note: 'Best accuracy' },
  { id: 'large-v3', name: 'Large v3', params: '~1.5B', speed: 1, note: 'Best accuracy+' },
  { id: 'large-v3-turbo', name: 'Large v3 Turbo', params: '~809M', speed: 3, note: 'Near-large accuracy, much faster' },
  { id: 'distil-large-v3', name: 'Distil Large v3', params: '~756M', speed: 4, note: 'Fast · English works best' },
  // Not Whisper: needs `pip install "amicoscript[fast]"`. No translation, no word timing.
  { id: 'parakeet-v3', name: 'Parakeet v3', params: '~600M', speed: 5, note: 'Fastest on CPU · 25 European languages' },
  { id: 'parakeet-v2', name: 'Parakeet v2', params: '~600M', speed: 5, note: 'Fastest on CPU · English only' },
];

export function renderModelGrid() {
  const sel = document.getElementById('model-select');
  sel.innerHTML = '';
  MODELS.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = `${m.name} (${m.params}) — ${m.note}`;
    if (m.id === state.selectedModel) opt.selected = true;
    sel.appendChild(opt);
  });
}

export function selectModel(id) {
  clientLog(`Model selected: ${id}`);
  state.selectedModel = id;
  localStorage.setItem('selectedModel', id);
  _saveTranscriptionDefaults();
  const sel = document.getElementById('model-select');
  if (sel) sel.value = id;
}
