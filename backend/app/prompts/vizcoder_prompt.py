"""VizCoderPrompt v4 — config-driven interactive visualizations."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.prompts._audience import curriculum_boundary_block
from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import VISUALIZATION_LIST_SCHEMA


GGB_CHEATSHEET = """\
# GeoGebra cheatsheet (use when engine="geogebra")
# Command names must be English. One command per line. No semicolons between commands.

## 1. 点 / 向量
A=(2,3)
v=Vector((3,2))
M=Midpoint(A,B)
P=Reflect(A, Line(B,C))
Q=Rotate(A, 30deg, O)
R=(x(K)+2*cos(t), y(K)+2*sin(t))

## 2. 线 / 圆 / 多边形
l=Line(A,B)
l=Line((0,c),(c,0))
l=Line(P, Vector((1,-1)))
s=Segment(A,B)
c=Circle((0,0),2)
c=Circle(A,B,C)
poly=Polygon(A,B,C,D)

## 3. 函数 / 曲线
f(x)=x^2
g(x)=sin(x)/x
h(x)=If(x<0, -x, x^2)
Curve(cos(t), sin(t), t, 0, 2*pi)
Curve(t, t^2, t^3, t, -2, 2)

## 4. 物理 / 动画
a=Slider(-3, 3, 0.1)
SetAnimating(a, true)
SetAnimationSpeed(a, 1)
StartAnimation()
SetTrace(P, true)

## 5. 文字 / 样式
SetCaption(A, "起点")
ShowLabel(A, true)
SetColor(c1, 255, 0, 0)
SetLineStyle(l1, 2)
SetLineThickness(l1, 4)

## 6. Parameters and view rules
- ggb_commands should only define objects, for example `a=Slider(-3,3,0.1)` or `flag=false`.
- Do not write `SetValue(a, 1.2)` / `SetValue(flag, true)` in ggb_commands.
- Put initial values in params[].default; the frontend will sync them after object creation.
- If a slider / toggle appears in params, ggb_commands must define an object with the same name.
- Put view range, grid, axes, and perspective in ggb_settings, not in ggb_commands.
- Do not use `SetConditionToShowObject(...)`; use conditional definitions with `If(...)` instead.
- Dependent point offsets must be written as `P=(x(K)+dx, y(K)+dy)`; do not write `P=K+(dx,dy)`.
- SetColor must use an RGB triplet, not a named color string.
"""

H_CHEATSHEET = """\
# JSXGraph helper `H` (prefer this when engine="jsxgraph")

## 1. 图形 / 曲线
H.shapes.circle(cx, cy, r, attrs)
H.shapes.triangle(A, B, C, attrs)
H.shapes.polygon(points, attrs)
H.shapes.segmentWithLabel(P, Q, label, attrs)
H.plot.functionGraph(fn, domain, attrs)
H.plot.parametric({x: fx, y: fy}, tRange, attrs)
H.plot.vectorField(fn, grid, attrs)

## 2. 物理 / 几何
H.phys.projectile({v0, angle, g}, attrs)
H.phys.springMass({k, m, x0}, attrs)
H.geom.midpoint(P, Q, attrs)
H.geom.reflect(P, line, attrs)
H.geom.rotate(P, center, angleDeg, attrs)
H.geom.intersectionPoint(a, b, attrs)

## 3. 动画
H.anim.loop({
  durationMs: 4000,
  onFrame: function(progress, elapsedMs) { ... },
  easing: "linear",   // optional: linear | easeInOutSine
  yoyo: false,        // optional
  repeat: true        // optional
})

H.anim.oscillate({
  from: -2,
  to: 2,
  durationMs: 3000,
  onValue: function(value, progress) { ... },
  easing: "easeInOutSine",  // optional
  yoyo: true,               // optional
  repeat: true              // optional
})

H.anim.animate(paramName, from, to, durationMs, onUpdate)

## 4. JSXGraph controller pattern
Recommended return value:
{
  update: function(nextParams) { ... },
  destroy: function() { ... }
}

Rules:
- Create objects once, then update coordinates / functions / text inside animation frames. Do not rebuild the whole board every frame.
- After mutating objects inside animation, `H.anim.loop(...)` handles the `board.update()` call.
- When parameters change, sync state inside `update(nextParams)` so drag interactions and automatic animation remain consistent.
"""

ALLOWED_GLOBALS = [
    "board", "JXG", "H", "params",
    "Math", "Number", "Array", "Object", "Boolean", "String", "JSON",
    "console", "requestAnimationFrame", "cancelAnimationFrame",
]

FORBIDDEN_GLOBALS = [
    "window", "document", "globalThis", "self", "top", "parent", "frames",
    "fetch", "XMLHttpRequest", "WebSocket", "Worker", "importScripts",
    "eval", "Function", "import", "require",
    "localStorage", "sessionStorage", "indexedDB",
    "setTimeout(string)", "setInterval(string)", "with",
]


def _preferred_engine(kwargs: dict[str, Any]) -> str:
    raw = str(kwargs.get("preferred_engine") or settings.viz.default_engine).strip().lower()
    if raw in {"jsxgraph", "geogebra"}:
        return raw
    return "jsxgraph"


_FEWSHOT_VIZCODER_USER = {
    "parsed_question": {"subject": "math", "grade_band": "junior", "question_text": "通过图像理解二次函数交点"},
    "answer_package": {"solution_steps": [{"step_index": 1, "statement": "先画出函数图像并标出交点。"}]},
}

_FEWSHOT_VIZCODER_ASSISTANT = {
    "visualizations": [
        {
            "id": "viz_batch_example_1",
            "title_cn": "交点建立",
            "caption_cn": "先固定抛物线，再看与 x 轴的交点位置。",
            "learning_goal": "建立函数图像与交点的联系",
            "interactive_hints": ["拖动参数观察交点变化"],
            "helpers_used": [],
            "engine": "geogebra",
            "jsx_code": "",
            "ggb_commands": ["t=Slider(-2,2,0.1)", "f(x)=x^2-2*x+t", "A=Intersect(f,xAxis,1)", "B=Intersect(f,xAxis,2)"],
            "ggb_settings": {"app_name": "graphing", "axes_visible": True, "grid_visible": True},
            "params": [{"name": "t", "label_cn": "参数 t", "kind": "slider", "min": -2, "max": 2, "step": 0.1, "default": 0}],
            "animation": None,
        },
        {
            "id": "viz_batch_example_2",
            "title_cn": "顶点比较",
            "caption_cn": "补出顶点后再判断最值位置。",
            "learning_goal": "理解顶点与最值",
            "interactive_hints": [],
            "helpers_used": [],
            "engine": "geogebra",
            "jsx_code": "",
            "ggb_commands": ["t=Slider(-2,2,0.1)", "f(x)=x^2-2*x+t", "V=Extremum(f)"],
            "ggb_settings": {"app_name": "graphing", "axes_visible": True, "grid_visible": True},
            "params": [{"name": "t", "label_cn": "参数 t", "kind": "slider", "min": -2, "max": 2, "step": 0.1, "default": 0}],
            "animation": None,
        }
    ]
}


def _engine_policy_block(preferred_engine: str) -> str:
    if preferred_engine == "geogebra":
        return """\
## Engine selection (important)
- engine="geogebra" — this is the current server-side default and should be preferred.
    - Best for standard function graphs, plane geometry, conic sections, solid geometry, and structured slider-driven constructions.
    - Output ggb_commands as ["...", "..."]; each entry is one GeoGebra command string.
        jsx_code should be an empty string.
    - ggb_settings.app_name may be classic / geometry / graphing / 3d / suite.
    - Put slider / toggle initial values in params[].default, not in SetValue(...).

- engine="jsxgraph" — keep this as an optional engine.
    - Use it when the problem clearly needs freer animation, frame-by-frame physics illustration, custom trajectories, local redraws, or finer interaction control.
    - Output jsx_code (function body) and leave ggb_commands as an empty array.
"""
    return """\
## Engine selection (important)
- engine="jsxgraph" — this is the current server-side default and should be preferred.
    - Use JSXGraph by default for animated parameter changes, continuous motion of functions / geometric objects, frame-by-frame physics demonstrations, or freer interaction logic.
    - Output jsx_code (function body) and leave ggb_commands as an empty array.
    - Prefer the stable structure: create objects once + return controller.update/destroy + drive motion with H.anim.*.
    - At least one figure should include lightweight animation or explicit dynamic behavior when the problem naturally involves continuous change. If the problem is fundamentally static, provide at least continuous slider / draggable parameter change.

- engine="geogebra" — keep this as an optional engine.
    - Use GeoGebra when the problem is standard Euclidean construction or when GeoGebra is clearly more direct and stable.
    - Output ggb_commands: ["...", "..."] and leave jsx_code as an empty string.
"""


def _engine_specific_rules(preferred_engine: str) -> str:
    if preferred_engine == "geogebra":
        return """\
## GeoGebra rules (when engine=geogebra)
- One command per line. Do not include newlines inside a command or add a ggbApplet prefix.
- Put only object / style / animation creation commands in ggb_commands; put view / axes / grid / perspective in ggb_settings.
- Do not use Greek-letter English aliases as variable names (alpha, beta, theta, ...); GeoGebra will rename them.
- Dependent offsets from another point must be written as `P=(x(K)+dx, y(K)+dy)`.
- Do not use wrappers such as `SetValue(...)`, `SetConditionToShowObject(...)`, or `Line(ax+by=c)`.
- Max command length is 512 characters; max total command count is 64.

## Fallback JSXGraph rules (when engine=jsxgraph)
- jsx_code must contain only the **function body**, not an outer wrapper like
    `function(board, JXG, H, params) { ... }`.
- Return `{ update(nextParams), destroy() }` or `undefined`.
- Safety: allowed globals are only __ALLOW__; forbidden globals are __FORBID__.
- When animation is needed, prefer `H.anim.loop(...)` / `H.anim.oscillate(...)`.
"""
    return """\
## JSXGraph rules (when engine=jsxgraph)
- jsx_code must contain only the **function body**, not an outer wrapper like
    `function(board, JXG, H, params) { ... }`.
- Recommended structure:
    1. read `params` and initialize state
    2. create points / lines / curves / text objects
    3. write a `sync(...)` function that updates positions / text / style
    4. if animation is needed, use `H.anim.loop(...)` / `H.anim.oscillate(...)`
    5. return `{ update(nextParams), destroy() }`
- Animation should be lightweight: update existing objects instead of rebuilding with `board.create(...)` every frame.
- Prefer the `H.*` helpers and call `board.create(...)` directly only when necessary.
- Safety: allowed globals are only __ALLOW__; forbidden globals are __FORBID__.
- Do not use string-based setTimeout / setInterval. Do not use import / require / with.

## Fallback GeoGebra rules (when engine=geogebra)
- Put one command per line in ggb_commands and include only object / style / animation commands.
- Put slider / toggle initial values in params[].default, not in SetValue(...).
- Put view settings in ggb_settings; do not write SetCoordSystem / ShowGrid / ShowAxes in ggb_commands.
"""


class VizCoderPrompt(PromptTemplate):

    version = PromptVersion(major=4, minor=1, date_updated="2026-04-22")
    name = "vizcoder"

    purpose = (
        "根据已生成的 AnswerPackage, 产出一组数学/物理交互式可视化; "
        "默认引擎由服务端配置决定, 当前支持 JSXGraph 与 GeoGebra。"
    )

    input_description = (
        "answer_package (AnswerPackage JSON, 必需), "
        "parsed_question (ParsedQuestion JSON, 必需)。"
    )

    output_description = (
        "符合 {visualizations: [...]} 的 JSON; 每个可视化指定 engine, 并提供"
        "jsx_code (jsxgraph) 或 ggb_commands (geogebra) 之一。"
    )

    design_decisions = [
        DesignDecision(
            title="默认引擎由配置决定",
            rationale=(
                "渲染引擎优先级不再写死在提示里, 而是由 backend/config.toml "
                "控制, 便于在不同阶段切换到更稳定的生成策略。"
            ),
            alternatives_considered=[
                "继续固定 GeoGebra-first — 切换成本高, 难针对现阶段问题快速调优",
                "彻底移除 GeoGebra — 会损失某些标准几何作图场景",
            ],
        ),
        DesignDecision(
            title="当前默认偏向 JSXGraph",
            rationale=(
                "现阶段 GeoGebra 命令常出现 Apps API 难以兜底的语义错误。"
                "JSXGraph 路径虽然需要 AST 校验, 但代码生成和运行时行为更可控,"
                "更适合做动画与逐帧演示。"
            ),
        ),
        DesignDecision(
            title="提示中强化 controller + 动画 helper 模式",
            rationale=(
                "仅说“输出 JSXGraph”不够。必须给 LLM 一个稳定骨架: 创建对象一次,"
                "参数更新走 controller.update, 动画走 H.anim.*。这样能明显减少"
                "每帧重建对象、内存泄漏和 destroy 不完整的问题。"
            ),
        ),
        DesignDecision(
            title="保留 GeoGebra 为可选引擎",
            rationale="标准欧式几何和某些规范化数学作图仍可能更适合 GeoGebra。",
        ),
        DesignDecision(
            title="每图必带 learning_goal",
            rationale="强制一句话学习目标, 防止生成装饰性可视化。",
        ),
        DesignDecision(
            title="id 与 solution_steps[].viz_ref 对齐",
            rationale="前端可精确把步骤锚定到对应的图。",
        ),
        DesignDecision(
            title="可视化必须服务于已生成的解答",
            rationale=(
                "VizCoder 在 Solver 之后运行, 输入包含完整 AnswerPackage。"
                "提示中显式要求模型阅读 solution_steps / formulas / pitfalls, "
                "优先把关键步骤和难点做成图。"
            ),
        ),
        DesignDecision(
            title="符号一致性: 复用题目/解答中的命名",
            rationale=(
                "滑块名、动点名、参数名若与题面/解答不一致, 学生需要额外做"
                "符号映射, 会削弱可视化教学价值。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return VISUALIZATION_LIST_SCHEMA

    def fewshot_examples(self, **kwargs: Any) -> list[dict]:
        return [
            {"role": "user", "content": "Example compact input\n" + json.dumps(_FEWSHOT_VIZCODER_USER, ensure_ascii=False, indent=2)},
            {"role": "assistant", "content": json.dumps(_FEWSHOT_VIZCODER_ASSISTANT, ensure_ascii=False, indent=2)},
        ]

    def system_message(self, **kwargs: Any) -> str:
        preferred_engine = _preferred_engine(kwargs)
        schema_str = json.dumps(self.schema, indent=2, ensure_ascii=False)
        allow = ", ".join(ALLOWED_GLOBALS)
        forbid = ", ".join(FORBIDDEN_GLOBALS)
        body = f"""\
你是数学/物理交互式可视化设计师, 为中学生把题目做成真正帮助理解解题过程的交互图。

{_engine_policy_block(preferred_engine)}

## 通用要求
- 输入包含 ParsedQuestion 与 **完整的 AnswerPackage**。可视化必须服务于该
  解答, 帮助学生理解解题过程, 而不是凭题目自由发挥。
- 在动笔之前, 先按下列顺序通读 AnswerPackage:
  1. method_pattern / key_points_of_answer — 决定整组图的主题。
    2. solution_steps[] — 只选最关键、最难想象的 2 步, 为它们各配一张图;
     若该步已有 viz_ref, 沿用同一 id, caption_cn 中复述该步的核心结论。
  3. formulas — 关键公式必须在图中体现为曲线、几何关系、向量、运动轨迹或标注。
  4. pitfalls — 若有分类讨论、边界情形、临界值, 优先做成可切换或可拖动的对比图。
  5. final_answer / 最终结论 — 图中应明确标出关键结果, 让学生看到“答案在图上如何出现”。
- **生成 1 或 2 个可视化, 不要凑数**。
    - 只保留最重要的 1-2 个学习难点或关键阶段。
    - 若只需要 1 张图就能把关键困难讲清, 不要机械补到 2 张。
    - 若生成 2 张图, 它们必须覆盖不同关键阶段, 不要重复画同一件事。
    - 若题目存在真正关键的分类讨论或多个情形, 应把其中一个名额留给它。
  - 每图必须有清晰的 learning_goal。
- **符号一致性 (重要)**:
  - 可视化中的几何对象、点、参数必须复用题目和解答中的符号。
  - 不要为同一个对象起新名 (例如把题目里的圆心 `K` 改成 `k1`)。
  - 不要凭空引入无意义的新滑块名; 若题面/解答已有 `t`, 优先直接用 `t`。
  - params[].label_cn 用题目/解答里的中文术语。
  - caption_cn / interactive_hints 里的参数名必须与渲染代码中一致。
- **学段约束 (重要)**:
    - ParsedQuestion.subject 与 ParsedQuestion.grade_band 是硬性课程边界。
    - 若 grade_band=junior, 只能使用初中阶段学生已经学过、也能理解的知识来组织图示与说明。
    - 不要用高中知识去解释初中题, 否则会增加理解负担。
- caption_cn 用简体中文一句话说明该图如何对应解答中的某一步; 可含 LaTeX, 用 $...$ 包裹。
- interactive_hints 给学生明确操作建议。
- 严禁生成与 AnswerPackage 中任何步骤、公式或结论无关的装饰性图。

{_engine_specific_rules(preferred_engine)}

## GeoGebra 命令速查
__GGB__

## JSXGraph helper 速查
__H__

## 输出格式
- 仅输出单个 JSON 对象, 不含 ```json 标记。
- 结构严格匹配下方 Schema。

## JSON Schema
__SCHEMA__
"""
        return (
            body
            .replace("__GGB__", GGB_CHEATSHEET)
            .replace("__H__", H_CHEATSHEET)
            .replace("__ALLOW__", allow)
            .replace("__FORBID__", forbid)
            .replace("__SCHEMA__", schema_str)
        )

    def user_message(self, **kwargs: Any) -> str:
        answer_package: dict = kwargs["answer_package"]
        parsed_question: dict = kwargs["parsed_question"]
        preferred_engine = _preferred_engine(kwargs)
        steps = answer_package.get("solution_steps") or []
        pitfalls = (answer_package.get("method_pattern") or {}).get("pitfalls") or []
        step_lines = []
        for s in steps:
            idx = s.get("step_index", "?")
            stmt = (s.get("statement") or "").strip().replace("\n", " ")
            if len(stmt) > 80:
                stmt = stmt[:80] + "…"
            ref = s.get("viz_ref") or ""
            tag = f" (viz_ref={ref})" if ref else ""
            step_lines.append(f"  - step {idx}: {stmt}{tag}")
        pitfall_lines = [f"  - {p}" for p in pitfalls]
        coverage_hint = (
            "\n\n## 覆盖要求 (必须遵守)\n"
            "- 从上面的 solution_steps / pitfalls / final_answer 中选出 **最重要的 2 个关键阶段或难点** 各配一张图,\n"
            "  并在每张图的 caption_cn 中明确写出对应的 step 编号 (例如 “对应解答 step 2”)。\n"
            "- 若某些 step 的 viz_ref 已给出, 优先为它们生成, id 必须同名。\n"
            "- 若 pitfalls 中的分类讨论/临界情形属于最重要难点之一, 必须有一张可切换或可拖动的对比图覆盖。\n"
            "- visualizations 数量必须正好 2 个; 绝不可交 1 个, 也不要超过 2 个。\n"
        )
        engine_hint = (
            '当前默认引擎: JSXGraph。优先输出 engine="jsxgraph"，'
            "除非某张图明显更适合 GeoGebra。"
            if preferred_engine == "jsxgraph"
            else '当前默认引擎: GeoGebra。优先输出 engine="geogebra"，'
                 "但若某张图需要更自由动画, 可以改用 JSXGraph。"
        )
        steps_block = "\n## 待覆盖的 solution_steps\n" + ("\n".join(step_lines) or "  (无)")
        pitfalls_block = "\n\n## 待覆盖的 pitfalls\n" + ("\n".join(pitfall_lines) or "  (无)")
        return (
            "## ParsedQuestion\n"
            + json.dumps(parsed_question, indent=2, ensure_ascii=False)
            + "\n\n"
            + curriculum_boundary_block(parsed_question, language="zh")
            + "\n\n## AnswerPackage (不含 visualizations)\n"
            + json.dumps(answer_package, indent=2, ensure_ascii=False)
            + steps_block
            + pitfalls_block
            + coverage_hint
            + "\n请基于上面的 AnswerPackage 只生成 2 个交互式可视化。"
            + "\n" + engine_hint
            + "\n要求:"
            + "\n- 在写代码/命令前, 先列出 ParsedQuestion / AnswerPackage 中已经"
            + "出现的几何对象与参数名, 直接复用这些名字。"
            + "\n- 每个可视化都必须显式对应 AnswerPackage.solution_steps 中的"
            + "某一步, caption_cn 中复述该步要点。"
            + "\n- 优先为关键公式、难点、分类讨论以及最终结论配图。"
            + "\n- 若使用 JSXGraph, 优先写稳定的 controller 模式:"
            + " 创建对象一次, update(nextParams) 只更新状态, destroy() 负责释放动画。"
            + "\n- 若使用 JSXGraph 且题目存在连续变化/运动过程, 至少一张图加入轻量动画"
            + " 或显式动态演示。"
            + "\n- 若使用 GeoGebra, params 中的 slider/toggle 在 ggb_commands 中只定义同名对象,"
            + " 不要再写 SetValue(name, ...); 初始值放到 params[].default。"
        )
