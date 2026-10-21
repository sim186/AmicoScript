/** Read FastAPI's error shape out of *response*, falling back sensibly. */
export async function describeApiError(response: Response, fallback = "Something went wrong."): Promise<string> {
  let body: any = null;
  try {
    body = await response.json();
  } catch {
    try {
      const text = await response.text();
      if (text) return text.slice(0, 300);
    } catch {
      /* body already consumed or empty */
    }
  }

  const detail = body && body.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && detail.message) return detail.message;
  if (body && typeof body.message === "string") return body.message;

  return `${fallback} (HTTP ${response.status})`;
}

/** Throw with a readable message when *response* is not ok. */
export async function throwIfFailed(response: Response, fallback?: string): Promise<Response> {
  if (response.ok) return response;
  throw new Error(await describeApiError(response, fallback));
}
