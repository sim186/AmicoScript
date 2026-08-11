// Library export and import.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { clientLog } from './upload.js';

function feedback(message, tone) {
  const el = document.getElementById('backup-feedback');
  if (!el) return;
  el.textContent = message;
  el.className = `text-[10px] leading-tight ${
    tone === 'error' ? 'text-red-600' : tone === 'ok' ? 'text-green-600' : 'text-slate-500'
  }`;
  el.classList.toggle('hidden', !message);
}

function busy(button, isBusy, label) {
  if (!button) return;
  button.disabled = isBusy;
  button.classList.toggle('opacity-60', isBusy);
  if (label) button.textContent = label;
}

export async function exportLibrary() {
  const button = document.getElementById('backup-export-btn');
  const includeAudio = document.getElementById('backup-include-audio')?.checked ?? true;
  const original = button?.textContent;

  busy(button, true, 'Preparing…');
  feedback('Building the bundle. With audio this can take a while.', 'info');
  try {
    const res = await fetch(`/api/library/export?include_audio=${includeAudio}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Export failed (${res.status})`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const stamp = new Date().toISOString().slice(0, 10);
    a.download = `amicoscript-library-${stamp}.zip`;
    a.click();
    URL.revokeObjectURL(url);
    feedback(`Exported ${(blob.size / 1024 / 1024).toFixed(1)} MB.`, 'ok');
    clientLog('Library exported');
  } catch (err) {
    feedback(err.message, 'error');
    clientLog(`Library export failed: ${err.message}`, 'ERROR');
  } finally {
    busy(button, false, original);
  }
}

export async function importLibrary(file) {
  if (!file) return;
  const button = document.getElementById('backup-import-btn');
  const original = button?.textContent;
  const overwrite = document.getElementById('backup-overwrite')?.checked;

  busy(button, true, 'Importing…');
  feedback('Importing…', 'info');
  try {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('mode', overwrite ? 'overwrite' : 'skip');
    const res = await fetch('/api/library/import', { method: 'POST', body: fd });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `Import failed (${res.status})`);

    const counts = body.imported || {};
    feedback(
      `Imported ${counts.recordings || 0} recording(s), ${counts.audio || 0} audio file(s).`,
      'ok',
    );
    clientLog(`Library imported: ${JSON.stringify(counts)}`);
    // The library list on screen predates the import.
    window.dispatchEvent(new CustomEvent('amicoscript:library-changed'));
  } catch (err) {
    feedback(err.message, 'error');
    clientLog(`Library import failed: ${err.message}`, 'ERROR');
  } finally {
    busy(button, false, original);
  }
}

export function initBackup() {
  document.getElementById('backup-export-btn')?.addEventListener('click', exportLibrary);

  const picker = document.getElementById('backup-import-input');
  document.getElementById('backup-import-btn')?.addEventListener('click', () => picker?.click());
  picker?.addEventListener('change', () => {
    const file = picker.files?.[0];
    picker.value = '';  // so re-picking the same file fires 'change' again
    importLibrary(file);
  });
}
