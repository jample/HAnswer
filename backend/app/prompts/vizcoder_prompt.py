"""VizCoderPrompt v3 — GeoGebra-first interactive visualizations."""

from __future__ import annotations

import json
from typing import Any

from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import VISUALIZATION_LIST_SCHEMA


GGB_CHEATSHEET = """\
# GeoGebra 命令速查 (engine="geogebra" 推荐)
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

## 4. 几何 / 微积分 / 代数
Intersect(f, g)
Tangent(A, c)
Derivative(f)
Integral(f, 0, 1)
Solve(x^2-2*x-3=0, x)

## 5. 物理 / 动画
a=Slider(-3, 3, 0.1)
SetAnimating(a, true)
SetAnimationSpeed(a, 1)
StartAnimation()
SetTrace(P, true)

## 5.5 交互参数约定
- ggb_commands 只负责“定义对象”, 例如 `a=Slider(-3,3,0.1)`、`flag=false`。
- 不要在 ggb_commands 中写 `SetValue(a, 1.2)` / `SetValue(flag, true)`。
- 初始值写到 params[].default; 前端会在 GeoGebra 对象创建后自动同步 default。
- 若某个滑块/开关出现在 params 里, ggb_commands 中必须有同名定义。

## 5.6 条件显示 (重要)
- 不要使用 `SetConditionToShowObject(obj, expr)` —— 它是 GUI 属性命令,
  Apps API 的 evalCommand 不接受。
- 用条件定义实现“开关切换显示”: 把对象本身写成 `If(...)` 形式,
  条件为假时对象未定义, GeoGebra 自动不绘制。示例:
  `polyMaxTop=If(isMin==false, Polygon((0,1),(6,1),(6,3),(0,3)))`
  `lMin1=If(isMin, Line((0, 2*sqrt(6)-3), (2*sqrt(6), -3)))`
- 互斥的两组对象 (如最大情形 / 最小情形), 用同一布尔条件分别写两组
  `If(...)`, 学生切换 toggle 时整组对象消失/出现。

## 6. 文字 / LaTeX
SetCaption(A, "起点")
ShowLabel(A, true)
SetColor(c1, 255, 0, 0)
SetLineStyle(l1, 2)
SetLineThickness(l1, 4)

## 7. 视图 / 界面 (优先放进 ggb_settings, 不要写进 ggb_commands)
ggb_settings = {
    "app_name": "classic",
    "perspective": "G",
    "coord_system": [-5, 5, -3, 3],
    "axes_visible": true,
    "grid_visible": true,
    "show_algebra_input": false,
    "show_tool_bar": false,
    "show_menu_bar": false
}

## 命令规范
- 名字开头小写为变量, 大写为命令 (a=Slider(...), 不是 slider=...)。
- 对象标签尽量用短 ASCII 名称, 避免下划线/中文/空格, 如 c1, l1, P, Q。
- 依赖点的位移必须写 `P=(x(K)+dx, y(K)+dy)` (坐标表达式),
  禁止 `P=K+(dx,dy)` 与 `Translate(K, Vector(...))`。
- Vector 写 `Vector((dx,dy))` (单个点元组) 或 `Vector(P,Q)` (两点);
  禁止 `Vector((dx),(dy))` (两个标量参数)。
- `Line(...)` 只能用 GeoGebra 支持的签名: `Line(Point,Point)`、
    `Line(Point,Vector)`、`Line(Point,ParallelLine)`。
    禁止 `Line(x+y=c)` / `Line(ax+by=c)` 这种把方程包进 Line(...) 的写法;
    这在 Apps API 中会失败。请改写成两点式或点+方向向量式。
- 样式命令 `SetColor(obj, 255, 0, 0)`, `SetLineStyle(obj, 2)`,
  `SetLineThickness(obj, 3)`, `SetPointSize(obj, 5)` —— SetColor 必须用 RGB 三元组,
  禁止色名字符串。
- 角度可写 30deg 或 pi/6。
- JSON 字符串中的反斜杠要双重转义。
- 不要写 ggbApplet.evalCommand(...), 仅写命令本身。
"""

H_CHEATSHEET = """\
# JSXGraph helper `H` (engine="jsxgraph" 时使用)
H.shapes.circle(cx, cy, r)
H.shapes.triangle(A, B, C)
H.shapes.polygon(points)
H.plot.functionGraph(fn, domain)
H.plot.parametric({x: fx, y: fy}, tRange)
H.phys.projectile({v0, angle, g})
H.anim.animate(paramName, from_, to, durationMs)
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


class VizCoderPrompt(PromptTemplate):

    version = PromptVersion(major=3, minor=0, date_updated="2026-04-19")
    name = "vizcoder"

    purpose = (
        "根据已生成的 AnswerPackage, 产出一组数学/物理交互式可视化, "
        '默认使用 GeoGebra (engine="geogebra")。'
    )

    input_description = (
        "answer_package (AnswerPackage JSON, 必需), "
        "parsed_question (ParsedQuestion JSON, 必需)。"
    )

    output_description = (
        "符合 {visualizations: [...]} 的 JSON; 每个可视化指定 engine, 并提供"
        "ggb_commands (geogebra) 或 jsx_code (jsxgraph) 之一。"
    )

    design_decisions = [
        DesignDecision(
            title="默认使用 GeoGebra Apps API",
            rationale=(
                "GeoGebra 输出命令字符串而非 JS 代码, 由 GeoGebra 运行时解释, "
                "无 eval, 安全性高于 JSXGraph; 同时提供原生 LaTeX、自动几何"
                "约束、内建动画与专业渲染, 更贴合中学数学教学场景。"
            ),
            alternatives_considered=[
                "纯 JSXGraph (旧方案) — 表达力弱, 渲染观感差, JS 注入风险",
                "纯 SVG/Canvas DSL — 缺动画与交互",
            ],
        ),
        DesignDecision(
            title="保留 JSXGraph 为 fallback",
            rationale="复杂自定义物理仿真等不易用 GeoGebra 表达。",
        ),
        DesignDecision(
            title="提示中提供 GeoGebra 命令速查",
            rationale="降低 LLM 凭记忆胡乱拼写命令名的概率。",
        ),
        DesignDecision(
            title="服务端轻量校验 ggb_commands",
            rationale="GeoGebra 命令由运行时解释, 无需 AST 校验; 仅做长度/数量检查。",
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
                "提示中显式要求模型阅读 solution_steps / key_insight / formulas /"
                "pitfalls, 并优先为这些已写出的关键步骤配图, 防止生成与解答"
                "无关的装饰性示意图。"
            ),
        ),
        DesignDecision(
            title="符号一致性: 复用题目/解答中的命名",
            rationale=(
                "若可视化引入与解答不同的滑块名 (例如解答用 t, 可视化造一个 "
                "tParam) 学生需要做心智映射, 增加认知负担。"
                "提示要求 VizCoder 先扫描 ParsedQuestion 与 AnswerPackage 中"
                "已经命名的对象/参数, 直接在 ggb_commands 中沿用同名; 仅在"
                "希腊字母与 GeoGebra 内置标识冲突时, 通过 SetCaption 把 GUI"
                "标签恢复成希腊字母, 而不是改变学生看到的符号。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return VISUALIZATION_LIST_SCHEMA

    def system_message(self, **kwargs: Any) -> str:
        schema_str = json.dumps(self.schema, indent=2, ensure_ascii=False)
        allow = ", ".join(ALLOWED_GLOBALS)
        forbid = ", ".join(FORBIDDEN_GLOBALS)
        body = """\
你是数学/物理交互式可视化设计师, 为中学生把题目做成专业的可交互示意图。
你有两种渲染引擎可选, 默认必须使用 GeoGebra:

## 引擎选择 (重要)
- engine="geogebra" — 默认, 优先使用。
  - 数学绘图 (函数/曲线/几何/向量/圆锥曲线/立体几何) 全部用 GeoGebra。
  - 物理题中可用滑块驱动的动力学示意 (抛体, 弹簧, 简谐) 也用 GeoGebra。
  - 输出 ggb_commands: ["...", "..."], 每条一个 GeoGebra 命令字符串;
    jsx_code 留空字符串。
    - ggb_settings.app_name 选: classic (大多数 2D 题默认), geometry (平面几何),
        graphing (纯函数/坐标图), 3d (立体几何/3D 运动)。
    - 默认令 ggb_settings.perspective="G"; 3D 题用 "T"。不要打开 Algebra/
        Tools/Table 面板, 不要生成额外表格型界面。
  - 通过 Slider(...) 创建滑块; SetAnimating(s, true) + StartAnimation() 触发动画。
    - 滑块/开关的初始值放进 params[].default, 不要在 ggb_commands 里写 SetValue(...)
        去二次赋值。
    - 视图范围、网格、坐标轴放进 ggb_settings.coord_system / grid_visible /
        axes_visible, 不要把 SetCoordSystem / SetGridVisible / SetAxesVisible 写进
        ggb_commands。

- engine="jsxgraph" — 仅在 GeoGebra 无法表达时使用。
  输出 jsx_code (函数体), ggb_commands 留空数组。

## 通用要求
- 输入包含 ParsedQuestion 与 **完整的 AnswerPackage**。可视化必须服务于该
  解答, 帮助学生理解解题过程, 而不是凭题目自由发挥。
- **符号一致性 (重要)**: 可视化中的几何对象、点、参数必须复用题目和解答中
  已经出现的符号。先扫描 ParsedQuestion.given / find / question_text 与
  AnswerPackage.solution_steps / formulas / final_answer, 列出题面/解答中
  已经登场的命名对象 (例如 `T`, `K`, `P`, `Q`, `O`, `t`, `r`), 然后在
  ggb_commands 里直接沿用这些名字。
  - 不要为同一个对象起新名 (例如把题目里的圆心 `K` 改写成 `k1`)。
  - 不要凭空引入题目/解答里没有的字母作为滑块名 (例如题目用参数 `t`,
    就不要再造一个 `tParam` 滑块)。
  - params[].label_cn 用题目/解答里的中文术语 ("参数 t", "动点 P 位置")。
  - caption_cn / interactive_hints 中描述的滑块名必须与 ggb_commands 里
    一致, 让学生一眼对上 "这个滑块就是解答第 N 步里的 t"。
- 在动笔之前, 先按下列顺序通读 AnswerPackage:
  1. method_pattern / key_insight — 决定整组图的主题。
  2. solution_steps[] — 找出最关键、最难想象的 2-3 步, 为它们各配一张图;
     若该步已有 viz_ref, 沿用同一 id, caption_cn 中复述该步的核心结论。
  3. formulas — 出现的关键公式 (函数表达式、几何关系、向量关系) 应在图中
     可视化体现 (画出函数曲线、几何对象、向量等)。
  4. pitfalls — 若有易错点适合用图说明 (例如分类讨论的两种情形、定义域
     边界、临界角等), 优先做成可切换/可拖动的对比图。
  5. answer_summary / final_answer — 图中标注最终结果 (交点坐标、距离、
     极值点等), 让学生在图上直接看到答案。
- **必须生成 3-4 个可视化** (面向中学应考场景; 需覆盖解答中
  不同的关键阶段)。
  - 需要为 AnswerPackage.solution_steps[] 中出现的不同关键阶段各配一张图,
    每个关键阶段只能出现一次, 不要重复。
  - 若题目含分类讨论 (pitfalls / 有多个情形), 必须为每种情形提供对应的
    可视化, 要么拆成多张图, 要么用可切换的同一张图。
  - 每图必须有清晰的 learning_goal (一句话, 与所配步骤的目标一致)。
- 若需要交互, 在 params 中声明滑块/开关。
  - 对 GeoGebra: params 中 slider 应与 ggb_commands 中 name=Slider(...) 同名,
    前端会通过 setValue() 把控件值同步进 GeoGebra。
- caption_cn 用简体中文一句话说明该图如何对应解答中的某一步; 可含 LaTeX,
  用 $...$ 包裹。
- interactive_hints 给学生明确的操作建议 ("拖动滑块 a 观察判别式符号变化")。
- 严禁生成与 AnswerPackage 中任何步骤、公式或结论无关的可视化。

## GeoGebra 命令规范 (engine=geogebra 时)
- 仅使用英文命令名 (Circle, Slider, Vector, Polygon, Tangent, ...)。
- 一行一个命令, 不要含换行或 ggbApplet 前缀。
- ggb_commands 只放创建对象/样式/动画的命令; 视图/坐标轴/网格/视角放进 ggb_settings,
  禁止在 ggb_commands 中出现 SetCoordSystem / SetAxesVisible / SetGridVisible /
  SetPerspective / ShowAxes / ShowGrid。
- **优先复用题目/解答里出现的符号**(见上方"符号一致性"); 仅当必须额外引入
  辅助参数时才新增, 并在 caption_cn 里说明它的含义。
- 变量/对象名禁止使用希腊字母英文别名 (alpha, beta, gamma, delta, theta, phi, ...);
  GeoGebra 会把它们自动重命名为 `beta_1` 等, 后续 `cos(beta)` 全部解析失败。
  若解答里写了 $\\alpha$/$\\beta$/$\\theta$, 在 ggb_commands 中改写为
  `aAng`/`bAng`/`tAng` 这类 ASCII 名, 并通过
  `SetCaption(aAng, "α")` 把希腊字母作为显示标签呈现给学生 ——
  ggb_commands 中的标识符与界面/解答里看到的符号一致, 只是改了底层名。
- 对象名不要覆盖 GeoGebra 内置名 (`xAxis`, `yAxis`, `zAxis`, `xOyPlane`,
  `xOzPlane`, `yOzPlane`, 常量 `e`, `i`)。要画辅助轴请另起名如 `lineX=Line((0,0),(1,0))`,
  要显示坐标轴直接依靠 ggb_settings.axes_visible 即可。
- 依赖另一个点的位移必须写坐标式: `P=(x(K)+dx, y(K)+dy)`。
  禁止 `P=K+(dx,dy)` 简写, 也禁止 `Translate(K, Vector(...))` (Apps API 内联 Vector 解析不稳定)。
- Vector 仅接受单个点元组或两点: 写 `Vector((dx,dy))` 或 `Vector(P,Q)`。
  禁止 `Vector((dx),(dy))` (两个标量, GeoGebra 拒绝)。
- `Line` 仅使用 GeoGebra 官方支持签名: `Line(P,Q)`、`Line(P,Vector((dx,dy)))`、
    `Line(P, existingLine)`。禁止 `Line(ax+by=c)` / `Line(x+y=p1)` 这类方程包装;
    要画隐式方程对应的直线时, 请直接给出两点式或点+方向向量式。
- SetColor 必须用 RGB 三元组: `SetColor(obj, 255, 0, 0)`。禁止 `SetColor(obj, "Red")` 等色名。
- 单条命令 ≤ 512 字符, 总命令数 ≤ 64。
- 禁止在 ggb_commands 中出现 `SetValue(name, value)`。
    - 定义交互对象时写 `name=Slider(...)` 或 `name=true/false`。
    - 初始值统一写入 params[].default, 前端会在对象创建后调用 GeoGebra API 同步。
- 禁止在 ggb_commands 中出现 `SetConditionToShowObject(name, expr)`。
    - 改为条件定义 `name=If(expr, OriginalDefinition)`。条件为假时对象未定义,
      GeoGebra 自动隐藏, 等价于显示条件。
- 上述规则会在后端被严格校验; 任意违例都会触发整段 JSON 重新生成。

__GGB__

## JSXGraph 后备规范 (engine=jsxgraph 时)
- jsx_code 是合法的 JavaScript 函数体, 签名 function(board, JXG, H, params) { ... }。
- 安全: 仅可使用全局 __ALLOW__; 严禁使用 __FORBID__。
- 禁止字符串形式的 setTimeout/setInterval, 禁止 import/require/with。

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
            "- pitfalls 中的分类讨论/临界情形必须有一张可切换的对比图覆盖,\n"
            "  不能漏。\n"
            "- visualizations 数量 3-4 个; 绝不可交 1–2 个, 也不要超过 4 个。\n"
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
            + "\n请基于上面的 AnswerPackage 生成 3-4 个交互式可视化"
            + ' (默认 engine="geogebra")。'
            + "\n要求:"
            + "\n- 在写命令前, 先列出 ParsedQuestion / AnswerPackage 中已经"
            + "出现的几何对象与参数名, ggb_commands 必须直接复用这些名字, "
            + "不要为同一对象起新名, 也不要凭空引入新滑块。"
            + "\n- 每个可视化都必须显式对应 AnswerPackage.solution_steps 中的"
            + "某一步 (在 caption_cn 中复述该步要点)。"
            + "\n- 优先为 key_insight、出现的 formulas 以及 pitfalls 中需要图"
            + "示的情形配图。"
            + "\n- 在图中标注 final_answer / answer_summary 中给出的关键数值"
            + "或几何对象, 让学生看到答案如何在图上呈现。"
            + "\n- 若 solution_steps[].viz_ref 已写出 id, 沿用同 id 以便前端"
            + "把图锚定到该步骤。"
            + "\n- 对于 params 中的 slider/toggle, ggb_commands 里只定义同名对象,"
            + "不要再写 SetValue(name, ...); 初始值放到 params[].default。"
        )
