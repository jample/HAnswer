"""JSON Schema definitions for all LLM output contracts (§7.1.5).

Single source of truth for the JSON structure the LLM must produce.
Embedded verbatim in system prompts so the LLM sees the exact schema.
Pydantic models in `app.schemas.llm` mirror these and perform runtime
validation (repair loop).
"""

from __future__ import annotations

from copy import deepcopy

from app.schemas.llm import (
    GeoGebraExecutionPayload,
    GeoGebraExecutionPayloadDraft,
)
from app.schemas.visualization_spec import VisualizationSpec, VisualizationSpecBundle


def _compact_schema_for_gemini(schema: dict) -> dict:
    """Remove high-state JSON Schema constraints that Gemini rejects.

    Gemini response_json_schema is more fragile than runtime Pydantic validation.
    For large nested contracts like VisualizationSpecBundle, keep the object shape
    and required fields, but strip enums, numeric/string bounds, and verbose
    metadata. The strict semantic checks still run later via model_validate_json().
    """

    STRIP_KEYS = {
        "title",
        "description",
        "default",
        "examples",
        "enum",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "pattern",
        "format",
    }

    def _visit(node, *, inside_properties: bool = False):
        if isinstance(node, dict):
            compact: dict = {}
            for key, value in node.items():
                # Inside a "properties" dict the keys are real field names,
                # not JSON Schema metadata — keep them even if they collide
                # (e.g. a model field literally named "title").
                if not inside_properties and key in STRIP_KEYS:
                    continue
                compact[key] = _visit(value, inside_properties=(key == "properties"))
            return compact
        if isinstance(node, list):
            return [_visit(item) for item in node]
        return node

    return _visit(deepcopy(schema))

# ── ParsedQuestion ──────────────────────────────────────────────────

PARSED_QUESTION_SCHEMA: dict = {
    "type": "object",
    "description": "Structured problem information parsed by Gemini from a problem image.",
    "required": [
        "subject", "grade_band", "topic_path", "question_text",
        "given", "find", "difficulty", "confidence",
    ],
    "properties": {
        "subject": {
            "type": "string", "enum": ["math", "physics"],
            "description": "Subject: math or physics.",
        },
        "grade_band": {
            "type": "string", "enum": ["junior", "senior"],
            "description": "Grade band: junior=middle school (grades 7-9), senior=high school (grades 10-12).",
        },
        "topic_path": {
            "type": "array", "items": {"type": "string"},
            "description": "Knowledge-point path from coarse to fine, for example ['几何', '三角形', '全等三角形'].",
        },
        "question_text": {
            "type": "string",
            "description": "Full problem text. Math expressions should use LaTeX wrapped in $...$.",
        },
        "given": {
            "type": "array", "items": {"type": "string"},
            "description": "List of given conditions, one fact per string. May contain LaTeX.",
        },
        "find": {
            "type": "array", "items": {"type": "string"},
            "description": "List of target unknowns / tasks to solve.",
        },
        "diagram_description": {
            "type": "string",
            "description": "Text description of the figure / diagram in the problem. Empty string if there is no figure.",
        },
        "difficulty": {
            "type": "integer", "minimum": 1, "maximum": 5,
            "description": "Difficulty level: 1 basic, 2 easy, 3 medium, 4 hard, 5 competition / olympiad style.",
        },
        "tags": {
            "type": "array", "items": {"type": "string"},
            "description": "Free-form tags, for example ['辅助线', '分类讨论'].",
        },
        "confidence": {
            "type": "number", "minimum": 0, "maximum": 1,
            "description": "Overall parsing confidence. The UI may ask for confirmation when it is below 0.5.",
        },
    },
    "additionalProperties": False,
}

# ── AnswerPackage ───────────────────────────────────────────────────

ANSWER_PACKAGE_SCHEMA: dict = {
    "type": "object",
    "description": "Teaching-oriented answer package. The primary deliverable is the reusable method pattern, not just the numeric answer.",
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
            "description": "Key bottlenecks / common mistakes in the problem so the student knows where the real difficulty lies.",
        },
        "solution_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["step_index", "statement", "rationale", "why_this_step"],
                "properties": {
                    "step_index": {"type": "integer"},
                    "statement": {"type": "string"},
                    "rationale": {"type": "string", "description": "Why this step is valid."},
                    "formula": {"type": "string"},
                    "why_this_step": {
                        "type": "string",
                        "description": "Why this method / step is chosen instead of another one. This is the core teaching field.",
                    },
                    "viz_ref": {"type": "string", "description": "Suggested visualization id for this step (optional)."},
                },
            },
        },
        "key_points_of_answer": {
            "type": "array", "items": {"type": "string"},
            "description": "Core conclusions / insights the student should retain after reading the answer.",
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
            "description": "Reusable solving pattern. This is the most important teaching deliverable in the app.",
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
            "description": "Exactly 3 same-pattern questions: one easier, one same level, one harder.",
        },
        "knowledge_points": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["node_ref", "weight"],
                "properties": {
                    "node_ref": {
                        "type": "string",
                        "description": "Existing id or a newly suggested path in the format 'new:path', for example 'new:二次函数>顶点式'.",
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
            "description": "Suggested short title for the current session. Return an empty string when no update is needed.",
        },
        "assistant_reply": {
            "type": "string",
            "description": "Final reply shown to the user. Must be in Simplified Chinese and may contain Markdown and LaTeX.",
        },
        "follow_up_suggestions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "0-3 suggested follow-up directions for the user.",
        },
        "memory": {
            "type": "object",
            "required": ["summary", "key_facts", "open_questions"],
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Rolling summary of the current conversation for next-turn context compression.",
                },
                "key_facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Stable facts, conclusions, user preferences, or constraints worth preserving across turns.",
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Questions that remain unresolved and may need follow-up later.",
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
                "Rendering engine. The server chooses the default preference by configuration. Currently supported values are 'geogebra' (GeoGebra Apps API) and 'jsxgraph'."
            ),
        },
        "ggb_commands": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "List of GeoGebra command strings (required when engine=geogebra), executed in order via ggbApplet.evalCommand(). Each entry must be one full command such as 'f(x)=x^2', 'A=(1,2)', 'C=Circle((0,0),1)', 'a=Slider(-3,3,0.1)', 'SetAnimating(a,true)', or 'StartAnimation()'. Command names must be English. Only object / style / animation commands belong here; view controls such as SetCoordSystem / SetGridVisible / SetAxesVisible / SetPerspective belong in ggb_settings. Prefer short ASCII object labels. If an interactive parameter appears in params, define only the same-name object here and do not initialize it again with SetValue(name, value); use params[].default instead. Avoid underscores and Chinese labels. No newline characters inside a single command. Max 512 characters per command, max 64 commands total."
            ),
        },
        "ggb_settings": {
            "type": "object",
            "description": "GeoGebra applet configuration (optional when engine=geogebra).",
            "properties": {
                "app_name": {
                    "type": "string",
                    "enum": ["graphing", "geometry", "3d", "classic", "suite"],
                    "description": (
                        "Which GeoGebra app to use: classic is the recommended default for most 2D problems, geometry for plane construction, graphing for pure function / coordinate plots, 3d for solid geometry or 3D physics, suite for multi-view use cases."
                    ),
                },
                "perspective": {"type": "string"},
                "coord_system": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Visible coordinate range: use [xmin, xmax, ymin, ymax] for 2D, or 6 numbers for 3D.",
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
                "JSXGraph render function body (required when engine=jsxgraph, otherwise empty string). Provide only the function body itself, not an outer wrapper such as `function(board, JXG, H, params) { ... }`. Allowed globals are board, JXG, H, params, Math, Number, Array, Object, Boolean, String, JSON, console, requestAnimationFrame, and cancelAnimationFrame. Forbidden globals include window, document, fetch, XMLHttpRequest, WebSocket, Worker, eval, Function, import, string-based setTimeout / setInterval, and with. Return { update(params), destroy() } or undefined."
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
                    "Frontend interaction parameters. name must match a same-name slider / toggle object already defined in ggb_commands. default is the initial value. Do not generate extra SetValue(name, value) commands."
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
            "minItems": 2,
            "maxItems": 2,
            "description": (
                "For exam-oriented middle-school / high-school teaching, generate exactly 2 visualizations focused on the two most important learning bottlenecks, key stages, case splits, or final conclusion in the answer."
            ),
        },
    },
    "additionalProperties": False,
}


VISUALIZATION_STORYBOARD_SCHEMA: dict = {
    "type": "object",
    "required": [
        "theme_cn",
        "selection_rationale_cn",
        "symbol_map",
        "shared_params",
        "coverage_summary",
        "sequence",
        "items",
    ],
    "properties": {
        "theme_cn": {"type": "string"},
        "selection_rationale_cn": {"type": "string"},
        "symbol_map": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["symbol", "meaning_cn"],
                "properties": {
                    "symbol": {"type": "string"},
                    "meaning_cn": {"type": "string"},
                    "source_ref": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "shared_params": {
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
                "additionalProperties": False,
            },
        },
        "coverage_summary": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["item_id", "summary_cn", "anchor_refs"],
                "properties": {
                    "item_id": {"type": "string"},
                    "summary_cn": {"type": "string"},
                    "anchor_refs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["kind", "ref"],
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "question_given",
                                        "solution_step",
                                        "formula",
                                        "pitfall",
                                        "final_answer",
                                        "method_pattern",
                                    ],
                                },
                                "ref": {"type": "string"},
                                "excerpt_cn": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
        "sequence": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 2,
        },
        "items": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": {
                "type": "object",
                "required": [
                    "id",
                    "title_cn",
                    "anchor_refs",
                    "difficulty_reason_cn",
                    "student_confusion_risk",
                    "conceptual_jump_cn",
                    "why_visualization_needed_cn",
                    "learning_goal_cn",
                    "engine",
                    "shared_symbols",
                    "shared_params",
                    "depends_on",
                    "caption_outline_cn",
                    "geo_target_cn",
                ],
                "properties": {
                    "id": {"type": "string"},
                    "title_cn": {"type": "string"},
                    "anchor_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["kind", "ref"],
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "question_given",
                                        "solution_step",
                                        "formula",
                                        "pitfall",
                                        "final_answer",
                                        "method_pattern",
                                    ],
                                },
                                "ref": {"type": "string"},
                                "excerpt_cn": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "difficulty_reason_cn": {"type": "string"},
                    "student_confusion_risk": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "conceptual_jump_cn": {"type": "string"},
                    "why_visualization_needed_cn": {"type": "string"},
                    "learning_goal_cn": {"type": "string"},
                    "engine": {
                        "type": "string",
                        "enum": ["geogebra", "jsxgraph"],
                    },
                    "shared_symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "shared_params": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "relation_to_prev_cn": {"type": "string"},
                    "relation_to_next_cn": {"type": "string"},
                    "caption_outline_cn": {"type": "string"},
                    "geo_target_cn": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


VISUALIZATION_SPEC_SCHEMA: dict = _compact_schema_for_gemini(
    VisualizationSpec.model_json_schema()
)

VISUALIZATION_SPEC_BUNDLE_SCHEMA: dict = _compact_schema_for_gemini(
    VisualizationSpecBundle.model_json_schema()
)

GEOGEBRA_EXECUTION_PAYLOAD_SCHEMA: dict = _compact_schema_for_gemini(
    GeoGebraExecutionPayload.model_json_schema()
)
GEOGEBRA_EXECUTION_PAYLOAD_DRAFT_SCHEMA: dict = _compact_schema_for_gemini(
    GeoGebraExecutionPayloadDraft.model_json_schema()
)


# ── Variant synthesis (M7 practice exams, §3.5) ─────────────────────

VARIANT_QUESTION_SCHEMA: dict = {
    "type": "object",
    "description": (
        "A new question that preserves the given method_pattern while changing surface features such as numbers, named objects, or context. Used to fill practice sets when the local bank is insufficient."
    ),
    "required": [
        "statement", "answer_outline", "rubric",
        "difficulty", "same_pattern",
    ],
    "properties": {
        "statement": {
            "type": "string",
            "description": "Full text of the new question. Use LaTeX wrapped in $...$ for formulas.",
        },
        "answer_outline": {
            "type": "string",
            "description": "Outline of the answer: key steps only, not necessarily a full solution.",
        },
        "rubric": {
            "type": "string",
            "description": "Scoring rubric: key credit points / common mistakes, in about 3-5 lines.",
        },
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
        "same_pattern": {
            "type": "boolean",
            "description": "Must be true. A variant is not allowed to change the solving pattern.",
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
