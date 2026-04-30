"""Provider-neutral LLM client gateway (§5.3 llm_client, §4 reliability).

Responsibilities:
  - Single entry point for all LLM calls (text, multimodal, embedding).
  - JSON-mode enforcement with pydantic validation and a repair loop.
  - Retry with exponential backoff on transient errors.
  - Cost / token accounting written to the llm_calls ledger.
  - Records prompt_name + prompt_version from the template's trace_tag().

Defers real network calls behind a thin adapter so tests can mock it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.prompts.base import PromptTemplate
from app.services.streaming_json import TopLevelStreamParser

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Any unrecoverable LLM error after retries and repair."""


class TransientLLMError(LLMError):
    """Network / rate-limit style error; retry-eligible."""


@dataclass
class LLMCallRecord:
    """Row written to `llm_calls` cost ledger."""

    task: str                 # prompt name
    prompt_version: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    status: str               # "ok" | "error" | "repaired"
    error: str | None = None


@dataclass
class PromptLogContext:
    phase_description: str = ""
    question_id: str | None = None
    solution_id: str | None = None
    conversation_id: str | None = None
    image_names: list[str] | None = None
    related: dict[str, Any] | None = None


@dataclass
class PromptLogRecord:
    timestamp: str
    call_id: str
    attempt: int
    request_kind: str
    task: str
    prompt_version: str
    model: str
    phase_description: str
    question_id: str | None
    solution_id: str | None
    conversation_id: str | None
    image_names: list[str]
    related: dict[str, Any]
    messages: list[dict[str, Any]] | None = None
    texts: list[str] | None = None
    task_type: str | None = None
    response_schema: dict[str, Any] | None = None
    response_preview: str | None = None
    response_content: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    status: str = "ok"
    error: str | None = None


@dataclass
class ResponseLogRecord:
    timestamp: str
    call_id: str
    attempt: int
    request_kind: str
    task: str
    prompt_version: str
    model: str
    phase_description: str
    question_id: str | None
    solution_id: str | None
    conversation_id: str | None
    image_names: list[str]
    related: dict[str, Any]
    response_content: str
    response_preview: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    status: str = "ok"
    error: str | None = None


@dataclass
class StreamChunk:
    """One delta yielded by ``generate_json_stream_iter``.

    ``text`` is the new text since the previous chunk (may be empty if
    this chunk only carries usage metadata). ``prompt_tokens`` /
    ``completion_tokens`` are the latest cumulative counts and may be
    zero until the final chunk.
    """

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class CostLedger(Protocol):
    """DI seam for persistence. Production impl writes to PG."""

    async def record(self, rec: LLMCallRecord) -> None: ...


class PromptLogger(Protocol):
    async def record_prompt(self, rec: PromptLogRecord) -> None: ...


class NoopLedger:
    async def record(self, rec: LLMCallRecord) -> None:
        log.info("llm_call %s", rec)


class NoopPromptLogger:
    async def record_prompt(self, rec: PromptLogRecord) -> None:
        log.debug("llm_prompt %s", rec)


class JsonlPromptLogger:
    def __init__(self, path: str | None = None, response_path: str | None = None) -> None:
        self.path = Path(path or settings.storage.llm_prompt_log_file)
        self.response_path = Path(
            response_path
            or (
                str(self.path.with_name("llm_responses.jsonl"))
                if path is not None and response_path is None
                else settings.storage.llm_response_log_file
            )
        )

    async def record_prompt(self, rec: PromptLogRecord) -> None:
        try:
            prompt_payload = self._prompt_payload(rec)
            response_payload = self._response_payload(rec)
            await asyncio.to_thread(self._append_records, prompt_payload, response_payload)
        except Exception as e:  # noqa: BLE001
            log.warning("prompt log write failed: %s", e)

    def _append_records(self, prompt_payload: str, response_payload: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(prompt_payload)
        if response_payload is not None:
            self.response_path.parent.mkdir(parents=True, exist_ok=True)
            with self.response_path.open("a", encoding="utf-8") as f:
                f.write(response_payload)

    @staticmethod
    def _prompt_payload(rec: PromptLogRecord) -> str:
        payload = {
            **rec.__dict__,
            "response_content": None,
        }
        return json.dumps(payload, ensure_ascii=False) + "\n"

    @staticmethod
    def _response_payload(rec: PromptLogRecord) -> str | None:
        if rec.response_content is None:
            return None
        payload = ResponseLogRecord(
            timestamp=rec.timestamp,
            call_id=rec.call_id,
            attempt=rec.attempt,
            request_kind=rec.request_kind,
            task=rec.task,
            prompt_version=rec.prompt_version,
            model=rec.model,
            phase_description=rec.phase_description,
            question_id=rec.question_id,
            solution_id=rec.solution_id,
            conversation_id=rec.conversation_id,
            image_names=rec.image_names,
            related=rec.related,
            response_content=rec.response_content,
            response_preview=rec.response_preview,
            prompt_tokens=rec.prompt_tokens,
            completion_tokens=rec.completion_tokens,
            latency_ms=rec.latency_ms,
            status=rec.status,
            error=rec.error,
        )
        return json.dumps(payload.__dict__, ensure_ascii=False) + "\n"


def _truncate_text(value: str, limit: int = 8000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _sanitize_messages(
    messages: list[dict[str, Any]],
    *,
    image_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    image_queue = list(image_names or [])
    image_count = 0
    sanitized: list[dict[str, Any]] = []
    for message in messages:
        row: dict[str, Any] = {"role": str(message.get("role") or "")}
        if "parts" in message:
            parts_out: list[dict[str, Any]] = []
            for part in list(message.get("parts") or []):
                if "text" in part:
                    parts_out.append({"text": _truncate_text(str(part.get("text") or ""))})
                    continue
                if "inline_data" in part:
                    inline = dict(part.get("inline_data") or {})
                    image_count += 1
                    image_name = image_queue.pop(0) if image_queue else f"image_{image_count}"
                    parts_out.append({
                        "inline_data": {
                            "mime_type": inline.get("mime_type"),
                            "image_name": image_name,
                        }
                    })
                    continue
                parts_out.append({"unsupported_part": str(sorted(part.keys()))})
            row["parts"] = parts_out
        elif "content" in message:
            row["content"] = _truncate_text(str(message.get("content") or ""))
        sanitized.append(row)
    return sanitized


# ── Low-level transport adapter ────────────────────────────────────

class LLMTransport(Protocol):
    """Minimal interface; concrete impls use provider SDKs."""

    async def generate_json(
        self,
        *,
        model: str,
        messages: list[dict],
        response_schema: dict,
        timeout_s: int,
    ) -> tuple[str, int, int]:
        """Return (raw_text, prompt_tokens, completion_tokens)."""
        ...

    async def generate_text(
        self,
        *,
        model: str,
        messages: list[dict],
        timeout_s: int,
    ) -> tuple[str, int, int]:
        """Return raw text plus token counts for non-JSON prompts."""
        ...

    async def generate_json_stream(
        self,
        *,
        model: str,
        messages: list[dict],
        response_schema: dict,
        timeout_s: int,
    ) -> tuple[str, int, int]:
        """Return streamed partial JSON concatenated into one final JSON string."""
        ...

    def generate_json_stream_iter(
        self,
        *,
        model: str,
        messages: list[dict],
        response_schema: dict,
        timeout_s: int,
    ) -> AsyncIterator[StreamChunk]:
        """Yield raw text deltas as they arrive from the LLM.

        The final chunk's ``usage`` carries the prompt/completion token
        counts. Implementations should accumulate the partial text
        themselves if they also need a final string for fallback.
        """
        ...

    async def embed(
        self,
        *,
        model: str,
        texts: list[str],
        task_type: str | None = None,
    ) -> list[list[float]]: ...


class FakeTransport:
    """Test double — returns canned JSON for schema round-trips."""

    def __init__(
        self,
        json_by_model: dict[str, str] | None = None,
        text_by_model: dict[str, str] | None = None,
    ) -> None:
        self.json_by_model = json_by_model or {}
        self.text_by_model = text_by_model or {}
        self.calls: list[dict] = []

    async def generate_json(
        self, *, model, messages, response_schema, timeout_s,
    ) -> tuple[str, int, int]:
        self.calls.append({"model": model, "messages": messages})
        raw = self.json_by_model.get(model)
        if raw is None and len(self.json_by_model) == 1:
            raw = next(iter(self.json_by_model.values()))
        raw = raw if raw is not None else "{}"
        return raw, 0, 0

    async def generate_text(
        self, *, model, messages, timeout_s,
    ) -> tuple[str, int, int]:
        self.calls.append({"model": model, "messages": messages, "text": True})
        raw = self.text_by_model.get(model)
        if raw is None and len(self.text_by_model) == 1:
            raw = next(iter(self.text_by_model.values()))
        raw = raw if raw is not None else ""
        return raw, 0, 0

    async def generate_json_stream(
        self, *, model, messages, response_schema, timeout_s,
    ) -> tuple[str, int, int]:
        self.calls.append({"model": model, "messages": messages, "stream": True})
        raw = self.json_by_model.get(model)
        if raw is None and len(self.json_by_model) == 1:
            raw = next(iter(self.json_by_model.values()))
        raw = raw if raw is not None else "{}"
        return raw, 0, 0

    async def generate_json_stream_iter(
        self, *, model, messages, response_schema, timeout_s,
    ):
        """Test double: yield the canned JSON in two halves to simulate
        a streaming response."""
        self.calls.append(
            {"model": model, "messages": messages, "stream": True, "iter": True}
        )
        raw = self.json_by_model.get(model)
        if raw is None and len(self.json_by_model) == 1:
            raw = next(iter(self.json_by_model.values()))
        raw = raw if raw is not None else "{}"
        mid = max(1, len(raw) // 2)
        yield StreamChunk(text=raw[:mid], prompt_tokens=0, completion_tokens=0)
        yield StreamChunk(text=raw[mid:], prompt_tokens=0, completion_tokens=0)

    async def embed(self, *, model, texts, task_type=None) -> list[list[float]]:
        return [[0.0] * settings.retrieval_dense_dim for _ in texts]


# ── Cost estimation ────────────────────────────────────────────────

# Tunable USD per 1K tokens; override per-model if pricing changes.
_COST_PER_1K: dict[str, tuple[float, float]] = {
    # model: (input_usd_per_1k, output_usd_per_1k)
    "gemini-2.0-flash": (0.000075, 0.00030),
    "gemini-3.1-pro-preview": (0.00125, 0.0050),
    "text-embedding-004": (0.0000125, 0.0),
    "gemini-embedding-001": (0.0000125, 0.0),
    "gemini-embedding-2-preview": (0.0000125, 0.0),
}


def _estimate_cost_usd(model: str, in_tok: int, out_tok: int) -> float:
    in_rate, out_rate = _COST_PER_1K.get(model, (0.0, 0.0))
    return (in_tok / 1000) * in_rate + (out_tok / 1000) * out_rate


def _log_before_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    sleep_s = retry_state.next_action.sleep if retry_state.next_action else 0.0
    total_attempts = settings.llm.max_retries + 1
    log.warning(
        "LLM transient error on attempt %d/%d; retrying in %.1fs: %s",
        retry_state.attempt_number,
        total_attempts,
        sleep_s,
        exc,
    )


def _resolve_literal_at(model_cls: type[BaseModel], loc: tuple) -> list | None:
    """Walk a Pydantic model along `loc` and return Literal values if any.

    `loc` follows ``ValidationError.errors()[i]["loc"]`` conventions:
    field names interleaved with list indices. Returns ``None`` when the
    target is not a Literal field.
    """
    from typing import Literal as _Literal
    from typing import Union as _Union
    from typing import get_args as _get_args
    from typing import get_origin as _get_origin

    def _unwrap(tp):
        origin = _get_origin(tp)
        if origin is _Union:
            non_none = [a for a in _get_args(tp) if a is not type(None)]
            if len(non_none) == 1:
                return _unwrap(non_none[0])
            return tp
        if origin in (list, set, tuple):
            args = _get_args(tp)
            if args:
                return _unwrap(args[0])
        if origin is dict:
            args = _get_args(tp)
            if len(args) == 2:
                return _unwrap(args[1])
        return tp

    current: Any = model_cls
    for part in loc:
        if isinstance(part, int):
            continue
        if not (isinstance(current, type) and issubclass(current, BaseModel)):
            return None
        field = current.model_fields.get(str(part))
        if field is None:
            return None
        current = _unwrap(field.annotation)

    if _get_origin(current) is _Literal:
        return list(_get_args(current))
    return None


def _format_repair_instructions(
    model_cls: type[BaseModel] | None,
    err: ValidationError,
    *,
    max_items: int = 30,
) -> str:
    """Convert a ValidationError into a focused fix-list for the LLM.

    Generic ``str(err)`` dumps are noisy and the model often re-emits the
    same drift on the second attempt. A structured per-error recipe keeps
    the repair turn small and actionable.
    """
    lines: list[str] = []
    seen: set[tuple] = set()
    for entry in err.errors():
        loc = tuple(entry.get("loc") or ())
        kind = entry.get("type", "")
        msg = entry.get("msg", "")
        key = (loc, kind, msg)
        if key in seen:
            continue
        seen.add(key)

        path = ".".join(str(p) for p in loc) if loc else "(root)"
        if kind == "literal_error" and model_cls is not None:
            allowed = _resolve_literal_at(model_cls, loc)
            input_value = entry.get("input")
            if allowed:
                lines.append(
                    f"- `{path}` must be exactly one of: "
                    f"{' | '.join(str(v) for v in allowed)}"
                    + (f" (you wrote: {input_value!r})" if input_value is not None else "")
                )
                continue
        if kind == "missing":
            lines.append(f"- `{path}` is required and must be provided.")
            continue
        if kind.startswith("value_error"):
            lines.append(f"- `{path}`: {msg}")
            continue
        if kind in {"int_parsing", "float_parsing", "bool_parsing", "string_type"}:
            lines.append(f"- `{path}`: {msg}")
            continue
        # Fallback: include the path + Pydantic message verbatim.
        lines.append(f"- `{path}` ({kind}): {msg}")

        if len(lines) >= max_items:
            lines.append("- ...and more; fix every error in the list above.")
            break

    if not lines:
        lines.append(f"- {err}")
    return "\n".join(lines)


# ── Gateway ────────────────────────────────────────────────────────


class LLMClient:
    def __init__(
        self,
        transport: LLMTransport,
        ledger: CostLedger | None = None,
        prompt_logger: PromptLogger | None = None,
    ) -> None:
        self.transport = transport
        self.ledger = ledger or NoopLedger()
        self.prompt_logger = prompt_logger or NoopPromptLogger()

    async def _record_prompt(
        self,
        *,
        call_id: str,
        attempt: int,
        request_kind: str,
        trace: dict[str, str],
        model: str,
        context: PromptLogContext | None,
        messages: list[dict[str, Any]] | None = None,
        texts: list[str] | None = None,
        task_type: str | None = None,
        response_schema: dict[str, Any] | None = None,
        response_preview: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int | None = None,
        status: str = "ok",
        error: str | None = None,
    ) -> None:
        ctx = context or PromptLogContext()
        await self.prompt_logger.record_prompt(
            PromptLogRecord(
                timestamp=datetime.now(tz=UTC).isoformat(),
                call_id=call_id,
                attempt=attempt,
                request_kind=request_kind,
                task=trace["prompt_name"],
                prompt_version=trace["prompt_version"],
                model=model,
                phase_description=ctx.phase_description,
                question_id=str(ctx.question_id) if ctx.question_id else None,
                solution_id=str(ctx.solution_id) if ctx.solution_id else None,
                conversation_id=str(ctx.conversation_id) if ctx.conversation_id else None,
                image_names=list(ctx.image_names or []),
                related=dict(ctx.related or {}),
                messages=_sanitize_messages(messages, image_names=ctx.image_names) if messages is not None else None,
                texts=[_truncate_text(str(text or "")) for text in texts] if texts is not None else None,
                task_type=task_type,
                response_schema=response_schema,
                response_preview=_truncate_text(response_preview or "", limit=4000) if response_preview else None,
                response_content=response_preview or None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                status=status,
                error=_truncate_text(error, limit=2000) if error else None,
            )
        )

    @retry(
        stop=stop_after_attempt(settings.llm.max_retries + 1),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(TransientLLMError),
        before_sleep=_log_before_retry,
        reraise=True,
    )
    async def _raw_call(
        self,
        *,
        model: str,
        messages: list[dict],
        response_schema: dict,
        timeout_s: int,
        stream: bool,
    ) -> tuple[str, int, int]:
        try:
            if stream:
                return await self.transport.generate_json_stream(
                    model=model,
                    messages=messages,
                    response_schema=response_schema,
                    timeout_s=timeout_s,
                )
            return await self.transport.generate_json(
                model=model,
                messages=messages,
                response_schema=response_schema,
                timeout_s=timeout_s,
            )
        except TimeoutError as e:
            raise TransientLLMError(f"timeout after {timeout_s}s: {e}") from e

    @retry(
        stop=stop_after_attempt(settings.llm.max_retries + 1),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(TransientLLMError),
        before_sleep=_log_before_retry,
        reraise=True,
    )
    async def _raw_text_call(
        self,
        *,
        model: str,
        messages: list[dict],
        timeout_s: int,
    ) -> tuple[str, int, int]:
        try:
            return await self.transport.generate_text(
                model=model,
                messages=messages,
                timeout_s=timeout_s,
            )
        except TimeoutError as e:
            raise TransientLLMError(f"timeout after {timeout_s}s: {e}") from e

    async def call_structured(
        self,
        *,
        template: PromptTemplate,
        model: str,
        model_cls: type[T],
        template_kwargs: dict[str, Any] | None = None,
        messages_override: list[dict] | None = None,
        prompt_context: PromptLogContext | None = None,
        timeout_s: int | None = None,
        stream: bool = False,
        stream_recovery_timeout_s: int | None = None,
        min_repair_attempts: int = 0,
        disable_repair: bool = False,
    ) -> T:
        """Call the LLM and validate output against a pydantic model.

        If validation fails, run a repair loop (§4 reliability):
        re-prompt the LLM with the validator error + offending JSON.
        """
        tk = template_kwargs or {}
        messages = messages_override or template.build(**tk)
        trace = template.trace_tag()
        resolved_timeout_s = timeout_s or settings.llm.request_timeout_s
        call_id = str(uuid4())
        max_repair_attempts = (
            0 if disable_repair else max(settings.llm.max_repair_attempts, min_repair_attempts)
        )

        attempt = 0
        last_err: str | None = None
        raw_json = ""
        t0 = time.perf_counter()
        ptok = ctok = 0
        use_stream = stream

        while attempt <= max_repair_attempts:
            try:
                raw_json, ptok, ctok = await self._raw_call(
                    model=model,
                    messages=messages,
                    response_schema=template.schema,
                    timeout_s=resolved_timeout_s,
                    stream=use_stream,
                )
            except TransientLLMError:
                if not use_stream:
                    latency = int((time.perf_counter() - t0) * 1000)
                    await self._record_prompt(
                        call_id=call_id,
                        attempt=attempt,
                        request_kind="structured_stream" if use_stream else "structured",
                        trace=trace,
                        model=model,
                        context=prompt_context,
                        messages=messages,
                        response_schema=template.schema,
                        latency_ms=latency,
                        status="error",
                        error=(
                            "transient_stream_failed"
                            if use_stream else "transient_call_failed"
                        ),
                    )
                    raise
                recovery_timeout_s = max(
                    resolved_timeout_s,
                    stream_recovery_timeout_s or settings.llm.stream_recovery_timeout_s,
                )
                log.warning(
                    "streaming structured call failed; retrying once via non-stream "
                    "path (stream_timeout=%ss, recovery_timeout=%ss)",
                    resolved_timeout_s,
                    recovery_timeout_s,
                )
                raw_json, ptok, ctok = await self._raw_call(
                    model=model,
                    messages=messages,
                    response_schema=template.schema,
                    timeout_s=recovery_timeout_s,
                    stream=False,
                )
                use_stream = False
            try:
                parsed = model_cls.model_validate_json(raw_json)
                latency = int((time.perf_counter() - t0) * 1000)
                await self._record_prompt(
                    call_id=call_id,
                    attempt=attempt,
                    request_kind="structured_stream" if stream and attempt == 0 else "structured",
                    trace=trace,
                    model=model,
                    context=prompt_context,
                    messages=messages,
                    response_schema=template.schema,
                    response_preview=raw_json,
                    prompt_tokens=ptok,
                    completion_tokens=ctok,
                    latency_ms=latency,
                    status="repaired" if attempt > 0 else "ok",
                )
                await self.ledger.record(
                    LLMCallRecord(
                        task=trace["prompt_name"],
                        prompt_version=trace["prompt_version"],
                        model=model,
                        prompt_tokens=ptok,
                        completion_tokens=ctok,
                        cost_usd=_estimate_cost_usd(model, ptok, ctok),
                        latency_ms=latency,
                        status="repaired" if attempt > 0 else "ok",
                    )
                )
                return parsed
            except ValidationError as e:
                last_err = str(e)
                latency = int((time.perf_counter() - t0) * 1000)
                await self._record_prompt(
                    call_id=call_id,
                    attempt=attempt,
                    request_kind="structured_stream" if stream and attempt == 0 else "structured",
                    trace=trace,
                    model=model,
                    context=prompt_context,
                    messages=messages,
                    response_schema=template.schema,
                    response_preview=raw_json,
                    prompt_tokens=ptok,
                    completion_tokens=ctok,
                    latency_ms=latency,
                    status="validation_error",
                    error=last_err,
                )
                log.warning(
                    "LLM output validation failed for prompt '%s' (attempt %d/%d): %s",
                    trace.get("prompt_name", "unknown"),
                    attempt,
                    max_repair_attempts,
                    last_err,
                )
                extra_repair_hint = ""
                if (
                    trace.get("prompt_name") == "vizplanner"
                    and "unknown shared symbols" in last_err
                ):
                    extra_repair_hint = (
                        "\n\nAdditional repair requirements:\n"
                        "- Each symbol_map entry may declare only one symbol.\n"
                        "- Do not combine symbols such as `P, Q` or `P'/Q'` in one entry.\n"
                        "- Every symbol used in item.shared_symbols must be declared individually in symbol_map.\n"
                        "- If an item uses symbols such as `r`, `r'`, `P'`, or `Q'`, add each one separately to symbol_map."
                    )
                fix_list = _format_repair_instructions(model_cls, e)
                # Build repair message: append validator error + bad JSON.
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw_json},
                    {
                        "role": "user",
                        "content": (
                            "The previous output did not satisfy the schema. Apply EXACTLY the fixes below and re-emit the FULL JSON (every required field, no omissions, no markdown).\n\n"
                            "Fixes required:\n"
                            f"{fix_list}\n\n"
                            "Notes:\n"
                            "- For literal fields, use the exact token shown; never paraphrase, translate, or invent variants.\n"
                            "- Re-check every cross-field rule listed in the system message before responding.\n"
                            "- Do not add explanatory prose outside the JSON."
                            f"{extra_repair_hint}"
                        ),
                    },
                ]
                attempt += 1
                use_stream = False

        latency = int((time.perf_counter() - t0) * 1000)
        await self._record_prompt(
            call_id=call_id,
            attempt=attempt,
            request_kind="structured",
            trace=trace,
            model=model,
            context=prompt_context,
            messages=messages,
            response_schema=template.schema,
            response_preview=raw_json,
            prompt_tokens=ptok,
            completion_tokens=ctok,
            latency_ms=latency,
            status="error",
            error=last_err,
        )
        await self.ledger.record(
            LLMCallRecord(
                task=trace["prompt_name"],
                prompt_version=trace["prompt_version"],
                model=model,
                prompt_tokens=ptok,
                completion_tokens=ctok,
                cost_usd=_estimate_cost_usd(model, ptok, ctok),
                latency_ms=latency,
                status="error",
                error=last_err,
            )
        )
        raise LLMError(
            f"LLM output failed validation after "
            f"{max_repair_attempts} repair attempts: {last_err}"
        )

    async def call_text(
        self,
        *,
        template: PromptTemplate,
        model: str,
        template_kwargs: dict[str, Any] | None = None,
        messages_override: list[dict] | None = None,
        prompt_context: PromptLogContext | None = None,
        timeout_s: int | None = None,
    ) -> str:
        tk = template_kwargs or {}
        messages = messages_override or template.build(**tk)
        trace = template.trace_tag()
        resolved_timeout_s = timeout_s or settings.llm.request_timeout_s
        call_id = str(uuid4())
        t0 = time.perf_counter()

        try:
            raw_text, ptok, ctok = await self._raw_text_call(
                model=model,
                messages=messages,
                timeout_s=resolved_timeout_s,
            )
        except Exception as e:
            latency = int((time.perf_counter() - t0) * 1000)
            await self._record_prompt(
                call_id=call_id,
                attempt=0,
                request_kind="text",
                trace=trace,
                model=model,
                context=prompt_context,
                messages=messages,
                latency_ms=latency,
                status="error",
                error=str(e),
            )
            if isinstance(e, LLMError):
                raise
            raise LLMError(str(e)) from e

        latency = int((time.perf_counter() - t0) * 1000)
        await self._record_prompt(
            call_id=call_id,
            attempt=0,
            request_kind="text",
            trace=trace,
            model=model,
            context=prompt_context,
            messages=messages,
            response_preview=raw_text,
            prompt_tokens=ptok,
            completion_tokens=ctok,
            latency_ms=latency,
            status="ok",
        )
        await self.ledger.record(
            LLMCallRecord(
                task=trace["prompt_name"],
                prompt_version=trace["prompt_version"],
                model=model,
                prompt_tokens=ptok,
                completion_tokens=ctok,
                cost_usd=_estimate_cost_usd(model, ptok, ctok),
                latency_ms=latency,
                status="ok",
            )
        )
        return raw_text

    async def call_structured_streaming(
        self,
        *,
        template: PromptTemplate,
        model: str,
        model_cls: type[T],
        template_kwargs: dict[str, Any] | None = None,
        messages_override: list[dict] | None = None,
        prompt_context: PromptLogContext | None = None,
        timeout_s: int | None = None,
        stream_recovery_timeout_s: int | None = None,
    ) -> AsyncIterator[tuple[str, object] | T]:
        """Stream-parse the LLM JSON, yielding ``(key, value)`` tuples
        as each top-level field completes, then a final validated
        ``model_cls`` instance.

        On streaming-parse failure (malformed JSON or pydantic
        validation), falls back to ``call_structured`` for one bulk
        retry+repair cycle. The final yielded item is always either a
        validated model instance or this method raises ``LLMError``.
        """
        tk = template_kwargs or {}
        messages = messages_override or template.build(**tk)
        trace = template.trace_tag()
        resolved_timeout_s = timeout_s or settings.llm.request_timeout_s
        call_id = str(uuid4())

        # If the transport doesn't expose a true iterator, fall back to
        # bulk path so callers still get a validated model out.
        iter_fn = getattr(self.transport, "generate_json_stream_iter", None)
        if iter_fn is None:
            parsed = await self.call_structured(
                template=template,
                model=model,
                model_cls=model_cls,
                template_kwargs=template_kwargs,
                messages_override=messages_override,
                prompt_context=prompt_context,
                timeout_s=timeout_s,
                stream_recovery_timeout_s=stream_recovery_timeout_s,
                stream=True,
            )
            yield parsed
            return

        parser = TopLevelStreamParser()
        ptok = ctok = 0
        t0 = time.perf_counter()
        full_text_parts: list[str] = []
        stream_error: Exception | None = None
        first_chunk_ms: int | None = None
        chunk_count = 0

        try:
            async for chunk in iter_fn(
                model=model,
                messages=messages,
                response_schema=template.schema,
                timeout_s=resolved_timeout_s,
            ):
                if chunk.text:
                    chunk_count += 1
                    if first_chunk_ms is None:
                        first_chunk_ms = int((time.perf_counter() - t0) * 1000)
                        log.info(
                            "structured stream first chunk task=%s question_id=%s solution_id=%s model=%s first_chunk_ms=%s",
                            trace.get("prompt_name", "unknown"),
                            prompt_context.question_id if prompt_context else None,
                            prompt_context.solution_id if prompt_context else None,
                            model,
                            first_chunk_ms,
                        )
                    full_text_parts.append(chunk.text)
                    for pair in parser.feed(chunk.text):
                        yield pair
                if chunk.prompt_tokens:
                    ptok = chunk.prompt_tokens
                if chunk.completion_tokens:
                    ctok = chunk.completion_tokens
        except (TimeoutError, TransientLLMError) as e:
            stream_error = e

        if stream_error is not None:
            recovery_timeout_s = max(
                resolved_timeout_s,
                stream_recovery_timeout_s or settings.llm.stream_recovery_timeout_s,
            )
            log.warning(
                "streaming transport failed; falling back to bulk structured call "
                "(stream_timeout=%ss, recovery_timeout=%ss): %s",
                resolved_timeout_s,
                recovery_timeout_s,
                stream_error,
            )
            parsed = await self.call_structured(
                template=template,
                model=model,
                model_cls=model_cls,
                template_kwargs=template_kwargs,
                messages_override=messages_override,
                prompt_context=prompt_context,
                timeout_s=recovery_timeout_s,
                stream_recovery_timeout_s=stream_recovery_timeout_s,
                stream=False,
            )
            yield parsed
            return

        raw_json = "".join(full_text_parts)
        try:
            parsed = model_cls.model_validate_json(raw_json)
            latency = int((time.perf_counter() - t0) * 1000)
            log.info(
                "structured stream completed task=%s question_id=%s solution_id=%s model=%s chunk_count=%s first_chunk_ms=%s total_elapsed_ms=%s",
                trace.get("prompt_name", "unknown"),
                prompt_context.question_id if prompt_context else None,
                prompt_context.solution_id if prompt_context else None,
                model,
                chunk_count,
                first_chunk_ms,
                latency,
            )
            await self._record_prompt(
                call_id=call_id,
                attempt=0,
                request_kind="structured_stream",
                trace=trace,
                model=model,
                context=prompt_context,
                messages=messages,
                response_schema=template.schema,
                response_preview=raw_json,
                prompt_tokens=ptok,
                completion_tokens=ctok,
                latency_ms=latency,
                status="ok",
            )
            await self.ledger.record(
                LLMCallRecord(
                    task=trace["prompt_name"],
                    prompt_version=trace["prompt_version"],
                    model=model,
                    prompt_tokens=ptok,
                    completion_tokens=ctok,
                    cost_usd=_estimate_cost_usd(model, ptok, ctok),
                    latency_ms=latency,
                    status="ok",
                )
            )
            yield parsed
            return
        except ValidationError as e:
            log.warning(
                "streaming output failed validation; falling back to repair loop: %s",
                e,
            )
            if settings.llm.max_repair_attempts <= 0:
                raise LLMError(f"streaming output failed validation: {e}") from e
            # Fallback: hand the bad JSON to the repair loop. We pass
            # the prior assistant text so the repair prompt can correct
            # it instead of regenerating from scratch.
            repair_messages = [
                *messages,
                {"role": "assistant", "content": raw_json},
                {
                    "role": "user",
                    "content": (
                        "上一次输出不符合 JSON Schema, 请仅修正以下问题并重新输出"
                        "完整 JSON (不要省略任何字段):\n"
                        f"{e}\n\n"
                        "仍须严格遵循 Schema, 不要添加解释文字。"
                    ),
                },
            ]
            parsed = await self.call_structured(
                template=template,
                model=model,
                model_cls=model_cls,
                template_kwargs=template_kwargs,
                messages_override=repair_messages,
                prompt_context=prompt_context,
                timeout_s=timeout_s,
                stream_recovery_timeout_s=stream_recovery_timeout_s,
                stream=False,
            )
            yield parsed

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        task_type: str | None = None,
        prompt_context: PromptLogContext | None = None,
    ) -> list[list[float]]:
        m = model or (
            settings.embedding.model
            if settings.embedding.provider in {"openai", "gemini"}
            else settings.retrieval.bge_m3_model
        )
        t0 = time.perf_counter()
        try:
            vectors = await self.transport.embed(model=m, texts=texts, task_type=task_type)
        except Exception as e:
            latency = int((time.perf_counter() - t0) * 1000)
            await self._record_prompt(
                call_id=str(uuid4()),
                attempt=0,
                request_kind="embed",
                trace={"prompt_name": "embedding", "prompt_version": "n/a"},
                model=m,
                context=prompt_context,
                texts=texts,
                task_type=task_type,
                latency_ms=latency,
                status="error",
                error=str(e),
            )
            if isinstance(e, LLMError):
                raise
            raise LLMError(str(e)) from e
        latency = int((time.perf_counter() - t0) * 1000)
        await self._record_prompt(
            call_id=str(uuid4()),
            attempt=0,
            request_kind="embed",
            trace={"prompt_name": "embedding", "prompt_version": "n/a"},
            model=m,
            context=prompt_context,
            texts=texts,
            task_type=task_type,
            latency_ms=latency,
            status="ok",
        )
        return vectors


GeminiTransport = LLMTransport
GeminiClient = LLMClient
