"""Viz AST validator adversarial test suite (§11.2).

Runs the Node helper (`viz_validator/validate.mjs`) against a battery of
LLM-style snippets. Skips entirely if `node` is not on PATH so developer
machines without Node don't fail the suite.
"""

from __future__ import annotations

import shutil

import pytest

from app.services.viz_validator import VizValidationError, validate_jsx_code


pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed",
)


# Each: (label, code, is_legal)
ADVERSARIAL: list[tuple[str, str, bool]] = [
    # --- legal ---
    ("plain-point",
     'var p = board.create("point",[1,2]); H.line(board,p,p);', True),
    ("math-use",
     'var x = Math.sin(params.t || 0); board.create("point",[x,0]);', True),
    ("raf-loop",
     'function step(){ board.update(); requestAnimationFrame(step); } step();',
     True),
    ("return-controller",
     'return { update: function(p){ }, destroy: function(){ } };', True),
    ("console-log",
     'console.log("ok");', True),
    ("helpers-only",
     'H.anim.animate(board, {duration_ms: 500}, function(t){ return t; });', True),
    ("local-var-ok",
     'var a = 1, b = 2; var c = a + b; console.log(c);', True),

    # --- illegal: network ---
    ("fetch", 'fetch("http://evil");', False),
    ("xhr",   'new XMLHttpRequest();', False),
    ("ws",    'new WebSocket("ws://x");', False),
    ("worker", 'new Worker("x.js");', False),
    ("importScripts", 'importScripts("x");', False),
    ("dynamic-import", 'import("./mod.js");', False),
    ("static-import", 'import x from "y";', False),

    # --- illegal: code execution ---
    ("eval",        'eval("1+1");', False),
    ("Function-ctor", 'new Function("return 1")();', False),
    ("Function-call", 'Function("return 1")();', False),
    ("string-settimeout", 'setTimeout("boom", 1);', False),
    ("string-setinterval", 'setInterval("boom", 1);', False),
    ("computed-eval", 'var f=globalThis["eval"]; f("1+1");', False),
    ("with-stmt", 'with(board){ update(); }', False),
    ("require", 'require("fs");', False),

    # --- illegal: DOM / global escape ---
    ("window-access", 'window.alert(1);', False),
    ("document-access", 'document.body.innerHTML="x";', False),
    ("parent-access", 'parent.postMessage("evil","*");', False),
    ("top-access", 'top.location = "http://evil";', False),
    ("globalThis", 'globalThis.fetch("x");', False),
    ("self-access", 'self.eval("1");', False),

    # --- illegal: storage / sensors ---
    ("localStorage", 'localStorage.setItem("k","v");', False),
    ("sessionStorage", 'sessionStorage.getItem("k");', False),
    ("indexedDB", 'indexedDB.open("x");', False),
    ("navigator", 'navigator.geolocation.getCurrentPosition(function(){});', False),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("label,code,legal", ADVERSARIAL, ids=[c[0] for c in ADVERSARIAL])
async def test_validator_classifies(label: str, code: str, legal: bool):
    if legal:
        report = await validate_jsx_code(code)
        assert report.ok, f"{label} should be legal"
    else:
        with pytest.raises(VizValidationError):
            await validate_jsx_code(code)


@pytest.mark.asyncio
async def test_validator_rejects_oversize():
    big = "var x = 0;\n" * 5000  # ≈60 KB → size cap
    with pytest.raises(VizValidationError):
        await validate_jsx_code(big)


@pytest.mark.asyncio
async def test_validator_rejects_syntax_error():
    with pytest.raises(VizValidationError):
        await validate_jsx_code("var x = ;")
