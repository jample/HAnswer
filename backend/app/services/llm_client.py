"""Gemini client gateway (§5.3 llm_client, §4 reliability).

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
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.prompts.base import PromptTemplate

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


class CostLedger(Protocol):
    """DI seam for persistence. Production impl writes to PG."""

    async def record(self, rec: LLMCallRecord) -> None: ...


class NoopLedger:
    async def record(self, rec: LLMCallRecord) -> None:
        log.info("llm_call %s", rec)


# ── Low-level transport adapter ────────────────────────────────────

class GeminiTransport(Protocol):
    """Minimal interface; concrete impl uses google-genai SDK."""

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

    async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]: ...


class FakeTransport:
    """Test double — returns canned JSON for schema round-trips."""

    def __init__(self, json_by_model: dict[str, str] | None = None) -> None:
        self.json_by_model = json_by_model or {}
        self.calls: list[dict] = []

    async def generate_json(
        self, *, model, messages, response_schema, timeout_s,
    ) -> tuple[str, int, int]:
        self.calls.append({"model": model, "messages": messages})
        raw = self.json_by_model.get(model, "{}")
        return raw, 0, 0

    async def embed(self, *, model, texts) -> list[list[float]]:
        return [[0.0] * settings.gemini.embed_dim for _ in texts]


# ── Cost estimation ────────────────────────────────────────────────

# Tunable USD per 1K tokens; override per-model if pricing changes.
_COST_PER_1K: dict[str, tuple[float, float]] = {
    # model: (input_usd_per_1k, output_usd_per_1k)
    "gemini-2.0-flash": (0.000075, 0.00030),
    "text-embedding-004": (0.0000125, 0.0),
}


def _estimate_cost_usd(model: str, in_tok: int, out_tok: int) -> float:
    in_rate, out_rate = _COST_PER_1K.get(model, (0.0, 0.0))
    return (in_tok / 1000) * in_rate + (out_tok / 1000) * out_rate


# ── Gateway ────────────────────────────────────────────────────────


class GeminiClient:
    def __init__(
        self,
        transport: GeminiTransport,
        ledger: CostLedger | None = None,
    ) -> None:
        self.transport = transport
        self.ledger = ledger or NoopLedger()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        retry=retry_if_exception_type(TransientLLMError),
        reraise=True,
    )
    async def _raw_call(
        self,
        *,
        model: str,
        messages: list[dict],
        response_schema: dict,
    ) -> tuple[str, int, int]:
        try:
            return await self.transport.generate_json(
                model=model,
                messages=messages,
                response_schema=response_schema,
                timeout_s=settings.llm.request_timeout_s,
            )
        except asyncio.TimeoutError as e:
            raise TransientLLMError(f"timeout: {e}") from e

    async def call_structured(
        self,
        *,
        template: PromptTemplate,
        model: str,
        model_cls: type[T],
        template_kwargs: dict[str, Any] | None = None,
        messages_override: list[dict] | None = None,
    ) -> T:
        """Call the LLM and validate output against a pydantic model.

        If validation fails, run a repair loop (§4 reliability):
        re-prompt the LLM with the validator error + offending JSON.
        """
        tk = template_kwargs or {}
        messages = messages_override or template.build(**tk)
        trace = template.trace_tag()

        attempt = 0
        last_err: str | None = None
        raw_json = ""
        t0 = time.perf_counter()
        ptok = ctok = 0

        while attempt <= settings.llm.max_repair_attempts:
            raw_json, ptok, ctok = await self._raw_call(
                model=model,
                messages=messages,
                response_schema=template.schema,
            )
            try:
                parsed = model_cls.model_validate_json(raw_json)
                latency = int((time.perf_counter() - t0) * 1000)
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
                log.warning(
                    "LLM output validation failed (attempt %d): %s",
                    attempt, last_err,
                )
                # Build repair message: append validator error + bad JSON.
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw_json},
                    {
                        "role": "user",
                        "content": (
                            "上一次输出不符合 JSON Schema, 请仅修正以下问题并重新输出"
                            "完整 JSON (不要省略任何字段):\n"
                            f"{last_err}\n\n"
                            "仍须严格遵循 Schema, 不要添加解释文字。"
                        ),
                    },
                ]
                attempt += 1

        latency = int((time.perf_counter() - t0) * 1000)
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
            f"{settings.llm.max_repair_attempts} repair attempts: {last_err}"
        )

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        m = model or settings.gemini.model_embed
        return await self.transport.embed(model=m, texts=texts)
