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
_FULL_FUNCTION_HEADER_RE = re.compile(
    r"""
    ^\s*
    \(?\s*
    function
    (?:\s+[A-Za-z_$][A-Za-z0-9_$]*)?
    \s*\(\s*board\s*,\s*JXG\s*,\s*H\s*,\s*params\s*\)
    \s*\{
    """,
    re.VERBOSE,
)
_ARROW_FUNCTION_HEADER_RE = re.compile(
    r"""
    ^\s*
    \(?\s*
    \(?\s*board\s*,\s*JXG\s*,\s*H\s*,\s*params\s*\)?
    \s*=>\s*
    \{
    """,
    re.VERBOSE,
)
_RENDER_VISUALIZATION_HEADER_RE = re.compile(
    r"""
    ^\s*
    function
    \s+renderVisualization
    \s*\(\s*containerId\s*,\s*spec\s*\)
    \s*\{
    """,
    re.VERBOSE,
)


def _strip_code_fence(code: str) -> str:
    text = str(code or "").strip()
    fence = _CODE_FENCE_RE.match(text)
    if fence:
        text = fence.group(1).strip()
    return text


def _extract_brace_body(source: str, opening_brace_index: int) -> tuple[str, str] | None:
    depth = 0
    for idx in range(opening_brace_index + 1, len(source)):
        char = source[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            if depth == 0:
                return source[opening_brace_index + 1:idx], source[idx + 1:]
            depth -= 1
    return None


def _unwrap_function_body(source: str, header_re: re.Pattern[str]) -> str | None:
    match = header_re.match(source)
    if not match:
        return None
    opening_brace_index = match.end() - 1
    extracted = _extract_brace_body(source, opening_brace_index)
    if extracted is None:
        return None
    body, suffix = extracted
    suffix = suffix.strip()
    if suffix not in {"", ")", ");", ";"}:
        return None
    return body.strip()


def _is_render_visualization_wrapper(source: str) -> bool:
    body = _unwrap_function_body(source, _RENDER_VISUALIZATION_HEADER_RE)
    return body is not None


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


def normalize_jsx_code(code: str, *, preserve_render_wrapper: bool = False) -> str:
    """Normalize LLM-emitted JSXGraph code to a function body.

    The sandbox + AST validator expect only the function body. In
    practice some model outputs still wrap the body as a full function:

    - ``function(board, JXG, H, params) { ... }``
    - ``(function(board, JXG, H, params) { ... })``
    - ``(board, JXG, H, params) => { ... }``

    This helper strips markdown fences and unwraps those common forms so
    they can still be validated and rendered.
    """
    text = _strip_code_fence(code)
    render_body = _unwrap_function_body(text, _RENDER_VISUALIZATION_HEADER_RE)
    if render_body is not None:
        return text if preserve_render_wrapper else render_body

    for pattern in (_FULL_FUNCTION_HEADER_RE, _ARROW_FUNCTION_HEADER_RE):
        body = _unwrap_function_body(text, pattern)
        if body is not None:
            return body
    return text


async def validate_jsx_code(code: str, *, timeout_s: float = 5.0) -> VizValidationReport:
    """Run the Node validator against `code`.

    Raises `VizValidationError` on rejection. Raises `RuntimeError` if the
    Node helper cannot be invoked (missing install) — callers should treat
    that as a hard server error, not a viz-level failure.
    """
    if not _VALIDATOR_SCRIPT.exists():
        raise RuntimeError(f"viz validator script missing: {_VALIDATOR_SCRIPT}")
    validator_input = normalize_jsx_code(code, preserve_render_wrapper=True)

    proc = await asyncio.create_subprocess_exec(
        "node", str(_VALIDATOR_SCRIPT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(_VALIDATOR_DIR),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(validator_input.encode("utf-8")), timeout=timeout_s,
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
