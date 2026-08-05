// Ask a question across every transcript, and jump to the answer.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.
//
// The citations are the feature. An answer on its own is a claim about
// recordings the user cannot check; a citation opens the recording at the
// second the passage was spoken, so the claim can be checked in one click.

import { throwIfFailed } from './errors.js';
import { openRecording } from './library.js';
import { state } from './state.js';
import { escHtml } from './transcript.js';

function el(id) {
  return document.getElementById(id);
}

function show() {
  el('lib-chat-panel').classList.remove('hidden');
}

function setNote(message) {
  const note = el('lib-chat-note');
  note.textContent = message || '';
  note.classList.toggle('hidden', !message);
}

function setBusy(busy) {
  const button = el('lib-chat-send');
  button.disabled = busy;
  button.textContent = busy ? 'Reading…' : 'Ask';
  button.classList.toggle('opacity-50', busy);
}

/** Open the cited recording and seek to the moment it was said. */
async function jumpTo(source) {
  const rec = state.library.recordings.find(r => r.id === source.recording_id);
  if (!rec) {
    setNote('That recording is no longer in the library.');
    return;
  }
  const opened = await openRecording(rec);
  if (!opened) return;

  // The waveform is built asynchronously by openRecording; seek once it is
  // ready, and give up quietly rather than spinning forever.
  for (let attempt = 0; attempt < 40; attempt++) {
    const ws = state.wavesurfer;
    const duration = ws && ws.getDuration ? ws.getDuration() : 0;
    if (duration > 0) {
      ws.seekTo(Math.min(source.start / duration, 1));
      return;
    }
    await new Promise(r => setTimeout(r, 100));
  }
}

function renderSources(sources, cited) {
  const box = el('lib-chat-sources');
  box.innerHTML = '';
  const citedSet = new Set(cited || []);

  sources.forEach((source, i) => {
    const number = i + 1;
    const wasCited = citedSet.has(number);
    const row = document.createElement('button');
    row.type = 'button';
    row.className =
      'w-full text-left px-2 py-1.5 rounded-md border text-xs transition focus:outline-none focus:ring-1 focus:ring-brand ' +
      (wasCited
        ? 'border-brand/40 bg-white hover:bg-brand/5'
        : 'border-transparent bg-white/60 hover:bg-white text-slate-500');
    row.innerHTML = `
      <span class="font-semibold ${wasCited ? 'text-brand' : 'text-slate-400'}">[${number}]</span>
      <span class="font-medium text-slate-700">${escHtml(source.title)}</span>
      <span class="text-slate-400">· ${escHtml(source.timestamp)}</span>
      <div class="mt-0.5 text-slate-500 line-clamp-2">${escHtml(source.text.slice(0, 200))}…</div>
    `;
    row.title = 'Open this recording at ' + source.timestamp;
    row.addEventListener('click', () => jumpTo(source));
    box.appendChild(row);
  });
}

async function ask(question) {
  setBusy(true);
  setNote('');
  el('lib-chat-sources').innerHTML = '';
  el('lib-chat-answer').textContent = 'Searching your library…';
  show();

  try {
    const res = await fetch('/api/library/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    await throwIfFailed(res, 'Could not answer that.');
    const body = await res.json();

    if (body.no_matches) {
      el('lib-chat-answer').textContent =
        'Nothing in your library matches that question.';
      setNote(body.pending ? `${body.pending} recording(s) are not indexed yet.` : '');
      return;
    }

    el('lib-chat-answer').textContent = body.answer || '(the model returned nothing)';
    renderSources(body.sources || [], body.cited || []);

    const notes = [];
    if (!body.used_semantic) notes.push('keyword search');
    if (body.pending) notes.push(`${body.pending} recording(s) not indexed yet`);
    setNote(notes.join(' · '));
  } catch (err) {
    el('lib-chat-answer').textContent = '';
    setNote(err.message || String(err));
  } finally {
    setBusy(false);
  }
}

export function initLibraryChat() {
  const form = el('lib-chat-form');
  if (!form) return;

  form.addEventListener('submit', e => {
    e.preventDefault();
    const question = el('lib-chat-input').value.trim();
    if (question) ask(question);
  });

  el('lib-chat-close')?.addEventListener('click', () => {
    el('lib-chat-panel').classList.add('hidden');
  });
}
