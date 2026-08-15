// Meeting auto-capture: recording chip and first-run setup prompt.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { openRecording } from './library.js';
import { state } from './state.js';
import { ensureSessionToken } from './upload.js';

let _watcherMiss = 0;

export let _watcherWasRecording = false;

export let _watcherAutoOpenPending = false;

let _watcherAutoOpenInFlight = false;

let _watcherStoppedAt = 0;

let _watcherRecordingSince = 0;

let _watcherTimerHandle = null;

// Which OS the *browser* is on. Only ever used to pick which installer to
// offer for download — the machine that needs the helper is the one holding
// the browser. Whether the backend can install it itself is a different
// question, and only the server can answer that (see d.host_can_install):
// with the app in Docker the two are different machines.
function _browserPlatform() {
  let p = '';
  try {
    p = (navigator.userAgentData && navigator.userAgentData.platform)
      || navigator.platform || navigator.userAgent || '';
  } catch (_) { return ''; }
  if (/win/i.test(p)) return 'windows';
  if (/mac|iphone|ipad/i.test(p)) return 'macos';
  if (/linux|x11|cros/i.test(p)) return 'linux';
  return '';
}

function _installerFor(d) {
  const installers = d.installers || {};
  return installers[_browserPlatform()] || null;
}

function _formatElapsed(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

function _tickRecordingTimer() {
  const timerEl = document.getElementById('recording-chip-timer');
  if (!timerEl || !_watcherRecordingSince) return;
  timerEl.textContent = _formatElapsed(Date.now() / 1000 - _watcherRecordingSince);
}

export async function _maybeOpenLatestWatcherRecording() {
  if (!_watcherAutoOpenPending || _watcherAutoOpenInFlight) return;
  if (_watcherWasRecording) return;
  _watcherAutoOpenInFlight = true;
  try {
    const res = await fetch('/api/library?sort=created_at&order=desc&limit=1&status=done');
    if (!res.ok) return;
    const rows = await res.json();
    const rec = rows && rows[0];
    if (!rec) return;
    if (_watcherStoppedAt && rec.created_at && rec.created_at + 10 < _watcherStoppedAt) return;
    if (state.activeRecordingId === rec.id && state.result) {
      _watcherAutoOpenPending = false;
      return;
    }
    const opened = await openRecording(rec, true);
    if (opened) _watcherAutoOpenPending = false;
  } catch (_) {
    // Try again on the next poll.
  } finally {
    _watcherAutoOpenInFlight = false;
  }
}

function _setWatcherUI(d) {
  const wasRecording = _watcherWasRecording;
  _watcherWasRecording = !!d.recording;
  if (!d.recording && wasRecording) {
    _watcherAutoOpenPending = true;
    _watcherStoppedAt = Date.now() / 1000;
  }

  // Bottom-right recording chip.
  const chip = document.getElementById('recording-chip');
  if (chip) {
    if (d.recording) {
      const appEl = document.getElementById('recording-chip-app');
      if (appEl) appEl.textContent = d.app ? `· ${d.app}` : '';
      chip.classList.remove('hidden');
      _watcherRecordingSince = d.since || _watcherRecordingSince || (Date.now() / 1000);
      _tickRecordingTimer();
      if (!_watcherTimerHandle) _watcherTimerHandle = setInterval(_tickRecordingTimer, 1000);
    } else {
      chip.classList.add('hidden');
      _watcherRecordingSince = 0;
      if (_watcherTimerHandle) { clearInterval(_watcherTimerHandle); _watcherTimerHandle = null; }
    }
  }

  // Sidebar helper-status line + download link.
  const installer = _installerFor(d);
  const canInstall = !!(d.host_can_install || installer);
  const line = document.getElementById('watcher-state-line');
  if (line) {
    if (d.alive && d.unsupported) {
      // Running, heartbeating, and unable to record a thing. Saying only
      // "Helper running" here would be the most misleading state in the UI.
      line.textContent = `● Helper running — ${d.unsupported}`;
      line.className = 'text-[10px] leading-tight text-amber-600';
    } else if (d.alive) {
      line.textContent = d.recording ? '● Helper running — recording' : '● Helper running';
      line.className = 'text-[10px] leading-tight text-emerald-600';
    } else if (canInstall) {
      line.textContent = '● Helper not running';
      line.className = 'text-[10px] leading-tight text-amber-600';
    } else {
      line.textContent = '● Helper not running — no setup available for this system';
      line.className = 'text-[10px] leading-tight text-slate-400';
    }
  }
  const link = document.getElementById('watcher-setup-link');
  if (link) {
    if (installer) {
      link.href = installer.url;
      link.setAttribute('download', installer.name);
      link.textContent = `Set it up (${installer.name})`;
      link.title = installer.hint || '';
    }
    link.classList.toggle('hidden', !!d.alive || !installer);
  }
  const onboardLink = document.getElementById('watcher-onboard-setup');
  if (onboardLink && installer) {
    onboardLink.href = installer.url;
    onboardLink.setAttribute('download', installer.name);
  }
  const hint = document.getElementById('watcher-onboard-hint');
  if (hint && installer) hint.textContent = installer.hint || '';

  // First-run onboarding banner: ask once; auto-hide as soon as the helper
  // is alive; stay hidden if the user dismissed it or can't run the setup.
  const banner = document.getElementById('watcher-onboard-banner');
  const steps = document.getElementById('watcher-onboard-steps');
  if (d.alive || !canInstall) {
    if (d.alive) _watcherMiss = 0;
    if (banner) banner.classList.add('hidden');
    if (steps) steps.classList.add('hidden');
  } else {
    _watcherMiss++;
    let dismissed = false;
    try { dismissed = localStorage.getItem('watcherSetupDismissed') === '1'; } catch (_) {}
    if (banner && !dismissed && _watcherMiss >= 2) banner.classList.remove('hidden');
  }

  if (_watcherAutoOpenPending && !d.recording) {
    _maybeOpenLatestWatcherRecording();
  }

  // Outdated installed-watcher banner. Re-shows for each newly-seen
  // current_version even if a previous version was dismissed.
  const updateBanner = document.getElementById('watcher-update-banner');
  if (updateBanner) {
    if (d.update_available) {
      let dismissedVersion = '';
      try { dismissedVersion = localStorage.getItem('watcherUpdateDismissedFor') || ''; } catch (_) {}
      updateBanner.classList.toggle('hidden', dismissedVersion === d.current_version);
    } else {
      updateBanner.classList.add('hidden');
    }
  }
}

async function refreshRecordingChip() {
  try {
    const res = await fetch('/api/watcher/status');
    if (!res.ok) return;
    _setWatcherUI(await res.json());
  } catch (_) { /* server momentarily unreachable — leave UI as-is */ }
}

window.refreshRecordingChip = refreshRecordingChip;

function initWatcherOnboarding() {
  const dismiss = document.getElementById('watcher-onboard-dismiss');
  const setup = document.getElementById('watcher-onboard-setup');
  const banner = document.getElementById('watcher-onboard-banner');
  const steps = document.getElementById('watcher-onboard-steps');
  if (dismiss) dismiss.addEventListener('click', () => {
    try { localStorage.setItem('watcherSetupDismissed', '1'); } catch (_) {}
    if (banner) banner.classList.add('hidden');
    if (steps) steps.classList.add('hidden');
  });
  // The link downloads the setup script natively; just reveal the next step.
  if (setup) setup.addEventListener('click', () => { if (steps) steps.classList.remove('hidden'); });
}

function initWatcherUpdateBanner() {
  const banner = document.getElementById('watcher-update-banner');
  const text = document.getElementById('watcher-update-text');
  const updateBtn = document.getElementById('watcher-update-now');
  const dismiss = document.getElementById('watcher-update-dismiss');
  if (!banner) return;
  if (dismiss) dismiss.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/watcher/status');
      const d = res.ok ? await res.json() : {};
      localStorage.setItem('watcherUpdateDismissedFor', d.current_version || '');
    } catch (_) {}
    banner.classList.add('hidden');
  });
  if (updateBtn) updateBtn.addEventListener('click', async () => {
    const originalLabel = updateBtn.textContent;
    updateBtn.textContent = 'Updating…';
    updateBtn.disabled = true;
    try {
      const token = await ensureSessionToken();
      const fd = new FormData();
      fd.append('token', token);
      const res = await fetch('/api/watcher/install', { method: 'POST', body: fd });
      const data = res.ok ? await res.json() : { ok: false };
      if (data.ok) {
        if (text) text.textContent = 'Helper updated — restarting…';
        setTimeout(refreshRecordingChip, 2000);
      } else {
        if (text) {
          // The backend may be on a different machine entirely (Docker), in
          // which case there is nothing for it to update — the user's own
          // computer is the one holding the helper.
          text.textContent = data.error === 'unsupported_host'
            ? "Can't auto-update from here — download and run the setup script on this computer instead."
            : `Auto-update failed (${data.error || 'unknown error'}) — try the setup script instead.`;
        }
        const onboardBanner = document.getElementById('watcher-onboard-banner');
        if (onboardBanner) onboardBanner.classList.remove('hidden');
      }
    } finally {
      updateBtn.textContent = originalLabel;
      updateBtn.disabled = false;
    }
  });
}

setInterval(refreshRecordingChip, 1000);

document.addEventListener('DOMContentLoaded', () => { initWatcherOnboarding(); initWatcherUpdateBanner(); refreshRecordingChip(); });
