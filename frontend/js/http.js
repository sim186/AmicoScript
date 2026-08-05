// A fetch wrapper that notices when the server has stopped trusting us.
//
// Every panel in the UI calls fetch directly and handles its own errors, which
// is fine for a 404 but not for a 401: an expired or missing session makes
// *every* request fail at once, and without this the page would just render a
// dozen empty panels with no explanation.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

import { showLoginOverlay } from './auth.js';

let installed = false;

export function installAuthAwareFetch() {
  if (installed) return;
  installed = true;

  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input, init) => {
    const response = await originalFetch(input, init);
    const url = typeof input === 'string' ? input : (input && input.url) || '';

    // The auth endpoints answer 401/403 as part of their normal contract —
    // a wrong password must show a form error, not the overlay again.
    if (!url.includes('/api/auth/')) {
      if (response.status === 401) {
        showLoginOverlay('Your session has expired. Please sign in again.');
      } else if (response.status === 503) {
        const clone = response.clone();
        clone.json().then((body) => {
          if (body && body.code === 'auth_setup_required') {
            showLoginOverlay(body.detail);
            document.getElementById('login-form')?.classList.add('hidden');
          }
        }).catch(() => { /* not a JSON body; leave it to the caller */ });
      }
    }
    return response;
  };
}
