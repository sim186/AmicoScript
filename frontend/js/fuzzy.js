// Subsequence fuzzy matching, for the command palette.
//
// A deliberate port of tui/fuzzy.py, weights and all: the terminal palette and
// this one are the same feature on two surfaces, and "gl" should put "Go to
// Library" first in both.
//
// Part of the AmicoScript frontend. No build step: these are plain ES
// modules loaded directly by the browser via <script type="module">.

const PREFIX_BONUS = 60;
const BOUNDARY_BONUS = 25;
const CONSECUTIVE_BONUS = 15;
const BASE_HIT = 5;
const LENGTH_PENALTY = 0.5;

function isAlnum(ch) {
  return /[\p{L}\p{N}]/u.test(ch);
}

// Returns a score (higher is better), or null when text is not a match at all.
// Every character of the query has to appear in order; the bonuses reward the
// matches a human would call obvious — start of the string, start of a word,
// runs of adjacent characters.
export function scoreMatch(query, text) {
  if (!query) return 0;
  const q = query.toLowerCase();
  const t = (text || '').toLowerCase();
  let qi = 0;
  let score = 0;
  let lastIdx = -2;

  for (let i = 0; i < t.length && qi < q.length; i++) {
    if (t[i] !== q[qi]) continue;
    let hit = BASE_HIT;
    if (i === 0 && qi === 0) hit += PREFIX_BONUS;
    if (i > 0 && !isAlnum(t[i - 1])) hit += BOUNDARY_BONUS;
    if (i === lastIdx + 1) hit += CONSECUTIVE_BONUS;
    score += hit;
    lastIdx = i;
    qi++;
  }

  if (qi < q.length) return null;
  // Without this a long string that happens to contain the letters outranks
  // the short one that is obviously meant.
  return score - Math.round(t.length * LENGTH_PENALTY);
}

// Filter and order `items` by how well `key(item)` matches. An empty query
// keeps the caller's order, which is how the palette shows its default list.
export function rank(query, items, key = String) {
  if (!query) return items.map(item => ({ score: 0, item }));
  const out = [];
  for (const item of items) {
    const score = scoreMatch(query, key(item));
    if (score !== null) out.push({ score, item });
  }
  out.sort((a, b) => b.score - a.score);
  return out;
}
