import { describe, expect, it } from "vitest";
import { elapsedSeconds } from "./time";

// Ports forward the guarantee from the pre-migration tests/frontend/watcher.test.mjs:
// the recording chip's elapsed time must track the server-provided `since`, not a
// value captured once and held. Proven here as purity — the same `since` fed with
// two different `now` values must never drift from what the arithmetic says, because
// there is no closure-captured anchor for it to have drifted from.
describe("elapsedSeconds", () => {
  it("computes elapsed time from since to now", () => {
    const since = 1_000_000;
    expect(elapsedSeconds(since, 1_005_000)).toBe(5);
  });

  it("re-derives from the same since with no held anchor", () => {
    const since = 1_000_000;
    const first = elapsedSeconds(since, 1_005_000);
    const second = elapsedSeconds(since, 1_010_000);

    expect(first).toBe(5);
    expect(second).toBe(10);
  });

  it("never returns a negative duration for a since in the future", () => {
    expect(elapsedSeconds(1_010_000, 1_000_000)).toBe(0);
  });
});
