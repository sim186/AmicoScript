// AI analysis panel and LLM settings.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { state } from './state.js';
import { escHtml } from './transcript.js';
import { currentProviderFields, showUrlNote } from './llm-setup.js';
import { clientLog } from './upload.js';

export function initAiAnalysis() {
  // Type buttons
  document.querySelectorAll('.ai-type-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.ai-type-btn').forEach(b => {
        b.classList.remove('border-brand', 'text-brand', 'bg-brand/5');
        b.classList.add('border-slate-200', 'text-slate-600');
      });
      btn.classList.add('border-brand', 'text-brand', 'bg-brand/5');
      btn.classList.remove('border-slate-200', 'text-slate-600');
      const type = btn.dataset.type;
      document.getElementById('ai-lang-row').classList.toggle('hidden', type !== 'translate');
      document.getElementById('ai-custom-row').classList.toggle('hidden', type !== 'custom');
    });
  });

  // Run / Stop button
  document.getElementById('ai-run-btn').addEventListener('click', () => {
    if (state.analysisJobId) {
      stopAnalysis();
    } else {
      runAnalysis();
    }
  });

  // Copy result
  document.getElementById('ai-result-copy').addEventListener('click', () => {
    if (state.rawAiText) navigator.clipboard.writeText(state.rawAiText).catch(() => { });
  });

  // LLM Settings: eye toggle for API key
  document.getElementById('llm-key-eye').addEventListener('click', () => {
    const inp = document.getElementById('llm-api-key');
    const isHidden = inp.type === 'password';
    inp.type = isHidden ? 'text' : 'password';
    document.getElementById('llm-key-eye-open').classList.toggle('hidden', isHidden);
    document.getElementById('llm-key-eye-closed').classList.toggle('hidden', !isHidden);
  });

  // LLM Settings: auto-save on input (debounced)
  let _llmSaveTimer = null;
  ['llm-base-url', 'llm-model-input', 'llm-api-key'].forEach(id => {
    document.getElementById(id).addEventListener('input', () => {
      clearTimeout(_llmSaveTimer);
      _llmSaveTimer = setTimeout(saveLlmSettings, 600);
    });
  });

  // Browse models button
  document.getElementById('llm-browse-models-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    const panel = document.getElementById('llm-models-panel');
    if (panel.classList.contains('hidden')) openModelsBrowser(); else panel.classList.add('hidden');
  });

  // Refresh inside panel
  document.getElementById('llm-refresh-models-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    refreshModelsBrowser();
  });

  // Close panel on outside click
  document.addEventListener('click', (e) => {
    const panel = document.getElementById('llm-models-panel');
    const browseBtn = document.getElementById('llm-browse-models-btn');
    if (panel && !panel.contains(e.target) && e.target !== browseBtn) {
      panel.classList.add('hidden');
    }
  });

  // Test connection button
  document.getElementById('llm-test-btn').addEventListener('click', testLlmConnection);

  // Benchmark button
  document.getElementById('benchmark-run-btn').addEventListener('click', runBenchmark);
  document.getElementById('benchmark-share-btn').addEventListener('click', shareBenchmark);
}

let _benchmarkData = null;

async function runBenchmark() {
  const btn = document.getElementById('benchmark-run-btn');
  const label = document.getElementById('benchmark-run-label');
  const runIcon = document.getElementById('benchmark-run-icon');
  const spinIcon = document.getElementById('benchmark-spin-icon');
  const status = document.getElementById('benchmark-status');
  const results = document.getElementById('benchmark-results');

  btn.disabled = true;
  runIcon.classList.add('hidden');
  spinIcon.classList.remove('hidden');
  label.textContent = 'Running… (1–3 min)';
  status.textContent = 'Downloading reference audio and running tiny → small → medium…';
  status.classList.remove('hidden');
  results.classList.add('hidden');
  _benchmarkData = null;
  clientLog('Benchmark started');

  try {
    const resp = await fetch('/api/benchmark/run', {
      method: 'POST',
      signal: AbortSignal.timeout ? AbortSignal.timeout(300000) : undefined,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const data = await resp.json();
    _benchmarkData = data;
    _renderBenchmarkResults(data);
    clientLog('Benchmark complete');
  } catch (e) {
    status.textContent = 'Error: ' + e.message;
    clientLog('Benchmark error: ' + e.message, 'ERROR');
  } finally {
    btn.disabled = false;
    runIcon.classList.remove('hidden');
    spinIcon.classList.add('hidden');
    label.textContent = 'Run Benchmark';
  }
}

function _renderBenchmarkResults(data) {
  const status = document.getElementById('benchmark-status');
  const results = document.getElementById('benchmark-results');
  const sysInfo = document.getElementById('benchmark-sys-info');
  const tbody = document.getElementById('benchmark-table-body');

  const s = data.system || {};
  const gpuLine = s.cuda ? (s.gpu || 'GPU (CUDA)') : 'CPU only';
  sysInfo.innerHTML = [
    `<div><span class="font-medium text-slate-600">CPU:</span> ${s.cpu || '—'} (${s.cpu_cores || '?'} cores)</div>`,
    `<div><span class="font-medium text-slate-600">RAM:</span> ${s.ram_gb != null ? s.ram_gb + ' GB' : '—'}</div>`,
    `<div><span class="font-medium text-slate-600">GPU:</span> ${gpuLine}</div>`,
    `<div><span class="font-medium text-slate-600">OS:</span> ${s.os || '—'} (${s.arch || '—'})</div>`,
  ].join('');

  tbody.innerHTML = (data.results || []).map(r => {
    if (r.error) {
      return `<tr><td class="py-1 font-medium">${r.model}</td><td colspan="4" class="py-1 text-red-500">${r.error}</td></tr>`;
    }
    const rtfClass = r.rtf < 1 ? 'text-emerald-600 font-medium' : 'text-amber-600 font-medium';
    return `<tr>
      <td class="py-1 font-medium">${r.model}</td>
      <td class="py-1 text-slate-500">${r.load_time_s}s</td>
      <td class="py-1 text-slate-500">${r.transcribe_time_s}s</td>
      <td class="py-1 text-slate-500">${r.elapsed_s}s</td>
      <td class="py-1 ${rtfClass}">${r.rtf}x</td>
    </tr>`;
  }).join('');

  const totalEl = document.getElementById('benchmark-total-elapsed');
  if (data.total_elapsed_s != null) {
    totalEl.textContent = `Total benchmark time: ${data.total_elapsed_s}s`;
    totalEl.classList.remove('hidden');
  } else {
    totalEl.classList.add('hidden');
  }

  status.classList.add('hidden');
  results.classList.remove('hidden');
}

function shareBenchmark() {
  if (!_benchmarkData) return;
  const d = _benchmarkData;
  const s = d.system || {};
  const gpuLine = s.cuda ? (s.gpu || 'GPU (CUDA)') : 'CPU only';
  const machineLabel = `${s.cpu || 'unknown'} / ${gpuLine}`;

  const tableRows = (d.results || []).map(r =>
    r.error
      ? `| ${r.model} | — | — | — | error |`
      : `| ${r.model} | ${r.load_time_s}s | ${r.transcribe_time_s}s | ${r.elapsed_s}s | ${r.rtf}x |`
  ).join('\n');

  const body = [
    `## Benchmark Results`,
    ``,
    `**Date:** ${d.date}`,
    `**OS:** ${s.os || '—'} (${s.arch || '—'})`,
    `**CPU:** ${s.cpu || '—'}`,
    `**Cores:** ${s.cpu_cores || '—'}`,
    `**RAM:** ${s.ram_gb != null ? s.ram_gb + ' GB' : '—'}`,
    `**GPU:** ${gpuLine}`,
    `**Reference audio:** ${d.reference_audio || 'jfk.flac'}`,
    ``,
    `| Model | Load | Inference | Elapsed | RTF |`,
    `|-------|------|-----------|---------|-----|`,
    tableRows,
    ``,
    d.total_elapsed_s != null ? `**Total benchmark time:** ${d.total_elapsed_s}s` : '',
    `> RTF < 1.0 = faster than real-time`,
    ``,
    `<!-- benchmark-data`,
    JSON.stringify(d),
    `-->`,
  ].join('\n');

  const title = encodeURIComponent(`[Benchmark] ${machineLabel} — ${d.date}`);
  const bodyEnc = encodeURIComponent(body);
  const url = `https://github.com/sim186/AmicoScript/issues/new?labels=benchmark-result&title=${title}&body=${bodyEnc}`;
  window.open(url, '_blank', 'noopener');
}

function getSelectedAnalysisType() {
  const btn = document.querySelector('.ai-type-btn.border-brand');
  return btn ? btn.dataset.type : null;
}

async function runAnalysis() {
  const recId = state.activeRecordingId || state.recordingId;
  if (!recId) { alert('No recording loaded.'); return; }

  const type = getSelectedAnalysisType();
  if (!type) { alert('Select an analysis type first.'); return; }
  clientLog(`AI analysis started: type=${type}, model=${document.getElementById('llm-model-input').value || '?'}`);

  const fd = new FormData();
  fd.append('analysis_type', type);
  if (type === 'translate') {
    fd.append('target_language', document.getElementById('ai-target-lang').value.trim() || 'English');
  }
  if (type === 'custom') {
    const prompt = document.getElementById('ai-custom-prompt').value.trim();
    if (!prompt) { alert('Enter a custom prompt.'); return; }
    fd.append('custom_prompt', prompt);
  }
  const outputLang = document.getElementById('ai-output-lang').value.trim();
  if (outputLang) fd.append('output_language', outputLang);

  try {
    const res = await fetch(`/api/recordings/${recId}/analyses`, { method: 'POST', body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(err.detail || 'Failed to start analysis');
    }
    const { job_id } = await res.json();
    connectAnalysisSSE(job_id, recId);
    // Clear custom prompt after submitting the request
    const custom = document.getElementById('ai-custom-prompt'); if (custom) custom.value = '';
  } catch (err) {
    alert(err.message);
  }
}

function connectAnalysisSSE(jobId, recId) {
  state.analysisJobId = jobId;
  const resultDiv = document.getElementById('ai-result-text');
  const spinner = document.getElementById('ai-result-spinner');
  const runBtn = document.getElementById('ai-run-btn');
  const resultArea = document.getElementById('ai-result-area');

  resultDiv.textContent = '';
  state.rawAiText = '';
  resultArea.classList.remove('hidden');
  spinner.classList.remove('hidden');
  runBtn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M6 6h12v12H6z"/></svg> Stop`;

  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  state.analysisEventSource = es;

  es.onmessage = e => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    if (data.heartbeat) return;

    if (data.status === 'streaming' && data.data?.chunk) {
      state.rawAiText += data.data.chunk;
      resultDiv.textContent = state.rawAiText;
      resultDiv.scrollTop = resultDiv.scrollHeight;
    } else if (data.status === 'done') {
      es.close();
      state.analysisJobId = null;
      state.analysisEventSource = null;
      spinner.classList.add('hidden');
      resultDiv.innerHTML = marked.parse(state.rawAiText);
      _resetAnalysisBtn();
      clientLog('AI analysis complete');
      const typeLabel = getSelectedAnalysisType();
      if (typeLabel) document.getElementById('ai-result-label').textContent = typeLabel.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
      loadPastAnalyses(recId || state.activeRecordingId || state.recordingId);
    } else if (data.status === 'error') {
      es.close();
      state.analysisJobId = null;
      state.analysisEventSource = null;
      spinner.classList.add('hidden');
      _resetAnalysisBtn();
      clientLog(`AI analysis error: ${data.message || 'unknown'}`, 'ERROR');
      if (data.message) {
        state.rawAiText += `\n\n[Error: ${data.message}]`;
        resultDiv.innerHTML = marked.parse(state.rawAiText);
      }
    } else if (data.status === 'cancelled') {
      es.close();
      state.analysisJobId = null;
      state.analysisEventSource = null;
      spinner.classList.add('hidden');
      if (state.rawAiText) resultDiv.innerHTML = marked.parse(state.rawAiText);
      _resetAnalysisBtn();
    }
  };

  es.onerror = () => { es.close(); state.analysisJobId = null; state.analysisEventSource = null; spinner.classList.add('hidden'); _resetAnalysisBtn(); };
}

function _resetAnalysisBtn() {
  const runBtn = document.getElementById('ai-run-btn');
  runBtn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2a10 10 0 100 20A10 10 0 0012 2zm-1 14.5v-9l6 4.5-6 4.5z"/></svg> Run Analysis`;
}

async function stopAnalysis() {
  if (state.analysisEventSource) { state.analysisEventSource.close(); state.analysisEventSource = null; }
  if (state.analysisJobId) {
    fetch(`/api/jobs/${state.analysisJobId}/cancel`, { method: 'POST' }).catch(() => { });
    state.analysisJobId = null;
  }
  document.getElementById('ai-result-spinner').classList.add('hidden');
  _resetAnalysisBtn();
}

export async function loadPastAnalyses(recordingId) {
  if (!recordingId) return;
  try {
    const res = await fetch(`/api/recordings/${recordingId}/analyses`);
    if (!res.ok) return;
    const analyses = await res.json();
    const container = document.getElementById('ai-past-list');
    const section = document.getElementById('ai-past-analyses');
    if (!analyses.length) { section.classList.add('hidden'); return; }
    section.classList.remove('hidden');

    const fragment = document.createDocumentFragment();
    analyses.forEach(a => {
      const label = a.analysis_type.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
      const preview = a.result_text.slice(0, 90) + (a.result_text.length > 90 ? '…' : '');
      const date = new Date(a.created_at * 1000).toLocaleDateString();

      const analysisDiv = document.createElement('div');
      analysisDiv.className = 'border border-slate-200 rounded-lg p-2 text-xs';
      analysisDiv.dataset.analysisId = String(a.id);

      const headerDiv = document.createElement('div');
      headerDiv.className = 'flex items-center justify-between mb-0.5';

      const labelSpan = document.createElement('span');
      labelSpan.className = 'font-semibold text-slate-700';
      labelSpan.textContent = label;

      const deleteButton = document.createElement('button');
      deleteButton.type = 'button';
      deleteButton.className = 'text-slate-300 hover:text-red-400 transition text-xs leading-none';
      deleteButton.textContent = '✕';
      deleteButton.addEventListener('click', () => window._deleteAnalysis(a.id, recordingId));

      headerDiv.appendChild(labelSpan);
      headerDiv.appendChild(deleteButton);

      const previewDiv = document.createElement('div');
      previewDiv.className = 'text-slate-500 cursor-pointer hover:text-slate-700 leading-snug';
      previewDiv.textContent = preview || '(empty)';
      previewDiv.addEventListener('click', () => window._showAnalysisResult(a.id, recordingId));

      const metaDiv = document.createElement('div');
      metaDiv.className = 'text-slate-300 mt-0.5';
      metaDiv.textContent = `${date} · ${a.model_name}`;

      analysisDiv.appendChild(headerDiv);
      analysisDiv.appendChild(previewDiv);
      analysisDiv.appendChild(metaDiv);
      fragment.appendChild(analysisDiv);
    });

    container.replaceChildren(fragment);
  } catch { /* silently ignore */ }
}

window._showAnalysisResult = async function (analysisId, recordingId) {
  try {
    const res = await fetch(`/api/recordings/${recordingId}/analyses/${analysisId}`);
    if (!res.ok) return;
    const a = await res.json();
    const resultDiv = document.getElementById('ai-result-text');
    state.rawAiText = a.result_text;
    resultDiv.innerHTML = marked.parse(a.result_text);
    const label = a.analysis_type.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
    document.getElementById('ai-result-label').textContent = label;
    document.getElementById('ai-result-area').classList.remove('hidden');
  } catch { /* ignore */ }
};

window._deleteAnalysis = async function (analysisId, recordingId) {
  if (!confirm('Delete this analysis result?')) return;
  try {
    await fetch(`/api/recordings/${recordingId}/analyses/${analysisId}`, { method: 'DELETE' });
    loadPastAnalyses(recordingId);
    // Clear result area if it was showing this analysis
    // (no easy way to track, just leave it)
  } catch { /* ignore */ }
};

export async function saveLlmSettings() {
  const fd = new FormData();
  const baseUrl = document.getElementById('llm-base-url').value.trim();
  const model = document.getElementById('llm-model-input').value.trim();
  fd.append('llm_base_url', baseUrl);
  fd.append('llm_model_name', model);
  // An untouched key field holds the placeholder, never the real key — send
  // the sentinel so saving other fields cannot wipe a stored credential.
  const keyInput = document.getElementById('llm-api-key');
  fd.append('llm_api_key', keyInput.dataset.masked === 'true' ? '__unchanged__' : keyInput.value);
  const contextTokens = document.getElementById('llm-context-tokens');
  if (contextTokens && contextTokens.value.trim()) {
    fd.append('llm_context_tokens', contextTokens.value.trim());
  }
  for (const [key, value] of Object.entries(currentProviderFields())) fd.append(key, value);

  clientLog(`LLM settings saved: url=${baseUrl || '(provider default)'}, model=${model || '(none)'}`);
  try {
    const res = await fetch('/api/llm/settings', { method: 'POST', body: fd });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      showUrlNote(body.detail || 'Could not save these settings.');
      return;
    }
    // The server cleans up the address (a trailing /v1, a container host).
    // Show what it settled on rather than leaving a stale value on screen.
    if (body.llm_base_url) document.getElementById('llm-base-url').value = body.llm_base_url;
    showUrlNote(body.note ? `Adjusted: ${body.note}.` : '');
  } catch { /* offline; the value stays in the form */ }
}

const POPULAR_MODELS = [
  { id: 'llama3.2', label: 'Llama 3.2 (3B)' },
  { id: 'llama3.1', label: 'Llama 3.1 (8B)' },
  { id: 'mistral', label: 'Mistral (7B)' },
  { id: 'gemma2', label: 'Gemma 2 (9B)' },
  { id: 'qwen2.5', label: 'Qwen 2.5 (7B)' },
  { id: 'phi4', label: 'Phi-4 (14B)' },
  { id: 'deepseek-r1', label: 'DeepSeek R1 (7B)' },
];

async function openModelsBrowser() {
  const panel = document.getElementById('llm-models-panel');
  panel.classList.remove('hidden');
  await refreshModelsBrowser();
}

async function refreshModelsBrowser() {
  const installedSection = document.getElementById('llm-installed-section');
  const installedList = document.getElementById('llm-installed-list');
  const popularList = document.getElementById('llm-popular-list');

  // Populate popular models list (with pull button)
  popularList.innerHTML = POPULAR_MODELS.map(m => `
    <div class="flex items-center justify-between px-3 py-1.5 hover:bg-slate-50 text-xs gap-2">
      <button type="button" onclick="selectLlmModel('${escHtml(m.id)}')"
        class="flex-1 text-left text-slate-700 hover:text-brand transition truncate">${escHtml(m.label)}</button>
      <button type="button" onclick="pullLlmModel('${escHtml(m.id)}')" id="pull-btn-${escHtml(m.id)}"
        class="shrink-0 text-[10px] px-2 py-0.5 rounded border border-slate-200 text-slate-500 hover:border-brand hover:text-brand transition">
        Pull
      </button>
    </div>
  `).join('');

  // Fetch installed models
  try {
    await saveLlmSettings();
    const res = await fetch('/api/llm/models');
    const models = await res.json();
    if (models.length) {
      installedSection.classList.remove('hidden');
      installedList.innerHTML = models.map(m => `
        <div class="flex items-center px-3 py-1.5 hover:bg-slate-50 text-xs gap-2">
          <button type="button" onclick="selectLlmModel('${escHtml(m.id)}')"
            class="flex-1 text-left text-slate-700 hover:text-brand transition truncate font-medium">${escHtml(m.id)}</button>
          <span class="shrink-0 text-slate-400">installed</span>
        </div>
      `).join('');
    } else {
      installedSection.classList.add('hidden');
    }
  } catch {
    installedSection.classList.add('hidden');
  }
}

export function selectLlmModel(modelId) {
  document.getElementById('llm-model-input').value = modelId;
  document.getElementById('llm-models-panel').classList.add('hidden');
  clientLog(`LLM model selected: ${modelId}`);
  saveLlmSettings();
}

export async function pullLlmModel(modelId) {
  const btn = document.getElementById(`pull-btn-${modelId}`);
  if (btn) { btn.textContent = '…'; btn.disabled = true; }
  clientLog(`Pulling LLM model: ${modelId}`);
  try {
    const res = await fetch('/api/llm/models/pull', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model_name: modelId }),
    });
    if (!res.ok) throw new Error(await res.text());
    clientLog(`Model pull complete: ${modelId}`);
    if (btn) { btn.textContent = '✓'; }
    // Auto-select the pulled model
    selectLlmModel(modelId);
    // Refresh installed list
    await refreshModelsBrowser();
  } catch (err) {
    clientLog(`Model pull failed: ${err.message}`, 'ERROR');
    if (btn) { btn.textContent = '✗'; btn.disabled = false; }
    setTimeout(() => { if (btn) { btn.textContent = 'Pull'; btn.disabled = false; } }, 3000);
  }
}

async function loadLlmModels() {
  // Legacy: just open the browse panel
  openModelsBrowser();
}

async function testLlmConnection() {
  const btn = document.getElementById('llm-test-btn');
  const statusEl = document.getElementById('llm-test-status');
  btn.disabled = true;
  btn.textContent = 'Testing…';
  statusEl.textContent = '';
  try {
    await saveLlmSettings();
    const res = await fetch('/api/llm/test-connection', { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      statusEl.className = 'text-xs text-emerald-500';
      statusEl.textContent = data.model_info || 'Connected ✓';
    } else {
      statusEl.className = 'text-xs text-red-500';
      statusEl.textContent = data.error || 'Connection failed';
    }
  } catch (err) {
    statusEl.className = 'text-xs text-red-500';
    statusEl.textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Test Connection';
  }
}
