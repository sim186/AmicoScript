// Subsequence fuzzy matching, for the command palette.
//
// A deliberate port of tui/fuzzy.py, weights and all: the terminal palette and
// this one are the same feature on two surfaces, and "gl" should put "Go to
// Library" first in both.

const PREFIX_BONUS = 60;
const BOUNDARY_BONUS = 25;
const CONSECUTIVE_BONUS = 15;
const BASE_HIT = 5;
const LENGTH_PENALTY = 0.5;

function isAlnum(ch: string): boolean {
  return /[\p{L}\p{N}]/u.test(ch);
}

/** Score (higher is better), or null when text is not a match at all. */
export function scoreMatch(query: string, text: string): number | null {
  if (!query) return 0;
  const q = query.toLowerCase();
  const t = (text || "").toLowerCase();
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
  return score - Math.round(t.length * LENGTH_PENALTY);
}

export function rank<T>(query: string, items: T[], key: (item: T) => string = String): { score: number; item: T }[] {
  if (!query) return items.map((item) => ({ score: 0, item }));
  const out: { score: number; item: T }[] = [];
  for (const item of items) {
    const score = scoreMatch(query, key(item));
    if (score !== null) out.push({ score, item });
  }
  out.sort((a, b) => b.score - a.score);
  return out;
}
