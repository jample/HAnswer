"""Python wrapper around the Node `acorn` viz validator (§3.3.3).

Spawns `node validate.mjs` once per viz and parses the JSON report.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_VALIDATOR_DIR = Path(__file__).resolve().parent.parent.parent / "viz_validator"
_VALIDATOR_SCRIPT = _VALIDATOR_DIR / "validate.mjs"

_CODE_FENCE_RE = re.compile(r"^\s*```(?:javascript|js)?\s*([\s\S]*?)\s*```\s*$", re.IGNORECASE)
_FULL_FUNCTION_RE = re.compile(
    r"""
    ^\s*
    \(?\s*
    function
    (?:\s+[A-Za-z_$][A-Za-z0-9_$]*)?
    \s*\(\s*board\s*,\s*JXG\s*,\s*H\s*,\s*params\s*\)
    \s*\{
    (?P<body>[\s\S]*)
    \}
    \s*\)?\s*;?\s*$
    """,
    re.VERBOSE,
)
_ARROW_FUNCTION_RE = re.compile(
    r"""
    ^\s*
    \(?\s*
    \(?\s*board\s*,\s*JXG\s*,\s*H\s*,\s*params\s*\)?
    \s*=>\s*
    \{
    (?P<body>[\s\S]*)
    \}
    \s*\)?\s*;?\s*$
    """,
    re.VERBOSE,
)


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


def normalize_jsx_code(code: str) -> str:
    """Normalize LLM-emitted JSXGraph code to a function body.

    The sandbox + AST validator expect only the function body. In
    practice some model outputs still wrap the body as a full function:

    - ``function(board, JXG, H, params) { ... }``
    - ``(function(board, JXG, H, params) { ... })``
    - ``(board, JXG, H, params) => { ... }``

    This helper strips markdown fences and unwraps those common forms so
    they can still be validated and rendered.
    """
    text = str(code or "").strip()
    fence = _CODE_FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()

    for pattern in (_FULL_FUNCTION_RE, _ARROW_FUNCTION_RE):
        m = pattern.match(text)
        if m:
            return m.group("body").strip()
    return text


async def validate_jsx_code(code: str, *, timeout_s: float = 5.0) -> VizValidationReport:
    """Run the Node validator against `code`.

    Raises `VizValidationError` on rejection. Raises `RuntimeError` if the
    Node helper cannot be invoked (missing install) — callers should treat
    that as a hard server error, not a viz-level failure.
    """
    if not _VALIDATOR_SCRIPT.exists():
        raise RuntimeError(f"viz validator script missing: {_VALIDATOR_SCRIPT}")
    normalized = normalize_jsx_code(code)

    proc = await asyncio.create_subprocess_exec(
        "node", str(_VALIDATOR_SCRIPT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(_VALIDATOR_DIR),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(normalized.encode("utf-8")), timeout=timeout_s,
        )
    except asyncio.TimeoutError as e:
        proc.kill()
        await proc.wait()
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
