"""LLM analysis job processing.

Long transcripts are the normal case here, not the exception: a one-hour
meeting is roughly 9k words ≈ 12k tokens, and local servers commonly run a
4k–8k context window. Sending the whole thing in one prompt means the model
silently drops the oldest part of the input, which produced confident
summaries covering only the *end* of the recording.

So anything that does not fit the configured budget is processed map-reduce
style: summarise each chunk, then summarise the summaries. Translation is the
exception — its chunks are concatenated rather than reduced, because merging
translated passages would rewrite them.
"""
import json as _json
import re
from dataclasses import dataclass
from json import JSONDecodeError

import requests as _req

import state
from core.job_helpers import append_job_log, handle_job_error, push_event
from db import new_session
from llm_providers import build_headers, chat_url, get_provider
from models import Analysis

# Rough characters-per-token ratio for English prose. Real tokenizers vary by
# model, so this is deliberately conservative — over-estimating the token count
# costs an extra chunk boundary, under-estimating costs silent truncation.
_CHARS_PER_TOKEN = 3.6

# Tokens reserved inside the context window for the instruction text and the
# model's own reply, so the transcript never fills the whole budget.
_PROMPT_OVERHEAD_TOKENS = 400


def estimate_tokens(text: str) -> int:
    """Approximate the token count of *text* (over-estimates on purpose)."""
    if not text:
        return 0
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines, then lines, then sentences — the natural seams."""
    parts = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(parts) > 1:
        return parts
    parts = [p for p in text.splitlines() if p.strip()]
    if len(parts) > 1:
        return parts
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _hard_split(piece: str, max_chars: int) -> list[str]:
    """Last resort for a single unit longer than a whole chunk (no punctuation)."""
    return [piece[i:i + max_chars] for i in range(0, len(piece), max_chars)]


def chunk_text(text: str, max_tokens: int) -> list[str]:
    """Split *text* into pieces that each fit within *max_tokens*.

    Splits on paragraph, then line, then sentence boundaries so a chunk rarely
    cuts through the middle of a spoken sentence.
    """
    if max_tokens <= 0 or estimate_tokens(text) <= max_tokens:
        return [text] if text.strip() else []

    # -1 keeps the invariant estimate_tokens(chunk) <= max_tokens exact, rather
    # than one token over on a chunk that lands exactly on the boundary.
    max_chars = max(1, int(max_tokens * _CHARS_PER_TOKEN) - 1)
    chunks: list[str] = []
    current = ""

    for piece in _split_paragraphs(text):
        if len(piece) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(piece, max_chars))
            continue
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = candidate

    if current.strip():
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def build_analysis_prompt(
    analysis_type: str,
    full_text: str,
    target_language: str = "",
    custom_prompt: str = "",
    output_language: str = "",
) -> str:
    """Build the LLM prompt string for an analysis type."""
    text_block = f"<transcript>\n{full_text}\n</transcript>"
    lang_suffix = f"\n\nPlease respond in {output_language}." if output_language.strip() else ""

    if analysis_type == "summary":
        return (
            "You are a helpful assistant. Provide a clear, concise summary of the "
            "following audio transcript. Focus on the main topics, decisions, and key points.\n\n"
            + text_block
            + lang_suffix
        )
    if analysis_type == "action_items":
        return (
            "You are a helpful assistant. Extract all action items, tasks, and to-dos from "
            "the following audio transcript. Format them as a bulleted list. "
            "If there are no action items, say so explicitly.\n\n"
            + text_block
            + lang_suffix
        )
    if analysis_type == "translate":
        lang = target_language.strip() or "English"
        return (
            f"You are a professional translator. Translate the following audio transcript "
            f"into {lang}. Preserve the meaning and tone faithfully. "
            "Output only the translated text, no explanations.\n\n"
            + text_block
        )
    if analysis_type == "custom":
        return f"{custom_prompt}\n\n{text_block}{lang_suffix}"

    raise ValueError(f"Unknown analysis_type: {analysis_type!r}")


def _build_map_prompt(
    analysis_type: str,
    chunk: str,
    index: int,
    total: int,
    target_language: str = "",
    custom_prompt: str = "",
    output_language: str = "",
) -> str:
    """Prompt for one chunk of a transcript that is too long to send at once."""
    text_block = f"<transcript_part>\n{chunk}\n</transcript_part>"
    lang_suffix = f"\n\nPlease respond in {output_language}." if output_language.strip() else ""
    position = f"This is part {index} of {total} of a longer recording."

    if analysis_type == "summary":
        return (
            "You are a helpful assistant. " + position + " Summarise this part on its own, "
            "keeping the main topics, decisions and key points. Do not add an introduction "
            "or note that it is partial.\n\n" + text_block + lang_suffix
        )
    if analysis_type == "action_items":
        return (
            "You are a helpful assistant. " + position + " List every action item, task or "
            "to-do mentioned in this part as a bulleted list. Reply with an empty list if "
            "there are none.\n\n" + text_block + lang_suffix
        )
    if analysis_type == "translate":
        lang = target_language.strip() or "English"
        return (
            f"You are a professional translator. {position} Translate it into {lang}, "
            "preserving meaning and tone. Output only the translated text.\n\n" + text_block
        )
    if analysis_type == "custom":
        return f"{custom_prompt}\n\n{position}\n\n{text_block}{lang_suffix}"

    raise ValueError(f"Unknown analysis_type: {analysis_type!r}")


def _build_reduce_prompt(
    analysis_type: str,
    partials: list[str],
    custom_prompt: str = "",
    output_language: str = "",
) -> str:
    """Prompt that merges per-chunk outputs into one answer."""
    joined = "\n\n".join(
        f"<part index=\"{i}\">\n{p}\n</part>" for i, p in enumerate(partials, 1)
    )
    lang_suffix = f"\n\nPlease respond in {output_language}." if output_language.strip() else ""

    if analysis_type == "summary":
        return (
            "You are a helpful assistant. Below are summaries of consecutive parts of one "
            "recording. Merge them into a single coherent summary of the whole recording. "
            "Remove repetition, keep the chronology, and do not mention that it was "
            "processed in parts.\n\n" + joined + lang_suffix
        )
    if analysis_type == "action_items":
        return (
            "You are a helpful assistant. Below are action items extracted from consecutive "
            "parts of one recording. Merge them into a single de-duplicated bulleted list, "
            "keeping the owner and any deadline where mentioned. If there are none, say so "
            "explicitly.\n\n" + joined + lang_suffix
        )
    if analysis_type == "custom":
        return (
            "Below are answers produced for consecutive parts of one recording, each "
            "following this instruction:\n\n"
            f"<instruction>\n{custom_prompt}\n</instruction>\n\n"
            "Merge them into a single consolidated answer, removing repetition.\n\n"
            + joined
            + lang_suffix
        )

    raise ValueError(f"Analysis type {analysis_type!r} is not reducible")


# ---------------------------------------------------------------------------
# LLM transport
# ---------------------------------------------------------------------------


class LLMError(RuntimeError):
    """Raised when the LLM endpoint cannot be used at all."""


@dataclass(frozen=True)
class LLMTarget:
    """Everything needed to make one request, kept together.

    Passing base_url/model/key/provider/token-limit as five positional
    arguments through map-reduce meant threading the same tuple through six
    call sites; one object keeps them from drifting apart.
    """

    base_url: str
    model_name: str
    api_key: str = ""
    provider_id: str = ""
    max_output_tokens: int = 1024

    @classmethod
    def from_options(cls, opts: dict) -> "LLMTarget":
        return cls(
            base_url=(opts.get("llm_base_url") or "").rstrip("/"),
            model_name=opts.get("llm_model_name", ""),
            api_key=opts.get("llm_api_key", ""),
            provider_id=opts.get("llm_provider", ""),
            max_output_tokens=int(opts.get("llm_max_output_tokens") or 1024),
        )


def _headers(api_key: str, provider_id: str = "") -> dict:
    """Auth plus whatever else the provider wants (OpenRouter's attribution)."""
    return build_headers(provider_id, api_key)


def _stream_completion(
    target: LLMTarget, prompt: str, on_delta=None, should_cancel=None,
) -> tuple[str, bool]:
    """Stream one completion. Returns (text, cancelled)."""
    payload = {
        "model": target.model_name,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": target.max_output_tokens,
    }
    collected: list[str] = []

    with _req.post(
        chat_url(target.base_url),
        json=payload,
        headers=_headers(target.api_key, target.provider_id),
        stream=True,
        timeout=120,
    ) as resp:
        resp.raise_for_status()
        for raw_line in resp.iter_lines():
            if should_cancel is not None and should_cancel():
                return "".join(collected), True
            if not raw_line:
                continue
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if line.startswith("data: "):
                line = line[6:]
            if line.strip() == "[DONE]":
                break
            try:
                chunk = _json.loads(line)
            except JSONDecodeError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if delta:
                collected.append(delta)
                if on_delta is not None:
                    on_delta(delta, "".join(collected))

    return "".join(collected), False


def _blocking_completion(target: LLMTarget, prompt: str) -> str:
    """Non-streaming request, used when the server does not support SSE."""
    payload = {
        "model": target.model_name,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": target.max_output_tokens,
    }
    resp = _req.post(
        chat_url(target.base_url),
        json=payload,
        headers=_headers(target.api_key, target.provider_id),
        timeout=600,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response from LLM endpoint: {data!r}") from exc


def run_completion(
    target: LLMTarget, prompt: str, on_delta=None, should_cancel=None,
) -> tuple[str, bool]:
    """Stream a completion, falling back to a plain request when streaming fails.

    Not every OpenAI-compatible server implements SSE (some proxies buffer it
    away entirely, returning a single JSON body). Previously that surfaced as an
    empty analysis with no error; now it retries without streaming.
    """
    try:
        text, cancelled = _stream_completion(target, prompt, on_delta, should_cancel)
        if cancelled or text.strip():
            return text, cancelled
    except _req.RequestException as exc:
        # A connection-level failure is worth one non-streaming retry; an HTTP
        # error status (bad model name, auth) will fail again the same way.
        if isinstance(exc, _req.HTTPError):
            raise
    return _blocking_completion(target, prompt), False


# ---------------------------------------------------------------------------
# Job entrypoint
# ---------------------------------------------------------------------------


def _persist(analysis_id: str, *, text: str | None = None, status: str | None = None,
             prompt: str | None = None) -> None:
    with new_session() as session:
        row = session.get(Analysis, analysis_id)
        if not row:
            return
        if text is not None:
            row.result_text = text
        if status is not None:
            row.status = status
        if prompt is not None:
            row.prompt_used = prompt
        session.add(row)
        session.commit()


def process_analysis_job(job_id: str) -> None:
    """Run the analysis job and stream progress to the SSE queue."""
    job = state.jobs[job_id]
    opts = job["options"]
    analysis_id = job["analysis_id"]
    analysis_type = opts["analysis_type"]

    def _cancelled() -> bool:
        flag = job.get("cancel_flag")
        return bool(flag and flag.is_set())

    try:
        append_job_log(job_id, "INFO", f"Analysis worker started (type={analysis_type})")
        push_event(job_id, "running", 0.05, "Building prompt...")

        target = LLMTarget.from_options(opts)
        provider = get_provider(target.provider_id)
        if provider.cloud and not opts.get("llm_allow_cloud"):
            raise LLMError(
                f"{provider.label} is a hosted service and sending this transcript "
                "there has not been allowed. Enable it in AI Analysis settings first."
            )

        context_tokens = int(opts.get("llm_context_tokens") or 8192)
        full_text = opts["transcript_full_text"]

        input_budget = max(
            512, context_tokens - target.max_output_tokens - _PROMPT_OVERHEAD_TOKENS
        )
        needed = estimate_tokens(full_text)

        if needed <= input_budget:
            _single_pass(job_id, analysis_id, opts, target, _cancelled)
        else:
            append_job_log(
                job_id,
                "INFO",
                f"Transcript is ~{needed} tokens, over the {input_budget}-token input budget; "
                "processing in chunks",
            )
            _map_reduce(job_id, analysis_id, opts, target, input_budget, _cancelled)
    except Exception as exc:
        handle_job_error(job_id, exc)
        try:
            _persist(analysis_id, status="error")
        except Exception as db_exc:
            append_job_log(job_id, "ERROR", f"Failed to persist analysis error state: {db_exc}")


def _finish_cancelled(job_id: str, analysis_id: str, partial: str) -> None:
    push_event(job_id, "cancelled", 0.0, "Cancelled by user.")
    append_job_log(job_id, "INFO", "Analysis job cancelled")
    _persist(analysis_id, text=partial, status="error")


def _single_pass(job_id, analysis_id, opts, target, cancelled) -> None:
    prompt = build_analysis_prompt(
        analysis_type=opts["analysis_type"],
        full_text=opts["transcript_full_text"],
        target_language=opts.get("target_language", ""),
        custom_prompt=opts.get("custom_prompt", ""),
        output_language=opts.get("output_language", ""),
    )
    _persist(analysis_id, prompt=prompt)
    push_event(job_id, "running", 0.10, "Connecting to LLM...")

    def _on_delta(delta: str, partial: str) -> None:
        push_event(
            job_id, "streaming", 0.5, "Generating...",
            data={"chunk": delta, "partial": partial},
        )

    text, was_cancelled = run_completion(
        target, prompt, on_delta=_on_delta, should_cancel=cancelled
    )
    if was_cancelled:
        _finish_cancelled(job_id, analysis_id, text)
        return

    _persist(analysis_id, text=text, status="done")
    push_event(
        job_id, "done", 1.0, "Analysis complete.",
        data={"result_text": text, "analysis_id": analysis_id},
    )
    append_job_log(job_id, "INFO", "Analysis job finished successfully")


def _map_reduce(job_id, analysis_id, opts, target, input_budget, cancelled) -> None:
    analysis_type = opts["analysis_type"]
    chunks = chunk_text(opts["transcript_full_text"], input_budget)
    total = len(chunks)
    # Translation keeps every chunk's output verbatim; the others get merged, so
    # their partials only need to be readable by the reduce step.
    concatenate = analysis_type == "translate"

    _persist(
        analysis_id,
        prompt=_build_map_prompt(
            analysis_type, "<chunked>", 1, total,
            target_language=opts.get("target_language", ""),
            custom_prompt=opts.get("custom_prompt", ""),
            output_language=opts.get("output_language", ""),
        ),
    )

    partials: list[str] = []
    map_span = 0.85 if concatenate else 0.65

    for index, chunk in enumerate(chunks, 1):
        if cancelled():
            _finish_cancelled(job_id, analysis_id, "\n\n".join(partials))
            return

        start = 0.05 + map_span * (index - 1) / total
        push_event(job_id, "running", start, f"Processing part {index} of {total}...")

        prompt = _build_map_prompt(
            analysis_type, chunk, index, total,
            target_language=opts.get("target_language", ""),
            custom_prompt=opts.get("custom_prompt", ""),
            output_language=opts.get("output_language", ""),
        )

        def _on_delta(delta: str, partial: str, _done="\n\n".join(partials)) -> None:
            combined = f"{_done}\n\n{partial}" if _done else partial
            push_event(
                job_id, "streaming", start, f"Processing part {index} of {total}...",
                data={"chunk": delta, "partial": combined},
            )

        text, was_cancelled = run_completion(
            target, prompt,
            on_delta=_on_delta if concatenate else None,
            should_cancel=cancelled,
        )
        if was_cancelled:
            _finish_cancelled(job_id, analysis_id, "\n\n".join(partials + [text]))
            return
        partials.append(text.strip())

    if concatenate:
        result = "\n\n".join(p for p in partials if p)
        _persist(analysis_id, text=result, status="done")
        push_event(
            job_id, "done", 1.0, "Analysis complete.",
            data={"result_text": result, "analysis_id": analysis_id},
        )
        append_job_log(job_id, "INFO", f"Analysis finished ({total} chunks, concatenated)")
        return

    if cancelled():
        _finish_cancelled(job_id, analysis_id, "\n\n".join(partials))
        return

    push_event(job_id, "running", 0.75, f"Merging {total} parts...")
    reduce_prompt = _build_reduce_prompt(
        analysis_type, partials,
        custom_prompt=opts.get("custom_prompt", ""),
        output_language=opts.get("output_language", ""),
    )

    # The merged partials can themselves overflow the window on very long
    # recordings; fold them down until they fit before the final pass.
    while estimate_tokens(reduce_prompt) > input_budget and len(partials) > 1:
        folded = []
        for group_start in range(0, len(partials), 2):
            group = partials[group_start:group_start + 2]
            if len(group) == 1:
                folded.append(group[0])
                continue
            text, was_cancelled = run_completion(
                target,
                _build_reduce_prompt(
                    analysis_type, group,
                    custom_prompt=opts.get("custom_prompt", ""),
                    output_language=opts.get("output_language", ""),
                ),
                should_cancel=cancelled,
            )
            if was_cancelled:
                _finish_cancelled(job_id, analysis_id, "\n\n".join(partials))
                return
            folded.append(text.strip())
        partials = folded
        reduce_prompt = _build_reduce_prompt(
            analysis_type, partials,
            custom_prompt=opts.get("custom_prompt", ""),
            output_language=opts.get("output_language", ""),
        )

    def _on_delta(delta: str, partial: str) -> None:
        push_event(
            job_id, "streaming", 0.85, "Merging parts...",
            data={"chunk": delta, "partial": partial},
        )

    result, was_cancelled = run_completion(
        target, reduce_prompt, on_delta=_on_delta, should_cancel=cancelled
    )
    if was_cancelled:
        _finish_cancelled(job_id, analysis_id, result)
        return

    _persist(analysis_id, text=result, status="done")
    push_event(
        job_id, "done", 1.0, "Analysis complete.",
        data={"result_text": result, "analysis_id": analysis_id},
    )
    append_job_log(job_id, "INFO", f"Analysis finished ({total} chunks, merged)")
