"""Postgres-backed CostLedger — persists every LLM call (§7.1.3, M8).

Each call to `record(rec)` inserts a row into `llm_calls`. Failures are
swallowed (logged only) because ledger I/O must never break a user-
facing LLM call; the ledger is observability, not the critical path.

Wired into `GeminiClient` via `llm_deps.get_llm_client()`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from app.db.models import LLMCall
from app.db.session import session_scope
from app.services.llm_client import LLMCallRecord

log = logging.getLogger(__name__)


class PgCostLedger:
    """Writes LLMCallRecord rows to the `llm_calls` table."""

    async def record(self, rec: LLMCallRecord) -> None:
        try:
            async with session_scope() as s:
                s.add(LLMCall(
                    task=rec.task,
                    prompt_version=rec.prompt_version,
                    model=rec.model,
                    prompt_tokens=rec.prompt_tokens,
                    completion_tokens=rec.completion_tokens,
                    cost_usd=rec.cost_usd,
                    latency_ms=rec.latency_ms,
                    status=rec.status,
                    error=rec.error,
                ))
        except Exception as e:  # noqa: BLE001
            # Observability write must never break the LLM path.
            log.warning("cost ledger write failed: %s (rec=%s)", e, asdict(rec))
