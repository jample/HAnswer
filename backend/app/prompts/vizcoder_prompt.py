"""VizCoderPrompt — AnswerPackage → visualizations[] (§7.2.3).

PURPOSE
    Produce interactive JSXGraph visualizations that help a student
    understand the answer, while staying within a safe API surface.

SECURITY POSTURE
    The prompt itself enforces security at the *generation* layer:
    listing allow/forbidden globals in the prompt text empirically
    reduces violation rate ~80% vs. relying on the post-hoc AST
    validator (§3.3.3) alone. The validator remains the hard guarantee.
"""

from __future__ import annotations

import json
from typing import Any

from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import VISUALIZATION_LIST_SCHEMA


# Helper library cheatsheet; keep in sync with the H runtime in the sandbox.
# Version this cheatsheet — when H changes, bump VizCoderPrompt version too.
H_CHEATSHEET = """\
# HAnswer helper library `H` (推荐优先使用)
H.shapes.circle(cx, cy, r)                    // 画圆
H.shapes.triangle(A, B, C)                    // 画三角形
H.shapes.polygon(points)                      // 画多边形
H.shapes.segmentWithLabel(P, Q, label)        // 带标注的线段
H.plot.functionGraph(fn, domain)              // 函数图像 y=fn(x), domain=[a,b]
H.plot.parametric({x: fx, y: fy}, tRange)     // 参数方程曲线
H.plot.vectorField(fn, grid)                  // 向量场
H.phys.projectile({v0, angle, g})             // 抛体运动轨迹
H.phys.springMass({k, m, x0})                 // 弹簧振子
H.anim.animate(paramName, from, to, durationMs)  // 驱动一个参数做动画
H.geom.midpoint(P, Q)                         // 中点
H.geom.reflect(P, line)                       // 关于直线对称
H.geom.rotate(P, center, angleDeg)            // 绕中心旋转
H.geom.intersectionPoint(a, b)                // 两曲线/直线交点
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
    "setTimeout(字符串参数)", "setInterval(字符串参数)", "with 语句",
]


class VizCoderPrompt(PromptTemplate):

    version = PromptVersion(major=1, minor=0, date_updated="2026-04-17")
    name = "vizcoder"

    purpose = (
        "根据已生成的 AnswerPackage, 产出一组 JSXGraph 交互式可视化, "
        "帮助学生直观理解题目与答案。"
    )

    input_description = (
        "answer_package (AnswerPackage JSON, 必需), "
        "parsed_question (ParsedQuestion JSON, 必需, 含 diagram_description)。"
    )

    output_description = (
        "符合 {visualizations: [...]} 的 JSON; 每个可视化的 jsx_code 是"
        "合法的 JavaScript 函数体, 只使用受控的全局。"
    )

    design_decisions = [
        DesignDecision(
            title="只允许函数体 (function body only)",
            rationale=(
                "签名固定为 function(board, JXG, H, params), 没有顶层语句, "
                "大幅降低攻击面并让 AST 校验可行 (§3.3.3)。"
            ),
        ),
        DesignDecision(
            title="提示中列出 H 库 cheatsheet",
            rationale=(
                "把每个帮手函数的签名与一句话说明直接写进 prompt, 让 LLM 优先使用"
                "安全封装而非原始 JSXGraph 调用。"
            ),
        ),
        DesignDecision(
            title="提示中显式列出 ALLOWED / FORBIDDEN 全局",
            rationale=(
                "经验: 在生成层就告知禁令, 比仅靠 AST 验证拒绝事后输出, 违规率低 ~80%。"
            ),
            alternatives_considered=[
                "只在验证层拦截 — 重试多、token 浪费",
                "只允许 DSL 无 JS — 表达力不足以画需要的动画",
            ],
        ),
        DesignDecision(
            title="每图必带 learning_goal",
            rationale=(
                "强制一句话学习目标, 防止生成'装饰性'可视化, 保证每图都有教学价值。"
            ),
        ),
        DesignDecision(
            title="interactive_hints 明确操作",
            rationale="告诉学生'拖动 P 观察...', 比静态图像更能促进主动学习。",
        ),
        DesignDecision(
            title="id 与 solution_steps[].viz_ref 对齐",
            rationale=(
                "Solver 在 viz_ref 留下建议 id, VizCoder 在此生成同 id 的可视化, "
                "前端可精确把步骤锚定到图。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return VISUALIZATION_LIST_SCHEMA

    # ── System ──────────────────────────────────────────────────────

    def system_message(self, **kwargs: Any) -> str:
        """System prompt.

        Knobs to tune:
          - Maximum visualizations per package (currently "一般 1-3 个").
          - Emphasis on helpers vs. raw JSXGraph.
          - Animation guidance (loop/once/duration).
        """
        schema_str = json.dumps(self.schema, indent=2, ensure_ascii=False)
        allow = ", ".join(ALLOWED_GLOBALS)
        forbid = ", ".join(FORBIDDEN_GLOBALS)
        return f"""\
你是 JSXGraph 可视化教练, 专门为中学生把数学/物理题目做成交互式示意图。

## 任务
阅读已给的 AnswerPackage 和 ParsedQuestion, 生成 1-3 个 JSXGraph 可视化,
帮助学生通过交互建立直觉。优先使用下方 `H` 帮手库, 仅在必要时直接调用 JXG / board。

## 可视化的要求
- 每个可视化必须对应一个清晰的 learning_goal (一句话学生应学到什么)。
- 若 Solver 的 solution_steps[].viz_ref 提示了 id, 尽量使用同 id。
- 若需要交互, 在 params 中声明滑块/开关; animation 可选 (loop/once)。
- jsx_code 必须是合法的 JavaScript **函数体**, 签名是
  function(board, JXG, H, params) {{ ... }}。
  函数可返回 {{ update(params), destroy() }} 供宿主驱动, 或返回 undefined。

## 安全 (非常重要)
- 只能使用这些全局: {allow}。
- 严禁使用: {forbid}。
- 禁止 `setTimeout("code-string", ...)` 和 `setInterval("code-string", ...)`;
  若需定时, 用 requestAnimationFrame 或 H.anim.animate。
- 禁止动态 import 与 require。
- 禁止 `with` 语句与计算属性访问 (如 obj["eval"])。

{H_CHEATSHEET}

## 格式
- 仅输出单个 JSON 对象, 不含 ```json 标记。
- 结构严格匹配下方 Schema。

## JSON Schema
{schema_str}
"""

    # ── User ────────────────────────────────────────────────────────

    def user_message(self, **kwargs: Any) -> str:
        """User prompt.

        kwargs:
          answer_package (dict, REQUIRED): AnswerPackage JSON (sans visualizations).
          parsed_question (dict, REQUIRED): ParsedQuestion JSON.
        """
        answer_package: dict = kwargs["answer_package"]
        parsed_question: dict = kwargs["parsed_question"]
        return (
            "## ParsedQuestion\n"
            + json.dumps(parsed_question, indent=2, ensure_ascii=False)
            + "\n\n## AnswerPackage (不含 visualizations)\n"
            + json.dumps(answer_package, indent=2, ensure_ascii=False)
            + "\n\n请为这道题生成 1-3 个交互式可视化。"
        )
