// Persisted preferences: server-side settings and localStorage.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { closeDrawer, isMobile, openDrawer } from './layout.js';
import { applyProvider, loadProviderCatalog } from './llm-setup.js';
import { state } from './state.js';
import { setMeetingCaptureToggle } from './upload.js';

export function _saveTranscriptionDefaults() {
  const fd = new FormData();
  fd.append('model', state.selectedModel);
  fd.append('language', state.selectedLanguage);
  fd.append('diarize', String(state.diarize));
  fetch('/api/settings', { method: 'POST', body: fd }).catch(() => {});
}

let _hfSaveTimer = null;

export function _saveHfTokenDebounced(token) {
  clearTimeout(_hfSaveTimer);
  const indicator = document.getElementById('hf-saved-indicator');
  indicator.classList.add('hidden');
  _hfSaveTimer = setTimeout(async () => {
    try {
      const fd = new FormData();
      fd.append('hf_token', token);
      const res = await fetch('/api/settings', { method: 'POST', body: fd });
      if (res.ok) {
        indicator.classList.remove('hidden');
        setTimeout(() => indicator.classList.add('hidden'), 2000);
      }
    } catch (_) { /* ignore save errors */ }
  }, 600);
}

export async function restoreSettings() {
  // Restore sidebar state — always closed on mobile, respect localStorage on desktop
  if (isMobile()) closeDrawer();
  else if (localStorage.getItem('drawerOpen') === '0') closeDrawer(); else openDrawer();

  const m = localStorage.getItem('selectedModel');
  if (m) state.selectedModel = m;

  const l = localStorage.getItem('selectedLanguage');
  if (l !== null) { state.selectedLanguage = l; document.getElementById('language-select').value = l; }

  if (localStorage.getItem('diarize') === 'true') {
    state.diarize = true;
  }

  if (localStorage.getItem('cloudPower') === 'true') {
    state.cloudPower = true;
    const btn = document.getElementById('cloud-power-toggle');
    if (btn) {
      btn.setAttribute('aria-checked', 'true');
      btn.querySelector('span').style.transform = 'translateX(16px)';
      btn.classList.add('bg-brand'); btn.classList.remove('bg-slate-200');
      document.getElementById('cloud-power-section').classList.remove('hidden');
    }
  }
  const savedColabUrl = localStorage.getItem('colabUrl');
  if (savedColabUrl) {
    state.colabUrl = savedColabUrl;
    const cInp = document.getElementById('colab-url-input');
    if (cInp) cInp.value = savedColabUrl;
  }

  // Load token status, exit token, and transcription defaults from server.
  // The server reports whether a Hugging Face token is stored and shows its
  // last four characters; it never sends the token itself.
  let tokenPreview = '';
  try {
    const res = await fetch('/api/settings');
    if (res.ok) {
      const data = await res.json();
      tokenPreview = data.hf_token_set ? (data.hf_token_preview || '••••••••') : '';
      if (data.exit_token) state.exitToken = data.exit_token;
      setMeetingCaptureToggle(!!data.meeting_capture_enabled);
      setAutoSummaryToggle(!!data.auto_summarize_meetings);
      // Use server defaults when localStorage has no value
      if (!localStorage.getItem('selectedModel') && data.default_model) {
        state.selectedModel = data.default_model;
      }
      if (localStorage.getItem('selectedLanguage') === null && data.default_language) {
        state.selectedLanguage = data.default_language;
        document.getElementById('language-select').value = data.default_language;
      }
      if (!localStorage.getItem('diarize') && data.default_diarize) {
        state.diarize = true;
      }
    }
  } catch (_) { /* server not available */ }
  const hfInput = document.getElementById('hf-token-input');
  if (tokenPreview && hfInput) {
    hfInput.value = tokenPreview;
    hfInput.dataset.masked = 'true';
    // Typing replaces the mask; until then the field is not the real token.
    hfInput.addEventListener('focus', () => {
      if (hfInput.dataset.masked === 'true') {
        hfInput.value = '';
        hfInput.dataset.masked = 'false';
      }
    }, { once: true });
  }

  // Apply diarize toggle UI after resolving the value
  if (state.diarize) {
    const btn = document.getElementById('diarize-toggle');
    btn.setAttribute('aria-checked', 'true');
    btn.querySelector('span').style.transform = 'translateX(16px)';
    btn.classList.add('bg-brand'); btn.classList.remove('bg-slate-200');
    document.getElementById('hf-section').classList.remove('hidden');
  }

  // Push the resolved values back so the server (and therefore the meeting
  // watcher) matches what the sidebar shows. Without this an existing user
  // whose settings live only in localStorage would have their auto-captured
  // meetings transcribed with different options than their manual uploads.
  _saveTranscriptionDefaults();

  // Load LLM settings from server
  try {
    const res = await fetch('/api/llm/settings');
    if (res.ok) {
      const cfg = await res.json();
      if (cfg.llm_base_url) document.getElementById('llm-base-url').value = cfg.llm_base_url;
      if (cfg.llm_model_name) document.getElementById('llm-model-input').value = cfg.llm_model_name;
      const keyInput = document.getElementById('llm-api-key');
      if (keyInput && cfg.llm_api_key_set) {
        keyInput.value = '••••••••';
        keyInput.dataset.masked = 'true';
        keyInput.addEventListener('focus', () => {
          if (keyInput.dataset.masked === 'true') {
            keyInput.value = '';
            keyInput.dataset.masked = 'false';
          }
        }, { once: true });
      }
      const contextInput = document.getElementById('llm-context-tokens');
      if (contextInput && cfg.llm_context_tokens) contextInput.value = cfg.llm_context_tokens;

      await loadProviderCatalog();
      applyProvider(cfg.llm_provider);
      const cloudBox = document.getElementById('llm-allow-cloud');
      if (cloudBox) cloudBox.checked = !!cfg.llm_allow_cloud;

      updateAutoSummaryAvailability(!!cfg.llm_model_name);
    }
  } catch (_) { /* server not available */ }
}


// --- automatic meeting summaries -------------------------------------------

export function setAutoSummaryToggle(enabled) {
  const btn = document.getElementById('auto-summary-toggle');
  if (!btn) return;
  btn.setAttribute('aria-checked', String(!!enabled));
  const knob = btn.querySelector('span');
  if (knob) knob.style.transform = enabled ? 'translateX(16px)' : 'translateX(0)';
  btn.classList.toggle('bg-brand', !!enabled);
  btn.classList.toggle('bg-slate-200', !enabled);
}

export function updateAutoSummaryAvailability(llmConfigured) {
  const hint = document.getElementById('auto-summary-hint');
  if (hint) hint.classList.toggle('hidden', llmConfigured);
}

export function initAutoSummaryToggle() {
  const btn = document.getElementById('auto-summary-toggle');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const next = btn.getAttribute('aria-checked') !== 'true';
    setAutoSummaryToggle(next);
    const fd = new FormData();
    fd.append('auto_summarize_meetings', String(next));
    try {
      const res = await fetch('/api/settings', { method: 'POST', body: fd });
      if (!res.ok) throw new Error(String(res.status));
    } catch (_) {
      setAutoSummaryToggle(!next);  // roll the switch back if the save failed
    }
  });
}
