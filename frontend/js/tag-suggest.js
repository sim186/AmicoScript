// LLM tag suggestions for the open recording.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.
//
// The model only ever proposes. A suggestion becomes a tag when the user
// clicks it — a chip that already exists in the library is attached, a new
// one is created first — so nothing relabels a library on its own.

import { throwIfFailed } from './errors.js';
import { fetchLibrary } from './library.js';
import { state } from './state.js';
import { fetchTags } from './tags.js';
import { renderRecordingMeta } from './transcript.js';

function el(id) {
  return document.getElementById(id);
}

function setError(message) {
  const box = el('tag-suggest-error');
  if (!box) return;
  box.textContent = message || '';
  box.classList.toggle('hidden', !message);
}

function setBusy(busy) {
  const btn = el('tag-suggest-btn');
  const label = el('tag-suggest-label');
  if (!btn || !label) return;
  btn.disabled = busy;
  btn.classList.toggle('opacity-50', busy);
  btn.classList.toggle('cursor-wait', busy);
  label.textContent = busy ? 'Reading the transcript…' : 'Suggest tags';
}

/** Show or hide the whole section — it only means anything for a saved recording. */
export function refreshTagSuggestUI() {
  const section = el('tag-suggest-section');
  if (!section) return;
  section.classList.toggle('hidden', !state.activeRecordingId);
  el('tag-suggest-results').innerHTML = '';
  setError('');
  setBusy(false);
}

function renderChips(suggestions) {
  const box = el('tag-suggest-results');
  box.innerHTML = '';
  if (!suggestions.length) {
    setError('No new tags to suggest for this recording.');
    return;
  }

  suggestions.forEach(s => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className =
      'tag-pill cursor-pointer border border-dashed border-slate-300 bg-white text-slate-600 ' +
      'hover:border-brand hover:text-brand transition focus:outline-none focus:ring-1 focus:ring-brand';
    chip.textContent = '+ ' + s.name;
    chip.title = s.tag_id
      ? `Apply the existing tag "${s.name}"`
      : `Create "${s.name}" and apply it`;
    chip.addEventListener('click', () => applySuggestion(s, chip));
    box.appendChild(chip);
  });
}

async function applySuggestion(suggestion, chip) {
  const recId = state.activeRecordingId;
  if (!recId) return;
  chip.disabled = true;
  chip.classList.add('opacity-50');
  try {
    let tagId = suggestion.tag_id;
    if (!tagId) {
      const fd = new FormData();
      fd.append('name', suggestion.name);
      const res = await fetch('/api/tags', { method: 'POST', body: fd });
      await throwIfFailed(res, 'Could not create the tag.');
      tagId = (await res.json()).id;
    }
    const link = await fetch(`/api/recordings/${recId}/tags/${tagId}`, { method: 'POST' });
    await throwIfFailed(link, 'Could not apply the tag.');

    chip.remove();
    await fetchTags();
    await fetchLibrary();

    // Redraw the pill strip from fresh metadata. Re-running the whole
    // transcript render would rebuild the waveform and lose the playhead.
    const recRes = await fetch(`/api/recordings/${recId}`);
    if (recRes.ok) {
      state.currentRecording = await recRes.json();
      renderRecordingMeta();
    }
  } catch (err) {
    chip.disabled = false;
    chip.classList.remove('opacity-50');
    setError(err.message || String(err));
  }
}

async function suggestTags() {
  const recId = state.activeRecordingId;
  if (!recId) return;
  setError('');
  setBusy(true);
  try {
    const res = await fetch(`/api/recordings/${recId}/suggest-tags`, { method: 'POST' });
    await throwIfFailed(res, 'Could not suggest tags.');
    renderChips((await res.json()).suggestions || []);
  } catch (err) {
    setError(err.message || String(err));
  } finally {
    setBusy(false);
  }
}

export function initTagSuggest() {
  el('tag-suggest-btn')?.addEventListener('click', suggestTags);
}
