// The recording chip must render exactly what the server sends for `since` —
// see backend/core/watcher_state.py for why the server is the sole owner of
// that value. This test pins the frontend half: no held anchor, no silent
// reuse of a stale timestamp.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { JSDOM } from 'jsdom';

const INDEX_HTML = readFileSync(
  fileURLToPath(new URL('../../frontend/index.html', import.meta.url)),
  'utf8',
);

async function loadWatcher() {
  // watcher.js transitively imports tabs.js/library.js/upload.js, which run
  // DOM lookups at module top-level against the production markup — so the
  // fixture has to be the real index.html, not a hand-built stub.
  const dom = new JSDOM(INDEX_HTML, { url: 'http://localhost/' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true });
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
  globalThis.fetch = async () => ({ ok: false });

  let intervalCb = null;
  globalThis.setInterval = (cb) => { intervalCb = cb; return 1; };
  globalThis.clearInterval = () => {};

  const mod = await import(`../../frontend/js/watcher.js?t=${Date.now()}-${Math.random()}`);
  return { ...mod, tick: () => intervalCb && intervalCb() };
}

test('recording chip timer tracks the server-provided since, not a held anchor', async () => {
  const { _setWatcherUI, tick } = await loadWatcher();
  const realNow = Date.now;

  try {
    Date.now = () => 1_005_000; // since=1000s + 5s elapsed
    _setWatcherUI({ recording: true, app: 'Zoom', since: 1000, alive: true });
    tick();
    assert.equal(document.getElementById('recording-chip-timer').textContent, '0:05');

    // A later heartbeat reports a different since (e.g. server restarted and
    // re-derived it). The chip must follow it exactly, not keep the old anchor.
    Date.now = () => 2_010_000; // since=2000s + 10s elapsed
    _setWatcherUI({ recording: true, app: 'Zoom', since: 2000, alive: true });
    tick();
    assert.equal(document.getElementById('recording-chip-timer').textContent, '0:10');
  } finally {
    Date.now = realNow;
  }
});

test('never falls back to a held anchor when the server reports no since', async () => {
  // Real production traffic never sends since=0/null while recording:true —
  // the server only omits since when recording is false. This pins the
  // frontend's half of the contract regardless: it must not paper over a
  // missing since by quietly reusing whatever it last saw.
  const { _setWatcherUI, tick } = await loadWatcher();
  const realNow = Date.now;
  const timerEl = () => document.getElementById('recording-chip-timer');

  try {
    Date.now = () => 1_005_000; // since=1000s + 5s elapsed
    _setWatcherUI({ recording: true, app: 'Zoom', since: 1000, alive: true });
    tick();
    assert.equal(timerEl().textContent, '0:05');

    // Next heartbeat reports since falsy while still recording. A held
    // anchor would compute elapsed against the stale 1000 and show '0:50'
    // here; the correct behaviour is to stop trusting that anchor, leaving
    // the display exactly as it was rather than advancing from it.
    Date.now = () => 1_050_000; // 1000s-anchor + 50s elapsed, if held
    _setWatcherUI({ recording: true, app: 'Zoom', since: 0, alive: true });
    tick();
    assert.notEqual(timerEl().textContent, '0:50');
    assert.equal(timerEl().textContent, '0:05');
  } finally {
    Date.now = realNow;
  }
});
