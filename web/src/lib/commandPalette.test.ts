import { describe, expect, it } from "vitest";
import { escapeHtml, markedSnippet } from "./commandPalette.svelte";

// /api/search wraps matches in <mark>, so a snippet cannot simply be assigned
// as HTML — it must be escaped whole and only the <mark>/</mark> tags put
// back, or a transcript containing "<script>" would run as one. Ported
// forward from the pre-migration test_snippets_are_escaped_before_they_are_marked.
describe("markedSnippet", () => {
  it("escapes everything except the mark tags the server wrapped matches in", () => {
    const raw = "<script>alert(1)</script> said <mark>hello</mark> to the room";
    const html = markedSnippet(raw);

    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("<mark>hello</mark>");
  });

  it("leaves plain text untouched", () => {
    expect(markedSnippet("just a normal sentence")).toBe("just a normal sentence");
  });
});

describe("escapeHtml", () => {
  it("escapes the five HTML-significant characters", () => {
    expect(escapeHtml(`<a href="x">it's & "fine"</a>`)).toBe(
      "&lt;a href=&quot;x&quot;&gt;it&#39;s &amp; &quot;fine&quot;&lt;/a&gt;",
    );
  });
});
