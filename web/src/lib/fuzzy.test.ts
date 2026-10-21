import { describe, expect, it } from "vitest";
import { rank, scoreMatch } from "./fuzzy";

// A deliberate port of tui/fuzzy.py's guarantees: the terminal palette and the
// web one are the same feature on two surfaces, so "gl" should put "Go to
// Library" first in both.
describe("scoreMatch", () => {
  it("requires every query character to appear in order", () => {
    expect(scoreMatch("xyz", "Go to Library")).toBeNull();
  });

  it("rewards a prefix match over a mid-string match", () => {
    const prefix = scoreMatch("go", "Go to Library");
    const midString = scoreMatch("go", "Ask your library, go on");
    expect(prefix).not.toBeNull();
    expect(midString).not.toBeNull();
    expect(prefix!).toBeGreaterThan(midString!);
  });

  it("rewards a word-boundary match over a mid-word match", () => {
    const boundary = scoreMatch("l", "Go to Library");
    const midWord = scoreMatch("l", "Go to xLibrary");
    expect(boundary!).toBeGreaterThan(midWord!);
  });
});

describe("rank", () => {
  it("keeps the caller's order for an empty query", () => {
    const items = ["b", "a", "c"];
    expect(rank("", items).map((r) => r.item)).toEqual(items);
  });

  it("puts 'Go to Library' first for the query 'gl'", () => {
    const items = ["Go to Transcribe", "Go to Transcript", "Go to Library"];
    const ranked = rank("gl", items);
    expect(ranked[0]?.item).toBe("Go to Library");
  });

  it("drops items that don't match at all", () => {
    const items = ["Go to Library", "Toggle the console"];
    const ranked = rank("zzz", items);
    expect(ranked).toEqual([]);
  });
});
