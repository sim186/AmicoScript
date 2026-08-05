// Choosing and configuring an LLM backend.
//
// The old flow was a single "Base URL" box, which assumed you knew that LM
// Studio listens on 1234, that Unsloth needs a key, and that "localhost" means
// the container when AmicoScript runs in Docker. This module turns that into:
// pick a provider, or press a button and let AmicoScript find what is already
// running.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { clientLog } from './upload.js';

let catalog = [];
let runtime = { in_container: false, container_host: '' };

export function getProvider(id) {
  return catalog.find((p) => p.id === id) || null;
}

function el(id) {
  return document.getElementById(id);
}

function setText(id, text, tone) {
  const node = el(id);
  if (!node) return;
  node.textContent = text || '';
  if (tone) {
    node.className = `text-[10px] leading-tight mt-1 ${
      tone === 'error' ? 'text-red-600' : tone === 'ok' ? 'text-green-600' : 'text-slate-500'
    }`;
  }
  node.classList.toggle('hidden', !text);
}

// --- provider selection -----------------------------------------------------

export async function loadProviderCatalog() {
  try {
    const res = await fetch('/api/llm/providers');
    if (!res.ok) return;
    const body = await res.json();
    catalog = body.providers || [];
    runtime = { in_container: body.in_container, container_host: body.container_host };
  } catch (_) { /* server not reachable; the plain URL field still works */ }

  const select = el('llm-provider-select');
  if (!select) return;
  select.innerHTML = catalog
    .map((p) => `<option value="${p.id}">${p.label}</option>`)
    .join('');
}

/** Reflect a provider's requirements in the form. */
export function applyProvider(providerId, { fillUrl = false } = {}) {
  const provider = getProvider(providerId);
  if (!provider) return;

  const select = el('llm-provider-select');
  if (select) select.value = provider.id;

  let notes = provider.notes || '';
  if (runtime.in_container && !provider.cloud) {
    notes += ` AmicoScript is running in a container, so a server on your machine is `
      + `reached at ${runtime.container_host} — addresses you type as localhost are `
      + `converted automatically.`;
  }
  setText('llm-provider-notes', notes.trim());

  const docs = el('llm-provider-docs');
  if (docs) {
    docs.href = provider.docs_url || '#';
    docs.classList.toggle('hidden', !provider.docs_url);
  }

  const requirement = el('llm-api-key-requirement');
  if (requirement) {
    requirement.textContent =
      provider.api_key === 'required'
        ? `(required${provider.key_hint ? ` — ${provider.key_hint}` : ''})`
        : provider.api_key === 'none' ? '(not needed)' : '(optional)';
  }

  el('llm-cloud-consent')?.classList.toggle('hidden', !provider.cloud);

  const urlInput = el('llm-base-url');
  if (urlInput) {
    urlInput.placeholder = provider.base_url || 'https://your-server.example.com';
    if (fillUrl && provider.base_url) urlInput.value = provider.base_url;
  }

  // Only Ollama can fetch models on request; for the others the list is
  // whatever they already have loaded.
  const browseBtn = el('llm-browse-models-btn');
  if (browseBtn) {
    browseBtn.title = provider.supports_pull
      ? 'Browse and download models'
      : 'Browse the models this server has loaded';
  }
}

// --- detection --------------------------------------------------------------

export async function detectServers() {
  const button = el('llm-detect-btn');
  const results = el('llm-detect-results');
  if (!results) return;

  const label = button?.textContent;
  if (button) { button.disabled = true; button.textContent = 'Scanning…'; }
  results.innerHTML = '';
  results.classList.add('hidden');
  setText('llm-detect-status', 'Checking the usual ports…', 'info');

  try {
    const res = await fetch('/api/llm/detect');
    const body = await res.json();
    const servers = body.servers || [];

    if (!servers.length) {
      setText(
        'llm-detect-status',
        `Nothing answered on ${(body.scanned || []).length} addresses. Start Ollama, `
        + 'LM Studio or Unsloth Studio and scan again, or enter an address by hand.',
        'error',
      );
      return;
    }

    results.innerHTML = servers.map(renderServer).join('');
    results.classList.remove('hidden');
    results.querySelectorAll('[data-use-server]').forEach((btn) => {
      btn.addEventListener('click', () => useServer(servers[Number(btn.dataset.useServer)]));
    });
    setText(
      'llm-detect-status',
      `Found ${servers.length} server${servers.length > 1 ? 's' : ''}.`,
      'ok',
    );
    clientLog(`LLM scan found: ${servers.map((s) => s.label).join(', ')}`);
  } catch (err) {
    setText('llm-detect-status', `Scan failed: ${err.message}`, 'error');
  } finally {
    if (button) { button.disabled = false; button.textContent = label; }
  }
}

function renderServer(server, index) {
  const models = server.model_count || 0;
  const detail = server.needs_api_key
    ? '<span class="text-amber-600">needs an API key</span>'
    : `${models} model${models === 1 ? '' : 's'}`;
  return `
    <div class="flex items-center justify-between gap-2 rounded-lg border border-slate-200 p-1.5">
      <div class="min-w-0">
        <p class="text-xs font-semibold text-slate-700 truncate">${server.label}</p>
        <p class="text-[10px] text-slate-400 truncate">${server.base_url} · ${detail}</p>
      </div>
      <button type="button" data-use-server="${index}"
        class="shrink-0 text-[10px] px-2 py-1 rounded-md bg-brand text-white hover:bg-brand-hover transition">
        Use
      </button>
    </div>`;
}

function useServer(server) {
  applyProvider(server.provider);
  const urlInput = el('llm-base-url');
  if (urlInput) urlInput.value = server.base_url;

  // Prefer a model the server already has loaded, so the form is usable
  // immediately rather than needing a second trip to the model browser.
  const modelInput = el('llm-model-input');
  if (modelInput && !modelInput.value && server.models?.length) {
    modelInput.value = server.models[0].id;
  }

  setText(
    'llm-detect-status',
    server.needs_api_key
      ? `Selected ${server.label}. Paste its API key below, then save.`
      : `Selected ${server.label}.`,
    'ok',
  );
  window.dispatchEvent(new CustomEvent('amicoscript:llm-config-changed'));
}

// --- wiring -----------------------------------------------------------------

export function initLlmSetup() {
  el('llm-detect-btn')?.addEventListener('click', detectServers);

  el('llm-provider-select')?.addEventListener('change', (event) => {
    // Switching provider fills in that provider's default address; the user can
    // still overwrite it.
    applyProvider(event.target.value, { fillUrl: true });
    window.dispatchEvent(new CustomEvent('amicoscript:llm-config-changed'));
  });

  el('llm-allow-cloud')?.addEventListener('change', () => {
    window.dispatchEvent(new CustomEvent('amicoscript:llm-config-changed'));
  });
}

/** Values the save handler needs from this panel. */
export function currentProviderFields() {
  return {
    llm_provider: el('llm-provider-select')?.value || '',
    llm_allow_cloud: String(!!el('llm-allow-cloud')?.checked),
  };
}

export function showUrlNote(note) {
  const node = el('llm-url-note');
  if (!node) return;
  node.textContent = note || '';
  node.classList.toggle('hidden', !note);
}
