"""Suggesting tags for a transcript with the configured LLM.

Nothing here applies a tag. The model proposes, the user disposes: the endpoint
returns candidates and the existing tag routes attach the ones that are wanted.
An LLM that quietly relabels a library is worse than no tagging at all.

The two things that make suggestions usable rather than merely plausible:

* the model is shown the tags the library already uses, so a second standup is
  tagged ``standup`` and not ``stand-up``, ``daily-standup`` or ``Standup``;
* its reply is parsed forgivingly, because small local models wrap JSON in
  prose, fence it, number it, or ignore the format entirely.
"""
from __future__ import annotations

import json
import re

from core.analysis import LLMTarget, run_completion

# More than this and the chip row stops being a decision and starts being a
# second library to curate.
MAX_SUGGESTIONS = 6

# A tag is a label, not a sentence. Anything longer is the model narrating.
MAX_TAG_CHARS = 32

# The prompt asks for at most three words; anything wordier is prose that
# happened to land in the list.
MAX_TAG_WORDS = 3

# Tokens set aside for the instruction and the (short) reply.
_TAGGING_OVERHEAD_TOKENS = 600

# How many excerpts a transcript too long for the budget is sampled into.
_SAMPLE_WINDOWS = 4

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_JSON_ARRAY_RE = re.compile(r"\[.*?\]", re.S)
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")

# Sentinel: a dict that held no list at all, which is different from a dict
# whose list was empty.
_NOT_A_LIST = object()


def looks_like_tag(text: str) -> bool:
    """Could *text* plausibly be a label rather than a sentence?"""
    cleaned = text.strip()
    return bool(cleaned) and len(cleaned) <= MAX_TAG_CHARS and len(cleaned.split()) <= MAX_TAG_WORDS


def sample_transcript(text: str, max_tokens: int) -> str:
    """Return at most *max_tokens* worth of *text*, spread across the whole thing.

    Truncating to the first N tokens would tag a two-hour meeting by its
    small talk. Taking evenly spaced windows costs nothing and keeps the
    topics from the second half in view.
    """
    from core.analysis import _CHARS_PER_TOKEN, estimate_tokens

    if max_tokens <= 0 or estimate_tokens(text) <= max_tokens:
        return text

    budget = max(1, int(max_tokens * _CHARS_PER_TOKEN))
    window = max(1, budget // _SAMPLE_WINDOWS)
    stride = max(1, (len(text) - window) // max(1, _SAMPLE_WINDOWS - 1))

    windows = []
    for i in range(_SAMPLE_WINDOWS):
        start = min(i * stride, max(0, len(text) - window))
        windows.append(text[start:start + window].strip())

    return "\n\n[…]\n\n".join(w for w in windows if w)


def build_prompt(text: str, existing_tags: list[str]) -> str:
    """The instruction sent to the model."""
    known = ""
    if existing_tags:
        listed = ", ".join(sorted(existing_tags))
        known = (
            "\nThe library already uses these tags. Reuse one whenever it fits "
            "rather than inventing a near-duplicate:\n"
            f"{listed}\n"
        )

    return (
        "You are tagging an audio transcript for a personal library.\n"
        f"Suggest between 1 and {MAX_SUGGESTIONS} short topic tags that would help "
        "someone find this recording again later.\n"
        "Prefer the subject matter — the project, the people, the kind of "
        "conversation. Avoid generic tags like 'audio', 'transcript' or "
        "'recording', which describe every item in the library equally.\n"
        f"Each tag is at most three words and at most {MAX_TAG_CHARS} characters.\n"
        f"{known}"
        "Reply with a JSON array of strings and nothing else, "
        'for example: ["quarterly review", "hiring"]\n\n'
        f"<transcript>\n{text}\n</transcript>"
    )


def parse_suggestions(raw: str) -> list[str]:
    """Pull a tag list out of whatever the model actually said.

    A JSON array is the happy path. Fenced JSON, an array buried in a sentence,
    and a plain bulleted or comma-separated list are all common enough from
    small local models to be worth handling rather than discarding.
    """
    text = (raw or "").strip()
    if not text:
        return []

    fenced = _CODE_FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    for candidate in (text, *(m.group(0) for m in _JSON_ARRAY_RE.finditer(text))):
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        # {"tags": [...]} is what a model does when told to reply with JSON and
        # it decides an object is more helpful.
        if isinstance(parsed, dict):
            parsed = next(
                (v for v in parsed.values() if isinstance(v, list)), _NOT_A_LIST
            )
        if isinstance(parsed, list):
            return [str(item) for item in parsed if isinstance(item, (str, int, float))]
        if parsed is _NOT_A_LIST:
            # It was valid JSON, just not a list of anything. Reading it as a
            # bulleted list below would make '{"tags": "hiring"}' into a tag.
            return []

    # No JSON anywhere: treat it as a list, one per line or comma-separated.
    lines = [_LIST_MARKER_RE.sub("", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) == 1:
        parts = [part.strip() for part in lines[0].split(",") if part.strip()]
        # One unmarked line split on commas is the weakest reading of a reply,
        # and it is also what an ordinary sentence looks like. "I'm sorry, I
        # can't help with that." would otherwise become two tags. Accept it
        # only when every part could actually be one.
        if not parts or any(not looks_like_tag(p) for p in parts):
            return []
        lines = parts
    return [line for line in lines if line]


def clean_suggestions(
    raw_names: list[str],
    existing_tags: list[str] | None = None,
    already_applied: list[str] | None = None,
) -> list[str]:
    """Normalise, de-duplicate and cap the model's list.

    A name that matches an existing tag apart from case comes back spelled the
    way the library already spells it, so accepting the chip reuses that tag
    instead of creating a second one beside it.
    """
    canonical = {t.casefold(): t for t in (existing_tags or [])}
    applied = {t.casefold() for t in (already_applied or [])}

    out: list[str] = []
    seen: set[str] = set()
    for name in raw_names:
        # Models like to hand back '#tag' or a quoted string despite the format.
        cleaned = str(name).strip().strip("\"'").lstrip("#").strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not looks_like_tag(cleaned):
            continue
        key = cleaned.casefold()
        if key in seen or key in applied:
            continue
        seen.add(key)
        out.append(canonical.get(key, cleaned))
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


def suggest_tags(
    full_text: str,
    existing_tags: list[str],
    already_applied: list[str],
    cfg: dict,
) -> list[str]:
    """Ask the model for tags. Returns the cleaned names, possibly empty."""
    budget = max(1, int(cfg.get("llm_context_tokens") or 0) - _TAGGING_OVERHEAD_TOKENS)
    excerpt = sample_transcript(full_text, budget)

    target = LLMTarget.from_options(cfg)
    raw, _ = run_completion(target, build_prompt(excerpt, existing_tags))
    return clean_suggestions(parse_suggestions(raw), existing_tags, already_applied)
