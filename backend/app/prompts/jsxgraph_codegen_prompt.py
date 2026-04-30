"""JSXGraph code generation prompt for HAVizNew Stage 2."""

from __future__ import annotations

import json
from typing import Any

from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.vizcoder_prompt import H_CHEATSHEET


class JsxGraphCodegenPrompt(PromptTemplate):
    version = PromptVersion(major=1, minor=3, date_updated="2026-04-22")
    name = "jsxgraph_codegen"

    purpose = (
        "把单个 VisualizationSpec 转换成稳定、可执行、数学含义忠实的 JSXGraph JavaScript 实现。"
    )

    input_description = "spec (单个 VisualizationSpec JSON)。"

    output_description = (
        "只输出 JavaScript 代码, 且必须定义唯一公开函数 renderVisualization(containerId, spec)。"
    )

    design_decisions = [
        DesignDecision(
            title="严格遵循代码-only 输出",
            rationale=(
                "HAVizNew Stage 2 明确要求只返回 JavaScript 代码, 不允许 Markdown fence 或解释性文本。"
            ),
        ),
        DesignDecision(
            title="固定 public API",
            rationale=(
                "把输出收敛到 renderVisualization(containerId, spec) 这一固定函数签名, 才能让前端"
                "运行时和错误恢复逻辑稳定演进。"
            ),
        ),
        DesignDecision(
            title="数学正确性高于视觉花哨",
            rationale=(
                "Stage 2 不应该重新解释 Stage 1 的数学含义。遇到复杂动画时优先采用 spec 中的"
                "fallback, 而不是为了炫技牺牲正确性。"
            ),
        ),
        DesignDecision(
            title="优先 slider 或 step 驱动",
            rationale=(
                "HAVizNew 已明确将 slider/step 视为更稳健的动画驱动方式, 比 autoplay 更容易验证"
                "也更符合教学交互。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return {"type": "string", "description": "Raw JavaScript source code only."}

    def preview(self, **kwargs: Any) -> str:
        messages = self.build(**kwargs)
        parts: list[str] = [
            "=" * 70,
            f"PROMPT: {self.name}  |  {self.version}",
            "=" * 70,
        ]
        for msg in messages:
            parts.append(f"\n--- [{msg['role'].upper()}] ---")
            parts.append(msg["content"])
        parts.append("\n" + "=" * 70)
        parts.append("OUTPUT: raw JavaScript only (no JSON schema wrapper)")
        parts.append("=" * 70)
        return "\n".join(parts)

    def fewshot_examples(self, **kwargs: Any) -> list[dict]:
        example_spec = {
            "id": "ex_slider_line",
            "title": "Point moving on a line",
            "interaction_and_animation": {
                "parameters": [
                    {
                        "name": "t",
                        "type": "number",
                        "range": {"min": -3, "max": 3, "step": 0.5},
                        "default_value": 0,
                        "meaning": "point position",
                    }
                ],
                "animation_driver": "slider",
                "animation_duration_ms": 2400,
            },
            "implementation_guidance": {
                "fallback_if_animation_is_too_complex": "Use three static point positions",
            },
        }
        example_code = """function renderVisualization(containerId, spec) {
  const specParams = Array.isArray(spec?.interaction_and_animation?.parameters)
    ? spec.interaction_and_animation.parameters
    : [];
  const param = specParams.find((item) => item && item.name === 't') || null;
  const runtimeValues = spec?.host_runtime?.parameter_values || {};
  const t = Number(runtimeValues.t ?? param?.default_value ?? 0);
  const board = JXG.JSXGraph.initBoard(containerId, {
    boundingbox: [-4, 4, 4, -4],
    axis: true,
    showNavigation: false,
    showCopyright: false,
  });
  const point = board.create('point', [t, 0], { name: 'P', size: 3 });
  const guide = board.create('line', [[-3, 0], [3, 0]], { straightFirst: false, straightLast: false });
  return {
    board,
    update(nextParams) {
      const nextT = Number(nextParams?.t ?? t);
      point.moveTo([nextT, 0]);
      board.update();
    },
    destroy() {
      if (guide && point) {
        board.removeObject([point, guide]);
      }
      JXG.JSXGraph.freeBoard(board);
    },
  };
}"""
        return [
            {
                "role": "user",
                "content": "Example VisualizationSpec\n" + json.dumps(example_spec, ensure_ascii=False, indent=2),
            },
            {"role": "assistant", "content": example_code},
        ]

    def system_message(self, **kwargs: Any) -> str:
        return f"""You are a senior front-end mathematical visualization engineer specializing in JSXGraph.

Your task is to convert a detailed visualization specification into a correct, executable, and stable JSXGraph implementation.

You must faithfully implement the specification.
You must NOT reinterpret the mathematics unless the specification explicitly contains ambiguity notes.
If there is a conflict, mathematical correctness and execution stability take priority over visual flourish.

## Primary Goal
Generate a single self-contained JavaScript implementation using JSXGraph that renders the requested visualization correctly.

## Hard Requirements
1. Output JavaScript code only.
2. Do NOT output markdown fences.
3. Do NOT output explanatory prose before or after the code.
4. The code must be executable in a browser environment where JSXGraph is already loaded.
5. The code must define exactly one public function:

function renderVisualization(containerId, spec)

6. The function must:
   - create or reset a JSXGraph board inside the given container
   - render the visualization described by spec
   - avoid leaking globals
   - be deterministic and stable
7. Do NOT use external libraries other than JSXGraph.
8. Do NOT invent mathematical objects or relations that are not present in the specification.
9. Do NOT silently change the meaning of the math.
10. Prefer a simpler but correct implementation over a visually sophisticated but fragile one.

## Defensive Engineering Requirements
1. Treat containerId as the board container identifier and pass it directly to JXG.JSXGraph.initBoard(containerId, ...); do not use DOM APIs such as document.getElementById directly.
2. The host runtime already disposes the previous render. Do NOT call JXG.JSXGraph.freeBoard(containerId) and do NOT free a board by string id.
3. Do NOT wrap the entire function body in a catch that suppresses failures. Let hard render/init failures propagate to the host runtime.
4. Never return an inert fallback such as null, undefined, or an object with board: null. On success, return a real controller object with a live board.
5. If the requested animation is too underspecified or unstable, implement the fallback strategy described in spec.implementation_guidance.fallback_if_animation_is_too_complex.
6. Include concise code comments where a simplification or approximation is intentionally used.
7. Never use eval, Function constructor, string-based code execution, or direct document/window access.

## Sandbox Hard Constraints (validator-enforced, will reject the code if violated)
The generated code runs inside an opaque-origin iframe with a strict allow-list. The backend AST validator and the runtime BOTH enforce these rules — violating any of them rejects the entire visualization.

ALLOWED globals (only these may appear unprefixed):
- `JXG`, `H`, `Math`, `Number`, `Array`, `Object`, `Boolean`, `String`, `JSON`
- `console`, `requestAnimationFrame`, `cancelAnimationFrame`
- function parameters `containerId`, `spec`, plus any local variables you declare.

FORBIDDEN globals (do NOT reference, even via computed access):
- `document`, `window`, `globalThis`, `self`, `top`, `parent`
- `Date`, `performance`, `navigator`, `location`, `history`
- `fetch`, `XMLHttpRequest`, `WebSocket`, `Worker`, `importScripts`
- `localStorage`, `sessionStorage`, `indexedDB`, `cookie`

FORBIDDEN constructs:
- `eval(...)`, `new Function(...)`, `Function(...)`, `require(...)`, `import(...)`, `import ...`, `with (...) { ... }`
- `setTimeout("string", ms)` / `setInterval("string", ms)` — only function callbacks are allowed
- `new Worker(...)`, `new WebSocket(...)`, `new XMLHttpRequest(...)`
- Computed member access to `"constructor"`, `"eval"`, or `"Function"` (e.g. `obj["constructor"]`)

TIMING:
- For any time-based logic use `requestAnimationFrame(cb)`. Never call `Date.now()` or `performance.now()`.
- Prefer slider-driven or step-index animation over auto-play loops.

SIZE:
- Stay under ~30 KB of source and under ~2000 AST nodes. Prefer a small, focused implementation.

## H helper reference
{H_CHEATSHEET}

## Critical spec fields to read first
- `spec.math_definition.objects`: the mathematical objects that must exist.
- `spec.visual_design`: viewport, labels, measurements, and whether trace/region display is required.
- `spec.interaction_and_animation.parameters[]`: the authoritative parameter list.
- `spec.interaction_and_animation.animation_duration_ms`: default animation duration if you animate automatically.
- `spec.expected_result` and `spec.implementation_guidance`: the required observable conclusion and simplification boundary.

## Parameter reading pattern
- Parameter definitions live at `spec.interaction_and_animation.parameters[]`.
- Updated live values arrive at `spec.host_runtime?.parameter_values?.[paramName]`.
- Always resolve each parameter with this precedence: runtime value first, then `default_value` from the parameter definition.
- For numeric parameters, use `range.min`, `range.max`, and `range.step` directly; do not parse text.

## Mathematical Integrity Rules
1. Distinguish carefully between circle boundary vs filled disk, line vs segment vs ray, graph vs region, and exact boundary vs illustrative overlay.
2. If the spec says distance to the circle boundary, do NOT implement distance to the filled disk.
3. If the expected result is a region, ensure the visual output clearly communicates a region, not just a curve.
4. If the expected result is generated by motion, the motion must support the mathematical explanation rather than distract from it.
5. If a final conclusion is known analytically and should be shown, display the relevant boundary objects explicitly when appropriate.

## Animation Rules
1. Prefer slider-parameterized animation for mathematical clarity and robustness.
2. If auto-play is implemented, it must remain optional and not break manual exploration.
3. Moving objects must stay mathematically constrained to their intended paths.
4. If trace accumulation is used, ensure the resulting visual supports the stated conclusion.
5. Avoid performance-heavy frame logic unless clearly necessary.

## Output Constraints
The generated code must be self-contained and directly usable.
Do not assume any framework wrapper.
Do not generate HTML.
Do not generate CSS.
Do not generate test code outside the function.

## Success Contract
- A successful implementation must return an object shaped like {{ board, update, destroy }}.
- `board` must be the live JSXGraph board instance created for containerId.
- `update` and `destroy` should be functions, even if one of them is a no-op.
- If the render cannot produce a live board, do not silently recover with a blank result.
"""

    def user_message(self, **kwargs: Any) -> str:
        spec = kwargs["spec"]
        return (
            "You will receive one visualization specification object.\n\n"
            "Generate JavaScript code only.\n\n"
            "## VisualizationSpec\n"
            + json.dumps(spec, indent=2, ensure_ascii=False)
        )