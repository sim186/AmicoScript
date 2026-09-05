/**
 * Elapsed seconds between a server-provided `since` timestamp and now.
 *
 * Deliberately a pure function of its two arguments, with no module-level
 * state: the meeting-watcher recording chip must always track the `since`
 * the server last reported, never a client-held anchor captured once and
 * reused (see the ported test in time.test.ts, and backend/core/watcher_state.py
 * for why the server is the sole owner of that value).
 */
export function elapsedSeconds(sinceEpochMs: number, nowEpochMs: number): number {
  return Math.max(0, Math.floor((nowEpochMs - sinceEpochMs) / 1000));
}
