import json
import re
import time
from typing import Any, Type
from langchain_groq import ChatGroq
from pydantic import BaseModel
from app.core.config import settings
from app.core.sanitize import sanitize_text
from app.agents.tender_analyst.state import TenderAnalystState
from app.agents.tender_analyst.prompts import METADATA_EXTRACTION_PROMPT, REQUIREMENTS_EXTRACTION_PROMPT
from app.agents.tender_analyst.llm_schemas import (
    LLMMetadata,
    LLMRequirementList,
    LLMSimpleRequirementList,
    normalize_metadata,
    normalize_requirement_list,
)
from app.services.observability import observability

# NOTE: Do NOT append PydanticOutputParser.get_format_instructions() to prompts
# when using structured output. Those instructions tell the model to wrap output
# in ```json markdown fences, which makes Groq's strict JSON validator
# (json_mode) reject the response with 400 json_validate_failed.


def get_llm(max_tokens: int | None = None):
    if not settings.GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY is missing from configuration.")
    # Default 3000: Groq's on_demand tier allows 8000 tokens/min and
    # max_tokens counts against that budget — 8192 raised 413
    # "Request too large ... Limit 8000, Requested 8192". Truncation risk
    # from a smaller completion budget is handled by capping chunk input
    # (TENDER_ANALYST_MAX_CHUNK_CHARS) and asking for ≤15 requirements/chunk.
    return ChatGroq(
        model=settings.GROQ_MODEL,
        api_key=settings.GROQ_API_KEY,
        temperature=0.0,
        max_tokens=max_tokens or settings.GROQ_MAX_TOKENS,
        reasoning_effort=settings.GROQ_REASONING_EFFORT,
    )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated to fit token budget]..."


def _is_provider_error(exc: Exception) -> bool:
    """Detect Groq provider-side failures (tool_use_failed, json validation, 400s)."""
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(
        token in msg
        for token in (
            "json_validate_failed",
            "failed to validate json",
            "failed_generation",
            "tool_use_failed",
            "tool choice is required",
            "did not call a tool",
            "failed to parse tool call",
            "output_parsing_failure",
            "failed to parse",
            "rate_limit_exceeded",
            "rate limit",
            "request too large",
            "reduce your message size",
            "tokens per minute",
            "tpm",
            "badrequest",
            "too many requests",
            "400",
            "413",
            "429",
        )
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect TPM/rate-limit errors (413/429) that need backoff, not instant retry."""
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(
        token in msg
        for token in (
            "rate_limit_exceeded",
            "rate limit",
            "request too large",
            "reduce your message size",
            "tokens per minute",
            "too many requests",
            "413",
            "429",
        )
    )


def _backoff_sleep(attempt: int, operation: str) -> None:
    # Exponential backoff: 5s, 10s, 20s... capped at 60s. Immediate retries
    # against a TPM limit only extend the throttle window.
    delay = min(60.0, 5.0 * (2 ** (attempt - 1)))
    observability.log_event(
        "rate_limit_backoff", operation=operation, attempt=attempt, delay_seconds=delay
    )
    time.sleep(delay)


def _extract_json_object(text: str) -> Any:
    """Best-effort extraction of the first {...} JSON object from raw LLM text.

    Repairs the exact glitches seen in production failed_generation payloads:
    raw control bytes copied from PDF text, doubled commas (",,"), doubled
    colons ('"key"::'), and trailing commas. All repairs target sequences
    that are never valid JSON, so a failed repair just raises as before.
    """
    if not text:
        raise ValueError("Empty completion, nothing to parse.")
    cleaned = sanitize_text(re.sub(r"```(?:json)?", "", text)).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in completion: {text[:200]!r}")
    candidate = cleaned[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*,", ",", candidate)  # "a",,  ->  "a",
        repaired = re.sub(r'("[^"\n]*")\s*::', r"\1:", repaired)  # "k"::v  ->  "k":v
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)  # trailing commas
        return json.loads(repaired)


# Appended to the prompt on retry attempts: the most common json_mode failure
# is the model drifting into prose/YAML. A short reminder fixes many retries.
RETRY_NUDGE = (
    "\n\nReminder: reply with ONLY the JSON object and nothing else. "
    "Keep it short; omit any empty fields."
)


def _invoke_structured(
    schema: Type[BaseModel],
    prompt: str,
    operation: str,
    fallback_schemas: tuple[Type[BaseModel], ...] = (),
):
    """Invoke the LLM tolerantly.

    Chain (first success wins), each retried TENDER_ANALYST_MAX_RETRIES times:
      1. method="json_mode" with the primary schema — for requirements this is
         the FLAT scalar-only schema, the easiest shape for small models to
         emit as strictly-valid JSON.
      2. method="json_mode" with each fallback schema (richer nested shape —
         used when the model cooperates and returns detail).
      3. method="function_calling" with the primary schema.
      4. Raw fallback: plain invoke + manual JSON extraction, parsed against
         the primary schema first, then each fallback.

    Returns the lenient parsed object. Callers normalize to strict models.

    Rate-limit errors (413/429, TPM exhausted) get exponential backoff
    between attempts instead of instant retries.
    """
    llm = get_llm()
    max_retries = max(1, settings.TENDER_ANALYST_MAX_RETRIES)
    last_exc: Exception | None = None

    def _attempt_call(method: str, sch: Type[BaseModel], attempt: int, label: str):
        nonlocal last_exc
        try:
            structured_llm = llm.with_structured_output(sch, method=method)  # type: ignore[arg-type]
            # Nudge back to JSON-only on retries after a validation failure.
            attempt_prompt = prompt + RETRY_NUDGE if attempt > 1 else prompt
            return True, structured_llm.invoke(attempt_prompt)
        except Exception as e:  # noqa: BLE001 — must catch provider errors to retry/fallback
            last_exc = e
            observability.log_error(
                "structured_output_failed",
                e,
                operation=operation,
                method=label,
                attempt=attempt,
                is_provider_error=_is_provider_error(e),
            )
            if _is_rate_limit_error(e):
                _backoff_sleep(attempt, operation)
            return False, None

    for sch in (schema, *fallback_schemas):
        for attempt in range(1, max_retries + 1):
            ok, result = _attempt_call("json_mode", sch, attempt, f"json_mode:{sch.__name__}")
            if ok:
                return result

    for attempt in range(1, max_retries + 1):
        ok, result = _attempt_call("function_calling", schema, attempt, "function_calling")
        if ok:
            return result

    # Final fallback: unconstrained generation, parse JSON manually.
    for attempt in range(1, max_retries + 1):
        try:
            raw = llm.invoke(prompt + RETRY_NUDGE if attempt > 1 else prompt)
            text = getattr(raw, "content", raw)
            if isinstance(text, list):
                text = "".join(
                    getattr(b, "text", str(b)) for b in text  # type: ignore[attr-defined]
                )
            data = _extract_json_object(str(text))
            for sch in (schema, *fallback_schemas):
                try:
                    return sch(**data)
                except Exception:
                    continue
            # No schema accepted it — surface the primary schema's error.
            return schema(**data)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            observability.log_error(
                "structured_output_failed",
                e,
                operation=operation,
                method="raw_json_fallback",
                attempt=attempt,
                is_provider_error=_is_provider_error(e),
            )
            if _is_rate_limit_error(e):
                _backoff_sleep(attempt, operation)
            continue

    assert last_exc is not None
    raise last_exc


def extract_metadata_node(state: TenderAnalystState) -> TenderAnalystState:
    tender_id = state.get("tender_id", "unknown")
    observability.log_event("node_start", node="extract_metadata", tender_id=tender_id)

    if not state.get("parsed_pages"):
        state["analysis_status"] = "FAILED"
        state["error_details"] = "No pages to extract metadata from."
        observability.log_event("node_failed", node="extract_metadata", reason="No pages", tender_id=tender_id)
        return state

    content = "\n".join([p["text"] for p in state["parsed_pages"][:3]])  # First 3 pages for metadata usually
    content = _truncate(sanitize_text(content), settings.TENDER_ANALYST_MAX_CHUNK_CHARS)
    prompt = METADATA_EXTRACTION_PROMPT.format(content=content)

    try:
        result = _invoke_structured(LLMMetadata, prompt, operation="extract_metadata")
        state["metadata"] = normalize_metadata(result)
        observability.log_event("node_success", node="extract_metadata", tender_id=tender_id)
    except Exception as e:
        state["validation_errors"].append(f"Metadata extraction failed: {str(e)[:500]}")
        state["metadata"] = {}
        observability.log_error("node_error", e, node="extract_metadata", tender_id=tender_id)

    return state


def extract_requirements_by_chunk_node(state: TenderAnalystState) -> TenderAnalystState:
    tender_id = state.get("tender_id", "unknown")
    observability.log_event("node_start", node="extract_requirements", tender_id=tender_id)
    try:
        get_llm()
    except Exception as e:
        state["validation_errors"].append(str(e))
        observability.log_error("node_error", e, node="extract_requirements", tender_id=tender_id)
        return state

    all_requirements = []
    chunk_size = settings.TENDER_ANALYST_CHUNK_SIZE
    pages = state.get("parsed_pages", [])
    document_name = state.get("document_name", "document.pdf")

    for i in range(0, len(pages), chunk_size):
        chunk = pages[i:i+chunk_size]
        chunk_text = "\n\n".join([f"--- Page {p['page_number']} ---\n{p['text']}" for p in chunk if p["extraction_status"] == "SUCCESS"])
        if not chunk_text.strip():
            continue
        # Keep prompt + completion inside the Groq TPM budget (413 otherwise),
        # and strip control chars so the model can't copy them into JSON.
        chunk_text = _truncate(sanitize_text(chunk_text), settings.TENDER_ANALYST_MAX_CHUNK_CHARS)

        default_page = chunk[0].get("page_number", 1) if chunk else 1
        prompt = REQUIREMENTS_EXTRACTION_PROMPT.format(
            document_name=document_name,
            chunk_content=chunk_text
        )

        try:
            observability.log_event("processing_chunk", chunk_index=i, tender_id=tender_id)
            res = _invoke_structured(
                LLMSimpleRequirementList,
                prompt,
                operation=f"extract_requirements_chunk_{i}",
                fallback_schemas=(LLMRequirementList,),
            )
            normalized = normalize_requirement_list(res, document_name, default_page)
            if not normalized:
                state["validation_errors"].append(
                    f"Chunk {i} extraction returned no usable requirements."
                )
            all_requirements.extend(normalized)
        except Exception as e:
            state["validation_errors"].append(f"Chunk {i} extraction failed: {str(e)[:500]}")
            observability.log_error("chunk_error", e, chunk_index=i, tender_id=tender_id)

    state["requirements"] = all_requirements
    observability.log_event("node_success", node="extract_requirements", tender_id=tender_id, total_requirements=len(all_requirements))
    return state


def assemble_final_result_node(state: TenderAnalystState) -> TenderAnalystState:
    tender_id = state.get("tender_id", "unknown")
    observability.log_event("node_start", node="assemble_final", tender_id=tender_id)

    if state.get("analysis_status") != "FAILED":
        state["analysis_status"] = "SUCCESS" if not state.get("validation_errors") else "PARTIAL"

    unreadable = sum(1 for p in state.get("parsed_pages", []) if p["extraction_status"] == "NO_TEXT")

    final_result = {
        "tender_id": state["tender_id"],
        "analysis_status": state["analysis_status"],
        "metadata": state.get("metadata", {}),
        "requirements": state.get("requirements", []),
        "issues": state.get("issues", []),
        "total_pages": len(state.get("parsed_pages", [])),
        "processed_pages": len(state.get("parsed_pages", [])) - unreadable,
        "unreadable_pages": unreadable,
        "requirements_needing_review": sum(1 for r in state.get("requirements", []) if r.get("needs_review")),
        "warnings": state.get("warnings", []) + state.get("validation_errors", []),
        "analysis_version": "1.0"
    }

    state["final_result"] = final_result
    observability.log_event("node_success", node="assemble_final", tender_id=tender_id, status=state["analysis_status"])
    return state
