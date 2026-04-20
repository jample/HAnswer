"""VizCoderPrompt v4 — config-driven interactive visualizations."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import VISUALIZATION_LIST_SCHEMA


GGB_CHEATSHEET = """\
# GeoGebra 命令速查 (engine="geogebra" 时使用)
# 命令名必须使用英文; 一行一个命令; 命令之间没有分号。

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

## 6. 参数与视图规范
- ggb_commands 只负责“定义对象”, 例如 `a=Slider(-3,3,0.1)`、`flag=false`。
- 不要在 ggb_commands 中写 `SetValue(a, 1.2)` / `SetValue(flag, true)`。
- 初始值写到 params[].default; 前端会在对象创建后自动同步 default。
- 若某个滑块/开关出现在 params 里, ggb_commands 中必须有同名定义。
- 视图范围、网格、坐标轴、视角放进 ggb_settings, 不要写进 ggb_commands。
- 不要使用 `SetConditionToShowObject(...)`; 请改成 `If(...)` 条件定义。
- 依赖点位移必须写 `P=(x(K)+dx, y(K)+dy)`; 禁止 `P=K+(dx,dy)`。
- SetColor 必须用 RGB 三元组, 禁止色名字符串。
"""

H_CHEATSHEET = """\
# JSXGraph helper `H` (engine="jsxgraph" 时推荐优先使用)

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

## 4. JSXGraph 控制器模式
推荐返回:
{
  update: function(nextParams) { ... },
  destroy: function() { ... }
}

规则:
- 先创建对象, 再在动画帧里更新坐标/函数/文本, 不要每帧重建整张图。
- 动画里修改对象后调用 `board.update()` 的工作由 `H.anim.loop(...)` 完成。
- 参数变化时在 `update(nextParams)` 内同步状态, 保持学生拖动控件和自动动画一致。
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


def _engine_policy_block(preferred_engine: str) -> str:
    if preferred_engine == "geogebra":
        return """\
## 引擎选择 (重要)
- engine="geogebra" — 当前服务端配置的默认引擎, 优先使用。
  - 适合标准函数图、平面几何、圆锥曲线、立体几何、滑块驱动的规范作图。
  - 输出 ggb_commands: ["...", "..."], 每条一个 GeoGebra 命令字符串;
    jsx_code 留空字符串。
  - ggb_settings.app_name 可选 classic / geometry / graphing / 3d / suite。
  - 滑块/开关初值写到 params[].default, 不要写 SetValue(...).

- engine="jsxgraph" — 作为可选引擎保留。
  - 当题目需要更自由的动画、逐帧物理示意、自定义轨迹、局部重绘或
    更精细的交互控制时, 可改用 JSXGraph。
  - 输出 jsx_code (函数体), ggb_commands 留空数组。
"""
    return """\
## 引擎选择 (重要)
- engine="jsxgraph" — 当前服务端配置的默认引擎, 优先使用。
  - 当你要做“参数变化带动画”“函数/几何对象随时间连续运动”“物理过程逐帧演示”
    或需要较自由的交互逻辑时, 默认就用 JSXGraph。
  - 输出 jsx_code (函数体), ggb_commands 留空数组。
  - 优先写“创建对象一次 + 返回 controller.update/destroy + 用 H.anim.* 驱动”的结构。
  - 至少 1 张图应在合适时包含轻量动画或显式动态过程展示; 若题目本质静态,
    则至少提供连续可拖动/可滑动的参数变化。

- engine="geogebra" — 作为可选引擎保留。
  - 当题目是标准欧式几何作图、GeoGebra 表达更直接且更稳时, 可使用 GeoGebra。
  - 输出 ggb_commands: ["...", "..."], jsx_code 留空字符串。
"""


def _engine_specific_rules(preferred_engine: str) -> str:
    if preferred_engine == "geogebra":
        return """\
## GeoGebra 规范 (engine=geogebra 时)
- 一行一个命令, 不要含换行或 ggbApplet 前缀。
- ggb_commands 只放创建对象/样式/动画的命令; 视图/坐标轴/网格/视角放进 ggb_settings。
- 变量/对象名禁止使用希腊字母英文别名 (alpha, beta, theta, ...); 会被 GeoGebra 改名。
- 依赖另一个点的位移必须写 `P=(x(K)+dx, y(K)+dy)`。
- 禁止 `SetValue(...)`、`SetConditionToShowObject(...)`、`Line(ax+by=c)` 包装写法。
- 单条命令 ≤ 512 字符, 总命令数 ≤ 64。

## JSXGraph 备用规范 (engine=jsxgraph 时)
- jsx_code 只写 **函数体本身**, 不要输出外层
  `function(board, JXG, H, params) { ... }` 包装。
- 返回 `{ update(nextParams), destroy() }` 或 `undefined`。
- 安全: 仅可使用全局 __ALLOW__; 严禁使用 __FORBID__。
- 若需要动画, 优先用 `H.anim.loop(...)` / `H.anim.oscillate(...)`。
"""
    return """\
## JSXGraph 规范 (engine=jsxgraph 时)
- jsx_code 只写 **函数体本身**, 不要输出外层
  `function(board, JXG, H, params) { ... }` 包装。
- 推荐结构:
  1. 读取 `params` 建立初始状态
  2. 创建点/线/曲线/文字对象
  3. 写一个 `sync(...)` 更新对象位置/文本/样式
  4. 若需要动画, 用 `H.anim.loop(...)` / `H.anim.oscillate(...)`
  5. 返回 `{ update(nextParams), destroy() }`
- 动画应是轻量的: 更新已有对象, 不要每帧 `board.create(...)` 重建对象。
- 优先用 `H.*` 帮手, 仅在必要时直接调用 `board.create(...)`。
- 安全: 仅可使用全局 __ALLOW__; 严禁使用 __FORBID__。
- 禁止字符串形式的 setTimeout/setInterval, 禁止 import/require/with。

## GeoGebra 备用规范 (engine=geogebra 时)
- ggb_commands 一行一个命令, 只放创建对象/样式/动画命令。
- 滑块/开关初值写入 params[].default, 不要写 SetValue(...).
- 视图设置放进 ggb_settings, 不要把 SetCoordSystem / ShowGrid / ShowAxes 写进 ggb_commands。
"""


class VizCoderPrompt(PromptTemplate):

    version = PromptVersion(major=4, minor=0, date_updated="2026-04-20")
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
  2. solution_steps[] — 找出最关键、最难想象的 2-3 步, 为它们各配一张图;
     若该步已有 viz_ref, 沿用同一 id, caption_cn 中复述该步的核心结论。
  3. formulas — 关键公式必须在图中体现为曲线、几何关系、向量、运动轨迹或标注。
  4. pitfalls — 若有分类讨论、边界情形、临界值, 优先做成可切换或可拖动的对比图。
  5. final_answer / 最终结论 — 图中应明确标出关键结果, 让学生看到“答案在图上如何出现”。
- **必须生成 3-4 个可视化**。
  - 不同可视化要覆盖不同关键阶段, 不要重复画同一件事。
  - 若题目存在分类讨论或多个情形, 必须至少有一张图覆盖这些情形。
  - 每图必须有清晰的 learning_goal。
- **符号一致性 (重要)**:
  - 可视化中的几何对象、点、参数必须复用题目和解答中的符号。
  - 不要为同一个对象起新名 (例如把题目里的圆心 `K` 改成 `k1`)。
  - 不要凭空引入无意义的新滑块名; 若题面/解答已有 `t`, 优先直接用 `t`。
  - params[].label_cn 用题目/解答里的中文术语。
  - caption_cn / interactive_hints 里的参数名必须与渲染代码中一致。
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
            "- 从上面的 solution_steps 中选出 **至少 3 个关键阶段** 各配一张图,\n"
            "  并在每张图的 caption_cn 中明确写出对应的 step 编号 (例如 “对应解答 step 2”)。\n"
            "- 若某些 step 的 viz_ref 已给出, 优先为它们生成, id 必须同名。\n"
            "- pitfalls 中的分类讨论/临界情形必须有一张可切换或可拖动的对比图覆盖。\n"
            "- visualizations 数量 3-4 个; 绝不可交 1-2 个, 也不要超过 4 个。\n"
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
            + "\n\n## AnswerPackage (不含 visualizations)\n"
            + json.dumps(answer_package, indent=2, ensure_ascii=False)
            + steps_block
            + pitfalls_block
            + coverage_hint
            + "\n请基于上面的 AnswerPackage 生成 3-4 个交互式可视化。"
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
