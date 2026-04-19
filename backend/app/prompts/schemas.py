"""JSON Schema definitions for all LLM output contracts (§7.1.5).

Single source of truth for the JSON structure the LLM must produce.
Embedded verbatim in system prompts so the LLM sees the exact schema.
Pydantic models in `app.schemas.llm` mirror these and perform runtime
validation (repair loop).
"""

from __future__ import annotations

# ── ParsedQuestion ──────────────────────────────────────────────────

PARSED_QUESTION_SCHEMA: dict = {
    "type": "object",
    "description": "Gemini 从题目图片中解析出的结构化题目信息",
    "required": [
        "subject", "grade_band", "topic_path", "question_text",
        "given", "find", "difficulty", "confidence",
    ],
    "properties": {
        "subject": {
            "type": "string", "enum": ["math", "physics"],
            "description": "学科: math=数学, physics=物理",
        },
        "grade_band": {
            "type": "string", "enum": ["junior", "senior"],
            "description": "学段: junior=初中(7-9年级), senior=高中(10-12年级)",
        },
        "topic_path": {
            "type": "array", "items": {"type": "string"},
            "description": "知识点分类路径, 由粗到细, 如 ['几何','三角形','全等三角形']",
        },
        "question_text": {
            "type": "string",
            "description": "完整题目文本, 数学公式用 LaTeX 并以 $ 包裹",
        },
        "given": {
            "type": "array", "items": {"type": "string"},
            "description": "已知条件列表, 每条一条, 可含 LaTeX",
        },
        "find": {
            "type": "array", "items": {"type": "string"},
            "description": "求解目标列表",
        },
        "diagram_description": {
            "type": "string",
            "description": "题目中图形/示意图的文字描述; 没有图则为空字符串",
        },
        "difficulty": {
            "type": "integer", "minimum": 1, "maximum": 5,
            "description": "难度: 1基础 2偏易 3中等 4偏难 5竞赛/压轴",
        },
        "tags": {
            "type": "array", "items": {"type": "string"},
            "description": "自由标签, 如 ['辅助线','分类讨论']",
        },
        "confidence": {
            "type": "number", "minimum": 0, "maximum": 1,
            "description": "整体解析置信度, 低于 0.5 时会触发 UI 确认",
        },
    },
    "additionalProperties": False,
}

# ── AnswerPackage ───────────────────────────────────────────────────

ANSWER_PACKAGE_SCHEMA: dict = {
    "type": "object",
    "description": "教学型答案包; 方法模式是首要产出, 数值答案次之",
    "required": [
        "question_understanding",
        "key_points_of_question",
        "solution_steps",
        "key_points_of_answer",
        "method_pattern",
        "similar_questions",
        "knowledge_points",
        "self_check",
    ],
    "properties": {
        "question_understanding": {
            "type": "object",
            "required": ["restated_question", "givens", "unknowns", "implicit_conditions"],
            "properties": {
                "restated_question": {"type": "string"},
                "givens": {"type": "array", "items": {"type": "string"}},
                "unknowns": {"type": "array", "items": {"type": "string"}},
                "implicit_conditions": {"type": "array", "items": {"type": "string"}},
            },
        },
        "key_points_of_question": {
            "type": "array", "items": {"type": "string"},
            "description": "这道题的难点/易错点 (让学生知道'难在哪')",
        },
        "solution_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["step_index", "statement", "rationale", "why_this_step"],
                "properties": {
                    "step_index": {"type": "integer"},
                    "statement": {"type": "string"},
                    "rationale": {"type": "string", "description": "这一步为什么成立"},
                    "formula": {"type": "string"},
                    "why_this_step": {
                        "type": "string",
                        "description": "为什么选这个方法而非其他 (教学核心)",
                    },
                    "viz_ref": {"type": "string", "description": "对应可视化 id (可选)"},
                },
            },
        },
        "key_points_of_answer": {
            "type": "array", "items": {"type": "string"},
            "description": "学生必须掌握的核心结论/洞见",
        },
        "method_pattern": {
            "type": "object",
            "required": [
                "pattern_id_suggested", "name_cn", "when_to_use",
                "general_procedure", "pitfalls",
            ],
            "properties": {
                "pattern_id_suggested": {"type": "string"},
                "name_cn": {"type": "string"},
                "when_to_use": {"type": "string"},
                "general_procedure": {"type": "array", "items": {"type": "string"}},
                "pitfalls": {"type": "array", "items": {"type": "string"}},
            },
            "description": "解题方法模式 — 本应用最核心的教学产出",
        },
        "similar_questions": {
            "type": "array", "minItems": 3, "maxItems": 3,
            "items": {
                "type": "object",
                "required": [
                    "statement", "answer_outline",
                    "same_pattern", "difficulty_delta",
                ],
                "properties": {
                    "statement": {"type": "string"},
                    "answer_outline": {"type": "string"},
                    "same_pattern": {"type": "boolean"},
                    "difficulty_delta": {"type": "integer", "minimum": -2, "maximum": 2},
                },
            },
            "description": "3 道同类题: 一易一同一难, 用相同方法模式",
        },
        "knowledge_points": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["node_ref", "weight"],
                "properties": {
                    "node_ref": {
                        "type": "string",
                        "description": "已有 id 或 'new:路径' (如 'new:二次函数>顶点式')",
                    },
                    "weight": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "self_check": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

# ── Multi-turn dialog memory ────────────────────────────────────────

CONVERSATION_TURN_RESULT_SCHEMA: dict = {
    "type": "object",
    "required": ["assistant_reply", "follow_up_suggestions", "memory"],
    "properties": {
        "title_suggested": {
            "type": "string",
            "description": "为当前会话建议的简短标题; 若不需要更新则返回空字符串",
        },
        "assistant_reply": {
            "type": "string",
            "description": "给用户的最终回答, 使用简体中文, 可包含 Markdown 和 LaTeX",
        },
        "follow_up_suggestions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "建议用户继续追问的 0-3 个方向",
        },
        "memory": {
            "type": "object",
            "required": ["summary", "key_facts", "open_questions"],
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "当前对话的滚动摘要, 用于下一轮上下文压缩",
                },
                "key_facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "应跨轮保留的稳定事实、结论、用户偏好或约束",
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "仍待澄清或后续要继续回答的问题",
                },
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}

# ── Visualization ───────────────────────────────────────────────────

VISUALIZATION_SCHEMA: dict = {
    "type": "object",
    "required": ["id", "title_cn", "caption_cn", "learning_goal", "engine"],
    "properties": {
        "id": {"type": "string"},
        "title_cn": {"type": "string"},
        "caption_cn": {"type": "string"},
        "learning_goal": {"type": "string"},
        "interactive_hints": {"type": "array", "items": {"type": "string"}},
        "helpers_used": {"type": "array", "items": {"type": "string"}},
        "engine": {
            "type": "string",
            "enum": ["geogebra", "jsxgraph"],
            "description": (
                "渲染引擎。优先使用 'geogebra' (GeoGebra Apps API), 仅在"
                "GeoGebra 命令无法表达时退回到 'jsxgraph'。"
            ),
        },
        "ggb_commands": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "GeoGebra 命令字符串列表 (engine=geogebra 时必须), 按顺序传给"
                "ggbApplet.evalCommand()。每条一个完整命令, 例如:"
                " 'f(x)=x^2', 'A=(1,2)', 'C=Circle((0,0),1)',"
                " 'a=Slider(-3,3,0.1)', 'SetAnimating(a,true)',"
                " 'StartAnimation()'。命令名必须是英文。"
                " 这里只放创建对象/样式/动画命令; SetCoordSystem /"
                " SetGridVisible / SetAxesVisible / SetPerspective 这类视图或"
                " 布局控制应写入 ggb_settings。对象标签尽量使用简短 ASCII 名称,"
                " 若某个交互参数出现在 params 中, 这里只定义同名对象, 不要"
                " 再写 SetValue(name, value) 初始化; 初始值统一放到"
                " params[].default。"
                " 避免下划线和中文。"
                "禁止换行符 (一行一个命令); 单条命令最长 512 字符; 总数 ≤ 64。"
            ),
        },
        "ggb_settings": {
            "type": "object",
            "description": "GeoGebra applet 配置 (engine=geogebra 时可选)。",
            "properties": {
                "app_name": {
                    "type": "string",
                    "enum": ["graphing", "geometry", "3d", "classic", "suite"],
                    "description": (
                        "选哪个 GeoGebra app: classic=大多数 2D 题推荐默认值,"
                        " geometry=平面几何作图, graphing=纯函数/坐标图,"
                        " 3d=立体几何/3D 物理, suite=多视图。"
                    ),
                },
                "perspective": {"type": "string"},
                "coord_system": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "可视区: 2D 给 [xmin,xmax,ymin,ymax], 3D 给 6 个数。",
                },
                "axes_visible": {"type": "boolean"},
                "grid_visible": {"type": "boolean"},
                "show_algebra_input": {"type": "boolean"},
                "show_tool_bar": {"type": "boolean"},
                "show_menu_bar": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "jsx_code": {
            "type": "string",
            "description": (
                "JSXGraph 渲染函数体 (engine=jsxgraph 时必须, 否则留空字符串)。"
                "签名: function(board, JXG, H, params) { ... }。"
                "仅可用: board, JXG, H, params, Math, Number, Array, Object, "
                "Boolean, String, JSON, console, requestAnimationFrame, cancelAnimationFrame。"
                "禁止: window, document, fetch, XMLHttpRequest, WebSocket, Worker, "
                "eval, Function, import, setTimeout(字符串), setInterval(字符串), with。"
                "返回 { update(params), destroy() } 或 undefined。"
            ),
        },
        "params": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "label_cn", "kind", "default"],
                "properties": {
                    "name": {"type": "string"},
                    "label_cn": {"type": "string"},
                    "kind": {"type": "string", "enum": ["slider", "toggle"]},
                    "min": {"type": "number"},
                    "max": {"type": "number"},
                    "step": {"type": "number"},
                    "default": {},
                },
                "description": (
                    "前端交互参数。name 必须对应 ggb_commands 中已经定义的同名"
                    "滑块/开关对象; default 是其初始值。不要额外生成"
                    "SetValue(name, value) 命令。"
                ),
            },
        },
        "animation": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["loop", "once"]},
                "duration_ms": {"type": "integer"},
                "drives": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "additionalProperties": False,
}

VISUALIZATION_LIST_SCHEMA: dict = {
    "type": "object",
    "required": ["visualizations"],
    "properties": {
        "visualizations": {
            "type": "array",
            "items": VISUALIZATION_SCHEMA,
            "minItems": 3,
            "maxItems": 4,
            "description": (
                "面向中学应考场景, 3-4 个可视化; 需覆盖解答中不同"
                "的关键阶段/分类讨论/最终结论。"
            ),
        },
    },
    "additionalProperties": False,
}


# ── Variant synthesis (M7 practice exams, §3.5) ─────────────────────

VARIANT_QUESTION_SCHEMA: dict = {
    "type": "object",
    "description": (
        "保留给定方法模式 (method_pattern) 但改变表面特征 (数值/命名对象/情境)"
        "的新题, 用于在题库不足时填充练习卷。"
    ),
    "required": [
        "statement", "answer_outline", "rubric",
        "difficulty", "same_pattern",
    ],
    "properties": {
        "statement": {
            "type": "string",
            "description": "新题目完整题面, 公式用 LaTeX 并以 $ 包裹",
        },
        "answer_outline": {
            "type": "string",
            "description": "答案要点, 步骤提纲 (不必完整解答)",
        },
        "rubric": {
            "type": "string",
            "description": "评分提示: 关键得分点 / 易错点, 3-5 行",
        },
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
        "same_pattern": {
            "type": "boolean",
            "description": "必须为 true; 变体不能换方法模式",
        },
    },
    "additionalProperties": False,
}

VARIANT_LIST_SCHEMA: dict = {
    "type": "object",
    "required": ["variants"],
    "properties": {
        "variants": {"type": "array", "items": VARIANT_QUESTION_SCHEMA},
    },
    "additionalProperties": False,
}
