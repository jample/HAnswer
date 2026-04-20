"""Prompt Template framework tests (§11.1 verification).

Verifies for every registered prompt:
  - explain() contains ≥3 design decisions with title + rationale;
  - preview() renders without raising and includes system / user sections;
  - build() returns at least [system, user];
  - trace_tag() exposes name + version;
  - diff_preview() produces a diff when kwargs change;
  - call_structured() round-trips a valid JSON via FakeTransport;
  - call_structured() triggers the repair loop on first invalid JSON
    and succeeds on the second attempt.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.config import settings
from app.prompts import PromptRegistry
from app.prompts.solver_prompt import _load_fewshot_examples
from app.schemas import ParsedQuestion
from app.services.llm_client import FakeTransport, GeminiClient, LLMError

# ---- registry-wide invariants -----------------------------------------

def test_registry_has_core_prompts():
    names = PromptRegistry.names()
    assert {"dialog", "parser", "solver", "vizplanner", "vizitem", "vizcoder"}.issubset(set(names))


@pytest.mark.parametrize("name", ["dialog", "parser", "solver", "vizcoder", "vizitem"])
def test_prompt_has_design_decisions(name: str):
    t = PromptRegistry.get(name)
    assert len(t.design_decisions) >= 3, "must document ≥3 design decisions"
    for d in t.design_decisions:
        assert d.title and d.rationale, "each decision needs title + rationale"


@pytest.mark.parametrize("name", ["dialog", "parser", "solver", "vizcoder", "vizitem"])
def test_explain_is_stable(name: str):
    t = PromptRegistry.get(name)
    text = t.explain()
    assert t.name in text
    assert "DESIGN DECISIONS" in text
    # each design decision must appear in the rendered explanation
    for d in t.design_decisions:
        assert d.title in text


# ---- preview / build --------------------------------------------------

SAMPLE_KWARGS: dict[str, dict] = {
    "dialog": {
        "session_title": "二次函数追问",
        "question_context": {
            "question_id": "q-1",
            "parsed_question": {"question_text": "求抛物线顶点", "given": [], "find": []},
        },
        "summary": "用户已经理解配方法, 但不确定顶点坐标怎么读。",
        "key_facts": ["题目围绕二次函数顶点式展开"],
        "open_questions": ["顶点坐标与对称轴如何快速读取"],
        "recent_messages": [
            {"role": "user", "content": "为什么要配方?"},
            {"role": "assistant", "content": "因为这样能把式子转成顶点式。"},
        ],
        "user_message": "那顶点坐标怎么从式子里直接看出来?",
    },
    "parser": {"raw_ocr": "已知 a=3, b=4, 求斜边长。"},
    "solver": {
        "parsed_question": {
            "subject": "math",
            "grade_band": "high",
            "stem_text": "已知 a=3, b=4, 求斜边长。",
            "givens": [{"symbol": "a", "value": "3"}, {"symbol": "b", "value": "4"}],
            "unknowns": ["c"],
            "figures": [],
            "candidate_kps": [],
        },
        "existing_patterns": [],
        "existing_kps": [],
    },
    "vizcoder": {
        "parsed_question": {"stem_text": "直角三角形斜边长。"},
        "answer_package": {
            "question_understanding": {"restated_goal": "求 c"},
            "solution_steps": [],
            "method_pattern": None,
        },
    },
    "vizplanner": {
        "parsed_question": {
            "question_text": "已知二次函数图像与 x 轴交点, 求最值。",
            "given": ["顶点在第一象限", "与 x 轴交于 A,B"],
            "find": ["最小值"],
        },
        "answer_package": {
            "question_understanding": {
                "restated_question": "求最值",
                "givens": [],
                "unknowns": [],
                "implicit_conditions": [],
            },
            "key_points_of_question": ["顶点位置", "与 x 轴交点关系"],
            "solution_steps": [],
            "key_points_of_answer": ["先构造函数图像", "再比较极值"],
            "method_pattern": {
                "pattern_id_suggested": "p1",
                "name_cn": "图像法",
                "when_to_use": "涉及二次函数最值",
                "general_procedure": ["画图", "找顶点"],
                "pitfalls": ["忽略定义域"],
            },
            "similar_questions": [
                {"statement": "s1", "answer_outline": "a1"},
                {"statement": "s2", "answer_outline": "a2"},
                {"statement": "s3", "answer_outline": "a3"},
            ],
            "knowledge_points": [{"node_ref": "kp:quad", "weight": 1.0}],
            "self_check": ["顶点坐标是否正确"],
        },
    },
    "vizitem": {
        "parsed_question": {
            "question_text": "已知二次函数图像与 x 轴交点, 求最值。",
            "given": ["顶点在第一象限", "与 x 轴交于 A,B"],
            "find": ["最小值"],
        },
        "answer_package": {
            "question_understanding": {
                "restated_question": "求最值",
                "givens": [],
                "unknowns": [],
                "implicit_conditions": [],
            },
            "key_points_of_question": ["顶点位置", "与 x 轴交点关系"],
            "solution_steps": [
                {
                    "step_index": 1,
                    "statement": "画出函数草图并标出交点 A,B。",
                    "rationale": "先把文字条件变成图形对象。",
                    "formula": "",
                    "why_this_step": "交点关系决定后续最值判断",
                    "viz_ref": "viz-1",
                }
            ],
            "key_points_of_answer": ["先构造函数图像", "再比较极值"],
            "method_pattern": {
                "pattern_id_suggested": "p1",
                "name_cn": "图像法",
                "when_to_use": "涉及二次函数最值",
                "general_procedure": ["画图", "找顶点"],
                "pitfalls": ["忽略定义域"],
            },
            "similar_questions": [
                {"statement": "s1", "answer_outline": "a1"},
                {"statement": "s2", "answer_outline": "a2"},
                {"statement": "s3", "answer_outline": "a3"},
            ],
            "knowledge_points": [{"node_ref": "kp:quad", "weight": 1.0}],
            "self_check": ["顶点坐标是否正确"],
        },
        "storyboard": {
            "theme_cn": "从图像关系到最值结论",
            "selection_rationale_cn": "选择三个关键跳跃",
            "symbol_map": [{"symbol": "A", "meaning_cn": "交点 A"}],
            "shared_params": [{"name": "t", "label_cn": "参数 t", "kind": "slider", "default": 0, "min": -2, "max": 2, "step": 0.1}],
            "coverage_summary": [{"item_id": "viz-1", "summary_cn": "画出交点", "anchor_refs": [{"kind": "question_given", "ref": "given:0"}]}],
            "sequence": ["viz-1", "viz-2", "viz-3"],
            "items": [
                {
                    "id": "viz-1",
                    "title_cn": "交点示意",
                    "anchor_refs": [{"kind": "question_given", "ref": "given:0"}],
                    "difficulty_reason_cn": "条件不容易映射到图像。",
                    "student_confusion_risk": "high",
                    "conceptual_jump_cn": "从题设到对象",
                    "why_visualization_needed_cn": "帮助建立图像对象",
                    "learning_goal_cn": "理解交点位置",
                    "engine": "geogebra",
                    "shared_symbols": ["A"],
                    "shared_params": ["t"],
                    "depends_on": [],
                    "caption_outline_cn": "对应解答 step 1 的交点建立。",
                    "geo_target_cn": "显示抛物线和 x 轴交点",
                },
                {
                    "id": "viz-2",
                    "title_cn": "顶点比较",
                    "anchor_refs": [{"kind": "solution_step", "ref": "2"}],
                    "difficulty_reason_cn": "极值位置不直观。",
                    "student_confusion_risk": "medium",
                    "conceptual_jump_cn": "从交点到顶点",
                    "why_visualization_needed_cn": "需要补出顶点",
                    "learning_goal_cn": "理解顶点与最值",
                    "engine": "geogebra",
                    "shared_symbols": ["A"],
                    "shared_params": ["t"],
                    "depends_on": ["viz-1"],
                    "caption_outline_cn": "对应极值比较。",
                    "geo_target_cn": "显示顶点",
                },
                {
                    "id": "viz-3",
                    "title_cn": "结论回扣",
                    "anchor_refs": [{"kind": "final_answer", "ref": "final_answer"}],
                    "difficulty_reason_cn": "学生只记答案。",
                    "student_confusion_risk": "medium",
                    "conceptual_jump_cn": "从观察到结论",
                    "why_visualization_needed_cn": "帮助回扣结论",
                    "learning_goal_cn": "理解最终结论",
                    "engine": "geogebra",
                    "shared_symbols": ["A"],
                    "shared_params": ["t"],
                    "depends_on": ["viz-2"],
                    "caption_outline_cn": "对应最终答案。",
                    "geo_target_cn": "标出证据",
                },
            ],
        },
        "storyboard_item": {
            "id": "viz-1",
            "title_cn": "交点示意",
            "anchor_refs": [{"kind": "question_given", "ref": "given:0"}],
            "difficulty_reason_cn": "条件不容易映射到图像。",
            "student_confusion_risk": "high",
            "conceptual_jump_cn": "从题设到对象",
            "why_visualization_needed_cn": "帮助建立图像对象",
            "learning_goal_cn": "理解交点位置",
            "engine": "geogebra",
            "shared_symbols": ["A"],
            "shared_params": ["t"],
            "depends_on": [],
            "caption_outline_cn": "对应解答 step 1 的交点建立。",
            "geo_target_cn": "显示抛物线和 x 轴交点"
        },
        "previous_items": [],
    },
}


@pytest.mark.parametrize("name", ["dialog", "parser", "solver", "vizcoder", "vizitem"])
def test_preview_renders(name: str):
    t = PromptRegistry.get(name)
    out = t.preview(**SAMPLE_KWARGS[name])
    assert "[SYSTEM]" in out
    assert "[USER]" in out
    assert "OUTPUT SCHEMA" in out


@pytest.mark.parametrize("name", ["dialog", "parser", "solver", "vizcoder", "vizitem"])
def test_build_has_system_and_user(name: str):
    t = PromptRegistry.get(name)
    msgs = t.build(**SAMPLE_KWARGS[name])
    roles = [m["role"] for m in msgs]
    assert roles[0] == "system"
    assert roles[-1] == "user"


def test_solver_loads_curated_fewshot_examples():
    examples = _load_fewshot_examples(subject="math", grade_band="senior")
    assert examples, "expected curated few-shot examples on disk"
    assert any(ex.get("topic_prefix") == ["代数", "一元二次方程"] for ex in examples)


def test_solver_selects_topic_matched_fewshot_examples():
    t = PromptRegistry.get("solver")
    msgs = t.fewshot_examples(parsed_question={
        "subject": "math",
        "grade_band": "senior",
        "topic_path": ["代数", "一元二次方程", "因式分解"],
        "question_text": "解 $x^2-5x+6=0$",
        "given": [],
        "find": [],
        "diagram_description": "",
        "difficulty": 2,
        "tags": [],
        "confidence": 0.9,
    })
    assert len(msgs) >= 2
    assert msgs[0]["role"] == "user"
    assert "因式分解法" in msgs[1]["content"]


@pytest.mark.parametrize("name", ["dialog", "parser", "solver", "vizcoder", "vizitem"])
def test_trace_tag(name: str):
    t = PromptRegistry.get(name)
    tag = t.trace_tag()
    assert tag["prompt_name"] == name
    assert tag["prompt_version"].startswith("v")


def test_diff_preview_shows_changes():
    t = PromptRegistry.get("parser")
    diff = t.diff_preview(
        old_kwargs={"subject_hint": "math"},
        new_kwargs={"subject_hint": "physics"},
    )
    assert "-" in diff and "+" in diff


def test_vizcoder_prompt_uses_geogebra_by_default():
    t = PromptRegistry.get("vizcoder")
    preview = t.preview(**SAMPLE_KWARGS["vizcoder"])
    assert '当前服务端配置的默认引擎, 优先使用。' in preview
    assert 'engine="geogebra"' in preview


def test_vizcoder_prompt_switches_with_config(monkeypatch):
    t = PromptRegistry.get("vizcoder")
    old = settings.viz.default_engine
    monkeypatch.setattr(settings.viz, "default_engine", "jsxgraph")
    try:
        preview = t.preview(**SAMPLE_KWARGS["vizcoder"])
    finally:
        monkeypatch.setattr(settings.viz, "default_engine", old)
    assert 'engine="jsxgraph"' in preview
    assert '当前默认引擎: JSXGraph。优先输出 engine="jsxgraph"' in preview


def test_vizplanner_prompt_prefers_bottlenecks_over_fixed_steps():
    t = PromptRegistry.get("vizplanner")
    preview = t.preview(**SAMPLE_KWARGS["vizplanner"])
    assert "先识别学生最可能卡住的 conceptual bottlenecks" in preview
    assert "不要机械地按 step 1/2/3/4 平铺" in preview
    assert "不输出 ggb_commands / jsx_code" in preview
    assert "不输出任何 GeoGebra 命令或 JSXGraph 代码" in preview


def test_vizitem_prompt_locks_to_single_storyboard_item():
    t = PromptRegistry.get("vizitem")
    preview = t.preview(**SAMPLE_KWARGS["vizitem"])
    assert "只输出一个 Visualization JSON 对象" in preview
    assert "`id` 必须与 storyboard_item.id 完全一致" in preview
    assert '当前这一项默认应输出 engine="geogebra"' in preview


# ---- GeminiClient round-trip via FakeTransport -----------------------


_VALID_PARSED = {
    "subject": "math",
    "grade_band": "senior",
    "topic_path": ["几何", "三角形"],
    "question_text": "已知 a=3, b=4, 求斜边长。",
    "given": ["a=3", "b=4"],
    "find": ["c"],
    "diagram_description": "",
    "difficulty": 2,
    "tags": [],
    "confidence": 0.9,
}


def test_call_structured_happy_path():
    transport = FakeTransport(json_by_model={"gemini-2.0-flash": json.dumps(_VALID_PARSED)})
    client = GeminiClient(transport)
    parser = PromptRegistry.get("parser")
    result = asyncio.run(
        client.call_structured(
            template=parser,
            model="gemini-2.0-flash",
            model_cls=ParsedQuestion,
            template_kwargs={"raw_ocr": "已知 a=3, b=4, 求斜边长。"},
        )
    )
    assert isinstance(result, ParsedQuestion)
    assert result.find == ["c"]
    assert len(transport.calls) == 1  # no repair needed


def test_call_structured_happy_path_streaming():
    transport = FakeTransport(json_by_model={"gemini-2.0-flash": json.dumps(_VALID_PARSED)})
    client = GeminiClient(transport)
    parser = PromptRegistry.get("parser")
    result = asyncio.run(
        client.call_structured(
            template=parser,
            model="gemini-2.0-flash",
            model_cls=ParsedQuestion,
            template_kwargs={"raw_ocr": "已知 a=3, b=4, 求斜边长。"},
            stream=True,
            timeout_s=91,
        )
    )
    assert isinstance(result, ParsedQuestion)
    assert result.find == ["c"]
    assert len(transport.calls) == 1
    assert transport.calls[0]["stream"] is True


class _RepairTransport(FakeTransport):
    """First call returns bad JSON, second returns valid."""

    def __init__(self, bad: str, good: str) -> None:
        super().__init__()
        self._responses = [bad, good]

    async def generate_json(self, *, model, messages, response_schema, timeout_s):
        self.calls.append({"model": model, "messages": messages})
        raw = self._responses.pop(0) if self._responses else "{}"
        return raw, 0, 0


def test_call_structured_repair_loop_recovers():
    transport = _RepairTransport(bad="{}", good=json.dumps(_VALID_PARSED))
    client = GeminiClient(transport)
    parser = PromptRegistry.get("parser")
    result = asyncio.run(
        client.call_structured(
            template=parser,
            model="gemini-2.0-flash",
            model_cls=ParsedQuestion,
            template_kwargs={"raw_ocr": "q"},
        )
    )
    assert isinstance(result, ParsedQuestion)
    assert len(transport.calls) == 2  # one repair round-trip


def test_call_structured_gives_up_after_max_attempts():
    transport = FakeTransport(json_by_model={"gemini-2.0-flash": "{}"})
    client = GeminiClient(transport)
    parser = PromptRegistry.get("parser")
    with pytest.raises(LLMError):
        asyncio.run(
            client.call_structured(
                template=parser,
                model="gemini-2.0-flash",
                model_cls=ParsedQuestion,
                template_kwargs={"raw_ocr": "q"},
            )
        )
