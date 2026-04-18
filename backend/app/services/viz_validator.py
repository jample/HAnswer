"""Python wrapper around the Node `acorn` viz validator (§3.3.3).

Spawns `node validate.mjs` once per viz and parses the JSON report.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_VALIDATOR_DIR = Path(__file__).resolve().parent.parent.parent / "viz_validator"
_VALIDATOR_SCRIPT = _VALIDATOR_DIR / "validate.mjs"


class VizValidationError(Exception):
    """Raised when the AST validator rejects the code."""

    def __init__(self, violations: list[dict]) -> None:
        self.violations = violations
        super().__init__(
            "viz validation failed: " + "; ".join(v.get("message", "") for v in violations)
        )


@dataclass
class VizValidationReport:
    ok: bool
    node_count: int = 0
    violations: list[dict] | None = None


async def validate_jsx_code(code: str, *, timeout_s: float = 5.0) -> VizValidationReport:
    """Run the Node validator against `code`.

    Raises `VizValidationError` on rejection. Raises `RuntimeError` if the
    Node helper cannot be invoked (missing install) — callers should treat
    that as a hard server error, not a viz-level failure.
    """
    if not _VALIDATOR_SCRIPT.exists():
        raise RuntimeError(f"viz validator script missing: {_VALIDATOR_SCRIPT}")

    proc = await asyncio.create_subprocess_exec(
        "node", str(_VALIDATOR_SCRIPT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(_VALIDATOR_DIR),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(code.encode("utf-8")), timeout=timeout_s,
        )
    except asyncio.TimeoutError as e:
        proc.kill()
        raise VizValidationError(
            [{"kind": "timeout", "message": f"validator timed out after {timeout_s}s"}]
        ) from e

    if proc.returncode != 0:
        raise RuntimeError(
            f"viz validator exited {proc.returncode}: {stderr.decode('utf-8', errors='replace')}"
        )

    try:
        report = json.loads(stdout.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"validator produced non-JSON output: {stdout!r}") from e

    if not report.get("ok", False):
        raise VizValidationError(report.get("violations", []))

    return VizValidationReport(ok=True, node_count=int(report.get("node_count", 0)))
