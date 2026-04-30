"""Structured visualization action logger.

Writes backend and frontend/runtime visualization traces to a dedicated
JSONL file so generation and rendering failures can be correlated.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)


def _truncate_text(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _sanitize(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return _truncate_text(str(value))


class JsonlVisualActionLogger:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or settings.storage.visual_action_log_file)

    async def record(self, record: dict[str, Any]) -> None:
        await self.record_many([record])

    async def record_many(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        try:
            payloads = [self._serialize(record) for record in records]
            await asyncio.to_thread(self._append_payloads, payloads)
        except Exception as exc:  # noqa: BLE001
            log.warning("visual action log write failed: %s", exc)

    def _append_payloads(self, payloads: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for payload in payloads:
                f.write(payload)

    @staticmethod
    def _serialize(record: dict[str, Any]) -> str:
        payload = {
            "timestamp": record.get("timestamp") or datetime.now(UTC).isoformat(),
            "source": str(record.get("source") or "unknown"),
            "phase": str(record.get("phase") or "unknown"),
            "action": str(record.get("action") or "unknown"),
            "status": str(record.get("status") or "info"),
            "question_id": record.get("question_id"),
            "solution_id": record.get("solution_id"),
            "visualization_id": record.get("visualization_id"),
            "engine": record.get("engine"),
            "component": record.get("component"),
            "details": _sanitize(record.get("details") or {}),
            "error": _sanitize(record.get("error")),
        }
        return json.dumps(payload, ensure_ascii=False) + "\n"


_LOGGER: JsonlVisualActionLogger | None = None


def get_visual_action_logger() -> JsonlVisualActionLogger:
    global _LOGGER
    configured_path = Path(settings.storage.visual_action_log_file)
    if _LOGGER is None or _LOGGER.path != configured_path:
        _LOGGER = JsonlVisualActionLogger(str(configured_path))
    return _LOGGER


async def log_visual_action(**record: Any) -> None:
    await get_visual_action_logger().record(record)