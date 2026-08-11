// Turning a failed API response into something worth showing a person.
//
// Several call sites did `throw new Error(await res.text())`, so a refused
// action surfaced as `Save failed: {"detail":"A tag called 'x' already
// exists."}` — the answer was right there, wrapped in JSON punctuation.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

/** Read FastAPI's error shape out of *response*, falling back sensibly. */
export async function describeApiError(response, fallback = 'Something went wrong.') {
  let body = null;
  try {
    body = await response.json();
  } catch (_) {
    try {
      const text = await response.text();
      if (text) return text.slice(0, 300);
    } catch (__) { /* body already consumed or empty */ }
  }

  const detail = body && body.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  // Some routes answer with a structured detail (the expired-job payload).
  if (detail && typeof detail === 'object' && detail.message) return detail.message;
  if (body && typeof body.message === 'string') return body.message;

  return `${fallback} (HTTP ${response.status})`;
}

/** Throw with a readable message when *response* is not ok. */
export async function throwIfFailed(response, fallback) {
  if (response.ok) return response;
  throw new Error(await describeApiError(response, fallback));
}
