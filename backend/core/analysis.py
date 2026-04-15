"""LLM analysis job processing."""
import json as _json
from json import JSONDecodeError

import requests as _req

import state
from core.job_helpers import _append_job_log, _handle_job_error, _push_event
from db import new_session
from models import Analysis


def _build_analysis_prompt(
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


def _process_analysis_job(job_id: str) -> None:
    """Run the analysis job and stream progress to the SSE queue."""
    job = state.jobs[job_id]
    opts = job["options"]
    analysis_id = job["analysis_id"]

    try:
        _append_job_log(job_id, "INFO", f"Analysis worker started (type={opts['analysis_type']})")
        _push_event(job_id, "running", 0.05, "Building prompt...")

        prompt = _build_analysis_prompt(
            analysis_type=opts["analysis_type"],
            full_text=opts["transcript_full_text"],
            target_language=opts.get("target_language", ""),
            custom_prompt=opts.get("custom_prompt", ""),
            output_language=opts.get("output_language", ""),
        )

        with new_session() as session:
            a = session.get(Analysis, analysis_id)
            if a:
                a.prompt_used = prompt
                session.add(a)
                session.commit()

        _push_event(job_id, "running", 0.10, "Connecting to LLM...")

        base_url = opts["llm_base_url"].rstrip("/")
        model_name = opts["llm_model_name"]
        api_key = opts.get("llm_api_key", "")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }

        collected: list[str] = []

        with _req.post(
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers=headers,
            stream=True,
            timeout=120,
        ) as resp:
            resp.raise_for_status()

            for raw_line in resp.iter_lines():
                if job["cancel_flag"].is_set():
                    _push_event(job_id, "cancelled", 0.0, "Cancelled by user.")
                    _append_job_log(job_id, "INFO", "Analysis job cancelled")
                    with new_session() as session:
                        a = session.get(Analysis, analysis_id)
                        if a:
                            a.status = "error"
                            a.result_text = "".join(collected)
                            session.add(a)
                            session.commit()
                    return

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
                    _push_event(
                        job_id,
                        "streaming",
                        0.5,
                        "Generating...",
                        data={"chunk": delta, "partial": "".join(collected)},
                    )

        full_result = "".join(collected)

        with new_session() as session:
            a = session.get(Analysis, analysis_id)
            if a:
                a.result_text = full_result
                a.status = "done"
                session.add(a)
                session.commit()

        _push_event(
            job_id,
            "done",
            1.0,
            "Analysis complete.",
            data={"result_text": full_result, "analysis_id": analysis_id},
        )
        _append_job_log(job_id, "INFO", "Analysis job finished successfully")
    except Exception as exc:
        _handle_job_error(job_id, exc)
        try:
            with new_session() as session:
                a = session.get(Analysis, analysis_id)
                if a:
                    a.status = "error"
                    session.add(a)
                    session.commit()
        except Exception as db_exc:
            _append_job_log(job_id, "ERROR", f"Failed to persist analysis error state: {db_exc}")
