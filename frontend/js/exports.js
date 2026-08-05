// Export menu and download helpers.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { state } from './state.js';
import { translateAll } from './transcript.js';
import { clientLog } from './upload.js';

export function initExportButtons() {
  document.querySelectorAll('.export-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const fmt = btn.dataset.fmt;
      let exportUrl, baseName;
      if (state.activeRecordingId) {
        exportUrl = `/api/recordings/${state.activeRecordingId}/export/${fmt}`;
        baseName = (state.result?.filename || 'transcript').replace(/\.[^.]+$/, '');
      } else if (state.jobId) {
        exportUrl = `/api/jobs/${state.jobId}/export/${fmt}`;
        baseName = (state.selectedFile?.name || 'transcript').replace(/\.[^.]+$/, '');
      } else {
        return;
      }
      try {
        clientLog(`Export as ${fmt.toUpperCase()}: ${baseName}.${fmt}`);
        const res = await fetch(exportUrl);
        if (!res.ok) throw new Error(await res.text());
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${baseName}.${fmt}`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (err) {
        clientLog(`Export failed: ${err.message}`, 'ERROR');
        alert(`Export failed: ${err.message}`);
      }
    });
  });

  const transAllBtn = document.getElementById('translate-all-btn');
  if (transAllBtn) {
    transAllBtn.addEventListener('click', translateAll);
  }
}

export function showError(msg) {
  const el = document.getElementById('error-msg');
  el.textContent = msg;
  el.classList.remove('hidden');
}

export function hideError() {
  document.getElementById('error-msg').classList.add('hidden');
}
