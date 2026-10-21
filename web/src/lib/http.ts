import { loginOverlay, showLoginOverlay } from "./auth.svelte";

let installed = false;

/**
 * Wraps window.fetch so an expired/missing session (401) or an unconfigured
 * auth setup (503 auth_setup_required) shows the login overlay instead of
 * every panel on the page failing silently and separately.
 */
export function installAuthAwareFetch(): void {
  if (installed) return;
  installed = true;

  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const response = await originalFetch(input, init);
    const url = typeof input === "string" ? input : input instanceof Request ? input.url : String(input);

    // The auth endpoints answer 401/403 as part of their normal contract — a
    // wrong password must show a form error, not the overlay again.
    if (!url.includes("/api/auth/")) {
      if (response.status === 401) {
        loginOverlay.formHidden = false;
        showLoginOverlay("Your session has expired. Please sign in again.");
      } else if (response.status === 503) {
        const clone = response.clone();
        clone
          .json()
          .then((body) => {
            if (body && body.code === "auth_setup_required") {
              showLoginOverlay(body.detail);
              loginOverlay.formHidden = true;
            }
          })
          .catch(() => {
            /* not a JSON body; leave it to the caller */
          });
      }
    }
    return response;
  };
}
