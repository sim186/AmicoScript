// Login gate and password management.
//
// On a normal local install none of this is visible: the server lets loopback
// requests through without credentials, so the overlay never appears and the
// Security panel simply offers to set a password. It matters when AmicoScript
// is reachable from the network, where the server refuses every API call until
// a password exists and the client has a session.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

let authState = { enabled: true, password_set: false, login_required: false, local: true };

export function getAuthState() {
  return authState;
}

export async function refreshAuthState() {
  try {
    const res = await fetch('/api/auth/status');
    if (res.ok) authState = await res.json();
  } catch (_) { /* server not reachable; leave the last known state */ }
  renderAuthPanel();
  return authState;
}

function show(el, visible) {
  if (el) el.classList.toggle('hidden', !visible);
}

function feedback(id, message, tone) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.className = `text-[10px] leading-tight ${
    tone === 'error' ? 'text-red-600' : tone === 'ok' ? 'text-green-600' : 'text-slate-500'
  }`;
  show(el, !!message);
}

// --- login overlay ----------------------------------------------------------

export function showLoginOverlay(message) {
  const overlay = document.getElementById('login-overlay');
  if (!overlay) return;
  const msg = document.getElementById('login-message');
  if (msg && message) msg.textContent = message;
  overlay.classList.remove('hidden');
  document.getElementById('login-password')?.focus();
}

export function hideLoginOverlay() {
  document.getElementById('login-overlay')?.classList.add('hidden');
}

async function submitLogin(event) {
  event.preventDefault();
  const input = document.getElementById('login-password');
  const error = document.getElementById('login-error');
  const fd = new FormData();
  fd.append('password', input.value);

  try {
    const res = await fetch('/api/auth/login', { method: 'POST', body: fd });
    if (res.ok) {
      hideLoginOverlay();
      // Reload rather than patching state: every panel fetched its data while
      // the API was refusing requests, so the page is showing nothing useful.
      window.location.reload();
      return;
    }
    const body = await res.json().catch(() => ({}));
    error.textContent = body.detail || 'Sign in failed.';
    error.classList.remove('hidden');
  } catch (err) {
    error.textContent = err.message;
    error.classList.remove('hidden');
  }
  input.value = '';
}

// --- security panel ---------------------------------------------------------

function renderAuthPanel() {
  const line = document.getElementById('auth-state-line');
  const current = document.getElementById('auth-current-password');
  const saveBtn = document.getElementById('auth-save-btn');
  const removeBtn = document.getElementById('auth-remove-btn');
  if (!line) return;

  if (!authState.enabled) {
    line.textContent =
      'Authentication is disabled (AMICOSCRIPT_AUTH=off). Anything that can reach '
      + 'this server has full access.';
    return;
  }

  if (authState.password_set) {
    line.textContent = 'A password is set. Access from other machines requires signing in.';
    show(current, true);
    if (saveBtn) saveBtn.textContent = 'Change password';
    show(removeBtn, authState.local);
  } else {
    line.textContent =
      'No password set. This machine has full access; requests from the network are '
      + 'refused until you set one.';
    show(current, false);
    if (saveBtn) saveBtn.textContent = 'Set password';
    show(removeBtn, false);
  }
}

async function savePassword() {
  const newPassword = document.getElementById('auth-new-password');
  const current = document.getElementById('auth-current-password');
  if (!newPassword?.value) {
    feedback('auth-feedback', 'Enter a new password first.', 'error');
    return;
  }

  const fd = new FormData();
  fd.append('new_password', newPassword.value);
  fd.append('current_password', current?.value || '');

  try {
    const res = await fetch('/api/auth/password', { method: 'POST', body: fd });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      feedback('auth-feedback', body.detail || 'Could not save the password.', 'error');
      return;
    }
    newPassword.value = '';
    if (current) current.value = '';
    feedback('auth-feedback', 'Password saved.', 'ok');
    if (body.api_token) {
      const tokenInput = document.getElementById('auth-api-token');
      if (tokenInput) tokenInput.value = body.api_token;
      show(document.getElementById('auth-token-row'), true);
    }
    await refreshAuthState();
  } catch (err) {
    feedback('auth-feedback', err.message, 'error');
  }
}

async function removePassword() {
  if (!confirm('Remove the password? Anyone who can reach this server will have full access.')) {
    return;
  }
  const current = document.getElementById('auth-current-password');
  const params = new URLSearchParams({ current_password: current?.value || '' });
  try {
    const res = await fetch(`/api/auth/password?${params}`, { method: 'DELETE' });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      feedback('auth-feedback', body.detail || 'Could not remove the password.', 'error');
      return;
    }
    if (current) current.value = '';
    feedback('auth-feedback', 'Password removed.', 'ok');
    show(document.getElementById('auth-token-row'), false);
    await refreshAuthState();
  } catch (err) {
    feedback('auth-feedback', err.message, 'error');
  }
}

export function initAuth() {
  document.getElementById('login-form')?.addEventListener('submit', submitLogin);
  document.getElementById('auth-save-btn')?.addEventListener('click', savePassword);
  document.getElementById('auth-remove-btn')?.addEventListener('click', removePassword);

  refreshAuthState().then((status) => {
    if (status.enabled && status.login_required) {
      showLoginOverlay(
        status.password_set
          ? 'Enter your password to continue.'
          : 'This AmicoScript has no password set, so it refuses connections from the '
            + 'network. Set one from the app on the machine it runs on.',
      );
      if (!status.password_set) {
        document.getElementById('login-form')?.classList.add('hidden');
      }
    }
  });
}
