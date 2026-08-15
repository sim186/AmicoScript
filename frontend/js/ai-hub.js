// Unified AI Assistant Hub — chat-style interface for both Analysis and Library Chat.
//
// Replaces the scattered LLM UI: AI Analysis slide-over (transcript tab) and
// Library Chat form (library tab) with a single floating conversational panel
// accessible from any tab.
//
// Part of the AmicoScript frontend. No build step.

import { state } from './state.js';
import { clientLog } from './upload.js';
import { escHtml } from './transcript.js';
import { openRecording } from './library.js';
import { currentProviderFields } from './llm-setup.js';

// ---------------------------------------------------------------------------
// DOM refs (created dynamically by init)
// ---------------------------------------------------------------------------
let _hubPanel = null;
let _messagesEl = null;
let _inputWrap = null;
let _modeTabs = null;
let _contextLabel = null;
let _isOpen = false;
let _currentMode = 'analyze';   // 'analyze' | 'chat'
let _analyzeType = 'summary';

// ---------------------------------------------------------------------------
// Mode switching
// ---------------------------------------------------------------------------
function _setMode(mode) {
  _currentMode = mode;
  if (!_hubPanel) return;

  const analyzeTab = document.getElementById('ai-hub-tab-analyze');
  const chatTab = document.getElementById('ai-hub-tab-chat');
  if (analyzeTab) {
    analyzeTab.classList.toggle('bg-brand', mode === 'analyze');
    analyzeTab.classList.toggle('text-white', mode === 'analyze');
    analyzeTab.classList.toggle('bg-white', mode !== 'analyze');
    analyzeTab.classList.toggle('text-slate-600', mode !== 'analyze');
  }
  if (chatTab) {
    chatTab.classList.toggle('bg-brand', mode === 'chat');
    chatTab.classList.toggle('text-white', mode === 'chat');
    chatTab.classList.toggle('bg-white', mode !== 'chat');
    chatTab.classList.toggle('text-slate-600', mode !== 'chat');
  }

  _renderInputArea();
  _updateContextLabel();
}

// ---------------------------------------------------------------------------
// Context label (shows which recording is active, or "Library")
// ---------------------------------------------------------------------------
function _updateContextLabel() {
  if (!_contextLabel) return;
  const rec = state.currentRecording || (state.result ? { title: 'Current recording' } : null);
  if (_currentMode === 'analyze') {
    _contextLabel.textContent = rec ? `Analyzing: ${rec.title || 'Recording'}` : 'No recording loaded';
  } else {
    _contextLabel.textContent = 'Ask across your entire library';
  }
}

// ---------------------------------------------------------------------------
// Show / hide
// ---------------------------------------------------------------------------
export function toggleHub() {
  if (!_hubPanel) return;
  _isOpen = !_isOpen;
  _hubPanel.classList.toggle('hidden', !_isOpen);
  const trigger = document.getElementById('ai-hub-trigger');
  if (trigger) trigger.classList.toggle('active', _isOpen);
  if (_isOpen) {
    _updateContextLabel();
    _scrollToBottom();
    clientLog('AI Assistant opened');
  }
}

export function openHub(mode = 'analyze') {
  if (!_hubPanel) return;
  _isOpen = true;
  _hubPanel.classList.remove('hidden');
  const trigger = document.getElementById('ai-hub-trigger');
  if (trigger) trigger.classList.add('active');
  _setMode(mode);
  _updateContextLabel();
  _scrollToBottom();
}

export function closeHub() {
  _isOpen = false;
  if (_hubPanel) _hubPanel.classList.add('hidden');
  const trigger = document.getElementById('ai-hub-trigger');
  if (trigger) trigger.classList.remove('active');
}

// ---------------------------------------------------------------------------
// Message rendering (shared between analyze + chat)
// ---------------------------------------------------------------------------
function _addMessage(role, html, opts = {}) {
  if (!_messagesEl) return;
  const wrap = document.createElement('div');
  wrap.className = 'mb-3';

  const bubble = document.createElement('div');
  const isUser = role === 'user';
  bubble.className = isUser
    ? 'ml-auto max-w-[85%] rounded-2xl rounded-tr-sm bg-brand text-white px-4 py-2.5 text-sm shadow-sm'
    : 'mr-auto max-w-[90%] rounded-2xl rounded-tl-sm bg-white border border-slate-200 px-4 py-3 text-sm text-slate-700 shadow-sm';

  if (opts.id) bubble.dataset.messageId = opts.id;
  if (opts.spinner) {
    bubble.innerHTML = `<div class="flex items-center gap-2 text-slate-400">
      <svg class="animate-spin w-3.5 h-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg>
      <span class="text-xs">${escHtml(opts.spinner)}</span>
    </div>`;
  } else {
    bubble.innerHTML = html;
  }
  wrap.appendChild(bubble);

  // Citations / sources row below the bubble
  if (opts.sources && opts.sources.length) {
    const srcRow = document.createElement('div');
    srcRow.className = 'mt-1.5 flex flex-wrap gap-1.5';
    opts.sources.forEach((src, i) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'text-[10px] px-2 py-0.5 rounded-full border border-slate-200 bg-white text-slate-500 hover:border-brand hover:text-brand transition';
      chip.textContent = `[${i + 1}] ${src.title || 'Source'}`;
      chip.addEventListener('click', () => _jumpToSource(src));
      srcRow.appendChild(chip);
    });
    wrap.appendChild(srcRow);
  }

  // Meta label (analysis type, model name, etc.)
  if (opts.meta) {
    const meta = document.createElement('div');
    meta.className = 'mt-0.5 text-[10px] text-slate-400' + (isUser ? ' text-right pr-1' : ' pl-1');
    meta.textContent = opts.meta;
    wrap.appendChild(meta);
  }

  _messagesEl.appendChild(wrap);
  _scrollToBottom();
}

function _updateLastMessage(html, opts = {}) {
  if (!_messagesEl) return;
  const bubbles = _messagesEl.querySelectorAll('[data-message-id]');
  const last = bubbles[bubbles.length - 1];
  if (!last) return;
  if (opts.spinner) {
    last.innerHTML = `<div class="flex items-center gap-2 text-slate-400">
      <svg class="animate-spin w-3.5 h-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path></svg>
      <span class="text-xs">${escHtml(opts.spinner)}</span>
    </div>`;
  } else {
    last.innerHTML = html;
  }
  if (opts.sources) {
    const existing = last.parentElement.querySelector('.flex.flex-wrap.gap-1\.5');
    if (existing) existing.remove();
    if (opts.sources.length) {
      const srcRow = document.createElement('div');
      srcRow.className = 'mt-1.5 flex flex-wrap gap-1.5';
      opts.sources.forEach((src, i) => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'text-[10px] px-2 py-0.5 rounded-full border border-slate-200 bg-white text-slate-500 hover:border-brand hover:text-brand transition';
        chip.textContent = `[${i + 1}] ${src.title || 'Source'}`;
        chip.addEventListener('click', () => _jumpToSource(src));
        srcRow.appendChild(chip);
      });
      last.parentElement.appendChild(srcRow);
    }
  }
  _scrollToBottom();
}

function _scrollToBottom() {
  if (_messagesEl) _messagesEl.scrollTop = _messagesEl.scrollHeight;
}

// ---------------------------------------------------------------------------
// Analyze mode — business logic (adapted from analysis.js)
// ---------------------------------------------------------------------------
let _analysisJobId = null;
let _analysisES = null;
let _rawAiText = '';

function _getSelectedAnalysisType() {
  return _analyzeType;
}

function _setAnalyzeType(type) {
  _analyzeType = type;
  document.querySelectorAll('.ai-hub-type-btn').forEach(b => {
    const active = b.dataset.type === type;
    b.classList.toggle('border-brand', active);
    b.classList.toggle('text-brand', active);
    b.classList.toggle('bg-brand/5', active);
    b.classList.toggle('border-slate-200', !active);
    b.classList.toggle('text-slate-600', !active);
  });
  const langRow = document.getElementById('ai-hub-lang-row');
  const customRow = document.getElementById('ai-hub-custom-row');
  if (langRow) langRow.classList.toggle('hidden', type !== 'translate');
  if (customRow) customRow.classList.toggle('hidden', type !== 'custom');
}

async function _runAnalysis() {
  const recId = state.activeRecordingId || state.recordingId;
  if (!recId) { _addMessage('assistant', '<em>No recording loaded. Open a recording first.</em>'); return; }

  const type = _getSelectedAnalysisType();
  if (!type) return;

  _addMessage('user', type === 'custom'
    ? (document.getElementById('ai-hub-custom-prompt')?.value.trim() || 'Custom analysis')
    : type.replace('_', ' '), { meta: 'Analysis request' });

  const msgId = 'ai-' + Date.now();
  _addMessage('assistant', '', { id: msgId, spinner: 'Analyzing…' });

  const fd = new FormData();
  fd.append('analysis_type', type);
  if (type === 'translate') {
    const target = document.getElementById('ai-hub-target-lang')?.value.trim() || 'English';
    fd.append('target_language', target);
  }
  if (type === 'custom') {
    const prompt = document.getElementById('ai-hub-custom-prompt')?.value.trim();
    if (!prompt) { _updateLastMessage('<em>Enter a custom prompt first.</em>', { id: msgId }); return; }
    fd.append('custom_prompt', prompt);
  }
  const outputLang = document.getElementById('ai-hub-output-lang')?.value.trim();
  if (outputLang) fd.append('output_language', outputLang);

  try {
    const res = await fetch(`/api/recordings/${recId}/analyses`, { method: 'POST', body: fd });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Failed');
    const { job_id } = await res.json();
    _connectAnalysisSSE(job_id, msgId, recId);
  } catch (err) {
    _updateLastMessage(`<span class="text-red-500">Error: ${escHtml(err.message)}</span>`, { id: msgId });
  }
}

function _connectAnalysisSSE(jobId, msgId, recId) {
  _analysisJobId = jobId;
  _rawAiText = '';
  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  _analysisES = es;

  es.onmessage = e => {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }
    if (data.heartbeat) return;

    if (data.status === 'streaming' && data.data?.chunk) {
      _rawAiText += data.data.chunk;
      _updateLastMessage(escHtml(_rawAiText).replace(/\n/g, '<br>'), { id: msgId, spinner: 'Generating…' });
    } else if (data.status === 'done') {
      es.close();
      _analysisJobId = null;
      _analysisES = null;
      _updateLastMessage(marked.parse(_rawAiText), { id: msgId });
      clientLog('AI analysis complete');
      _loadPastAnalyses(recId);
    } else if (data.status === 'error') {
      es.close();
      _analysisJobId = null;
      _analysisES = null;
      _updateLastMessage(`<span class="text-red-500">Error: ${escHtml(data.message || 'unknown')}</span>`, { id: msgId });
    } else if (data.status === 'cancelled') {
      es.close();
      _analysisJobId = null;
      _analysisES = null;
      _updateLastMessage(_rawAiText ? marked.parse(_rawAiText) : '<em>Cancelled</em>', { id: msgId });
    }
  };
  es.onerror = () => {
    es.close();
    _analysisJobId = null;
    _analysisES = null;
    _updateLastMessage(_rawAiText ? marked.parse(_rawAiText) : '<em>Connection lost</em>', { id: msgId });
  };
}

function _stopAnalysis() {
  if (_analysisES) { _analysisES.close(); _analysisES = null; }
  if (_analysisJobId) {
    fetch(`/api/jobs/${_analysisJobId}/cancel`, { method: 'POST' }).catch(() => {});
    _analysisJobId = null;
  }
}

async function _loadPastAnalyses(recordingId) {
  if (!recordingId) return;
  try {
    const res = await fetch(`/api/recordings/${recordingId}/analyses`);
    if (!res.ok) return;
    const analyses = await res.json();
    // Show a compact hint in the hub footer instead of a full list
    const count = analyses.length;
    if (count > 0) {
      const hint = document.getElementById('ai-hub-past-hint');
      if (hint) {
        hint.textContent = `${count} previous analysis${count !== 1 ? 'es' : ''} for this recording`;
        hint.classList.remove('hidden');
      }
    }
  } catch { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Chat mode — business logic (adapted from library-chat.js)
// ---------------------------------------------------------------------------
async function _askLibrary(question) {
  _addMessage('user', question, { meta: 'Library question' });
  const msgId = 'chat-' + Date.now();
  _addMessage('assistant', '', { id: msgId, spinner: 'Reading your library…' });

  try {
    const res = await fetch('/api/library/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Failed');
    const body = await res.json();

    if (body.no_matches) {
      let msg = 'Nothing in your library matches that question.';
      if (body.pending) msg += ` (${body.pending} recording(s) not indexed yet)`;
      _updateLastMessage(`<em>${escHtml(msg)}</em>`, { id: msgId });
      return;
    }

    const sources = (body.sources || []).map(s => ({
      title: s.title,
      timestamp: s.timestamp,
      text: s.text,
      recording_id: s.recording_id,
      start: s.start,
    }));
    _updateLastMessage(escHtml(body.answer || ''), { id: msgId, sources });

    const notes = [];
    if (!body.used_semantic) notes.push('keyword search');
    if (body.pending) notes.push(`${body.pending} not indexed`);
    if (notes.length) {
      const meta = document.createElement('div');
      meta.className = 'text-[10px] text-slate-400 mt-0.5 pl-1';
      meta.textContent = notes.join(' · ');
      const bubble = _messagesEl.querySelector(`[data-message-id="${msgId}"]`);
      if (bubble) bubble.parentElement.appendChild(meta);
    }
  } catch (err) {
    _updateLastMessage(`<span class="text-red-500">${escHtml(err.message || String(err))}</span>`, { id: msgId });
  }
}

async function _jumpToSource(source) {
  const rec = state.library.recordings.find(r => r.id === source.recording_id);
  if (!rec) return;
  const opened = await openRecording(rec);
  if (!opened) return;
  for (let attempt = 0; attempt < 40; attempt++) {
    const ws = state.wavesurfer;
    const duration = ws && ws.getDuration ? ws.getDuration() : 0;
    if (duration > 0) { ws.seekTo(Math.min(source.start / duration, 1)); return; }
    await new Promise(r => setTimeout(r, 100));
  }
}

// ---------------------------------------------------------------------------
// Input area rendering (mode-dependent controls above the text input)
// ---------------------------------------------------------------------------
function _renderInputArea() {
  if (!_inputWrap) return;
  _inputWrap.innerHTML = '';

  if (_currentMode === 'analyze') {
    const controls = document.createElement('div');
    controls.className = 'space-y-2 mb-2';
    controls.innerHTML = `
      <div class="flex flex-wrap gap-1.5">
        <button type="button" data-type="summary" class="ai-hub-type-btn px-2.5 py-1 rounded-md text-xs font-medium border transition">Summary</button>
        <button type="button" data-type="action_items" class="ai-hub-type-btn px-2.5 py-1 rounded-md text-xs font-medium border transition">Action Items</button>
        <button type="button" data-type="translate" class="ai-hub-type-btn px-2.5 py-1 rounded-md text-xs font-medium border transition">Translate</button>
        <button type="button" data-type="custom" class="ai-hub-type-btn px-2.5 py-1 rounded-md text-xs font-medium border transition">Custom</button>
      </div>
      <div id="ai-hub-lang-row" class="hidden">
        <input id="ai-hub-target-lang" type="text" placeholder="Target language…" class="w-full text-xs rounded-md border border-slate-200 px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-brand" />
      </div>
      <div id="ai-hub-custom-row" class="hidden">
        <textarea id="ai-hub-custom-prompt" rows="2" placeholder="Your instructions for the LLM…" class="w-full text-xs rounded-md border border-slate-200 px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-brand resize-none"></textarea>
      </div>
      <button type="button" id="ai-hub-run-btn" class="w-full flex items-center justify-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-lg bg-brand text-white hover:bg-brand-hover transition disabled:opacity-40">
        <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2a10 10 0 100 20A10 10 0 0012 2zm-1 14.5v-9l6 4.5-6 4.5z"/></svg>
        Run Analysis
      </button>
      <p id="ai-hub-past-hint" class="hidden text-[10px] text-slate-400 text-center"></p>
    `;
    _inputWrap.appendChild(controls);

    controls.querySelectorAll('.ai-hub-type-btn').forEach(b => {
      b.addEventListener('click', () => _setAnalyzeType(b.dataset.type));
    });
    document.getElementById('ai-hub-run-btn').addEventListener('click', _runAnalysis);
    _setAnalyzeType(_analyzeType);
  } else {
    const form = document.createElement('form');
    form.className = 'flex items-center gap-2';
    form.innerHTML = `
      <div class="relative flex-1">
        <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm3.75 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm3.75 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zM21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z"/></svg>
        <input id="ai-hub-chat-input" type="text" autocomplete="off" placeholder="Ask your library…" class="w-full pl-7 pr-3 py-2 text-xs rounded-lg border border-slate-200 focus:outline-none focus:ring-1 focus:ring-brand focus:border-brand" />
      </div>
      <button type="submit" class="px-3 py-2 rounded-lg bg-brand text-white text-xs font-medium hover:bg-brand-hover transition focus:outline-none focus:ring-2 focus:ring-brand focus:ring-offset-1">Ask</button>
    `;
    _inputWrap.appendChild(form);
    form.addEventListener('submit', e => {
      e.preventDefault();
      const inp = document.getElementById('ai-hub-chat-input');
      const q = inp.value.trim();
      if (q) { _askLibrary(q); inp.value = ''; }
    });
  }
}

// ---------------------------------------------------------------------------
// Build DOM
// ---------------------------------------------------------------------------
function _buildDom() {
  if (document.getElementById('ai-hub-panel')) return;

  // Trigger button (floating, bottom-right)
  const trigger = document.createElement('button');
  trigger.id = 'ai-hub-trigger';
  trigger.type = 'button';
  trigger.className = 'fixed right-5 z-[60] w-12 h-12 rounded-full bg-brand text-white shadow-lg hover:shadow-xl hover:scale-105 transition flex items-center justify-center focus:outline-none focus:ring-2 focus:ring-brand focus:ring-offset-2';
  trigger.style.bottom = 'calc(var(--console-h, 36px) + 0.75rem)';
  trigger.title = 'AI Assistant';
  trigger.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>`;
  trigger.addEventListener('click', toggleHub);
  document.body.appendChild(trigger);

  // Panel
  const panel = document.createElement('div');
  panel.id = 'ai-hub-panel';
  panel.className = 'hidden fixed right-5 z-[60] w-[420px] max-w-[92vw] h-[560px] max-h-[75vh] flex flex-col bg-white rounded-2xl shadow-2xl border border-slate-200 overflow-hidden';
  panel.style.bottom = 'calc(var(--console-h, 36px) + 4.5rem)';
  panel.innerHTML = `
    <!-- Header -->
    <div class="shrink-0 flex items-center justify-between px-4 py-3 border-b border-slate-100 bg-gradient-to-r from-brand/5 to-white">
      <div class="flex items-center gap-2">
        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-brand" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
        <span class="text-sm font-semibold text-slate-700">AI Assistant</span>
      </div>
      <button id="ai-hub-close" type="button" class="p-1 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition focus:outline-none">
        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
      </button>
    </div>
    <!-- Context label -->
    <div id="ai-hub-context" class="shrink-0 px-4 py-1.5 bg-slate-50 border-b border-slate-100 text-[10px] text-slate-500 truncate"></div>
    <!-- Mode tabs -->
    <div class="shrink-0 flex gap-1 px-3 py-2 bg-slate-50 border-b border-slate-100">
      <button type="button" id="ai-hub-tab-analyze" class="flex-1 px-2 py-1 rounded-md text-xs font-medium transition bg-brand text-white">Analyze</button>
      <button type="button" id="ai-hub-tab-chat" class="flex-1 px-2 py-1 rounded-md text-xs font-medium transition bg-white text-slate-600 border border-slate-200 hover:border-brand hover:text-brand">Chat</button>
    </div>
    <!-- Messages -->
    <div id="ai-hub-messages" class="flex-1 overflow-y-auto px-4 py-3 bg-slate-50/50 space-y-1"></div>
    <!-- Input area -->
    <div id="ai-hub-input" class="shrink-0 px-4 py-3 bg-white border-t border-slate-100"></div>
  `;
  document.body.appendChild(panel);

  _hubPanel = panel;
  _messagesEl = document.getElementById('ai-hub-messages');
  _inputWrap = document.getElementById('ai-hub-input');
  _contextLabel = document.getElementById('ai-hub-context');

  document.getElementById('ai-hub-close').addEventListener('click', closeHub);
  document.getElementById('ai-hub-tab-analyze').addEventListener('click', () => _setMode('analyze'));
  document.getElementById('ai-hub-tab-chat').addEventListener('click', () => _setMode('chat'));

  // Welcome message
  _addMessage('assistant', '<strong>Welcome.</strong><br>Select a recording, then choose an analysis type or switch to <strong>Chat</strong> to ask across your entire library.', { meta: 'AI Assistant' });
}

// ---------------------------------------------------------------------------
// Public init
// ---------------------------------------------------------------------------
export function initAiHub() {
  _buildDom();
  _renderInputArea();
}

// Legacy compatibility: analysis.js may call this to open the hub
export function ensureHub() {
  if (!_hubPanel) initAiHub();
}
