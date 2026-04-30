"""GeoGebra render-engine support for VizCoder (M4+).

Covers:
  - Schema accepts ``engine="geogebra"`` payloads with ``ggb_commands``
    and an empty ``jsx_code``.
  - Schema still accepts legacy ``engine="jsxgraph"`` payloads (default).
  - The Pydantic ``Visualization`` validator catches GeoGebra anti-patterns
    (view directives in commands, ``P=K+(...)`` shorthand, ``Vector((a),(b))``,
    color names in ``SetColor``, oversized payloads). Failures raise
    ``ValidationError`` so the LLM repair loop can fix them.
  - ``_persist_viz`` writes legacy columns while the main answer-resume
    serializer exposes only the GeoGebra-first spec/execution-payload shape.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.config import settings
from app.db import models
from app.db.models import VisualizationRow
from app.schemas.llm import (
    GgbSettings,
    StoryboardCoverageEntry,
    StoryboardSymbol,
    Visualization,
    VisualizationAnchorRef,
    VisualizationList,
    VisualizationStoryboard,
    VisualizationStoryboardItem,
)
from app.services.answer_job_service import _serialize_viz_row
from app.services.llm_client import FakeTransport, GeminiClient, LLMError
from app.services.geogebra_validator import (
    GeoGebraValidationError,
    GeoGebraValidationReport,
    sanitize_geogebra_visualization,
)
from app.services.question_solution_service import bootstrap_solution_from_question
from app.services.vizcoder_service import (
    _generate_visualization_for_storyboard_item,
    _persist_viz,
    generate_visualizations,
    generate_visualizations_batch,
    plan_visualization_storyboard,
)


@pytest.fixture(autouse=True)
def _stub_geogebra_runtime_validator(monkeypatch):
    async def _ok(viz, *, timeout_s=20.0):
        return GeoGebraValidationReport(ok=True, render_ms=1.0)

    monkeypatch.setattr("app.services.vizcoder_service.validate_geogebra_visualization", _ok)


def _seed_question(session, marker: str) -> uuid.UUID:
    q = models.Question(
        parsed_json={
            "subject": "math",
            "grade_band": "senior",
            "topic_path": [],
            "question_text": marker,
            "given": [],
            "find": [],
            "diagram_description": "",
            "difficulty": 2,
            "tags": [],
            "confidence": 0.9,
        },
        subject="math",
        grade_band="senior",
        difficulty=2,
        dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
        seen_count=1,
        status="parsed",
    )
    session.add(q)
    return q  # type: ignore[return-value]


def _answer_package_json() -> dict:
    return {
        "question_understanding": {
            "restated_question": "求最值",
            "givens": ["抛物线与 x 轴交于 A,B"],
            "unknowns": ["最小值"],
            "implicit_conditions": [],
        },
        "key_points_of_question": ["交点与顶点关系"],
        "solution_steps": [
            {
                "step_index": 1,
                "statement": "画出函数草图并标出交点 A,B。",
                "rationale": "先把题设转成图像对象。",
                "formula": "",
                "why_this_step": "后续最值判断依赖图像位置关系。",
                "viz_ref": "viz-1",
            },
            {
                "step_index": 2,
                "statement": "根据顶点位置判断最值。",
                "rationale": "顶点决定最值。",
                "formula": "",
                "why_this_step": "最值与顶点直接相关。",
                "viz_ref": "viz-2",
            },
        ],
        "key_points_of_answer": ["先构造图像", "再看顶点"],
        "method_pattern": {
            "pattern_id_suggested": "p1",
            "name_cn": "图像法",
            "when_to_use": "求二次函数最值",
            "general_procedure": ["画图", "看顶点"],
            "pitfalls": ["忽略定义域"],
        },
        "similar_questions": [
            {"statement": "s1", "answer_outline": "a1"},
            {"statement": "s2", "answer_outline": "a2"},
            {"statement": "s3", "answer_outline": "a3"},
        ],
        "knowledge_points": [{"node_ref": "kp:quad", "weight": 1.0}],
        "self_check": ["顶点坐标是否正确"],
    }


class _SequenceTransport(FakeTransport):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self._responses = list(responses)

    async def generate_json(self, *, model, messages, response_schema, timeout_s):
        self.calls.append({"model": model, "messages": messages})
        raw = self._responses.pop(0)
        return raw, 0, 0

    async def generate_json_stream(self, *, model, messages, response_schema, timeout_s):
        self.calls.append({"model": model, "messages": messages, "stream": True})
        raw = self._responses.pop(0)
        return raw, 0, 0


def _storyboard_payload() -> dict:
    return {
        "theme_cn": "从图像关系到最值结论",
        "selection_rationale_cn": "选择两个关键跳跃。",
        "symbol_map": [
            {"symbol": "A", "meaning_cn": "交点A"},
            {"symbol": "B", "meaning_cn": "交点B"},
        ],
        "shared_params": [],
        "coverage_summary": [],
        "sequence": ["viz-1", "viz-2"],
        "items": [
            {
                "id": "viz-1",
                "title_cn": "图1",
                "anchor_refs": [{"kind": "question_given", "ref": "given:0", "excerpt_cn": ""}],
                "difficulty_reason_cn": "难点1",
                "student_confusion_risk": "medium",
                "conceptual_jump_cn": "跳跃1",
                "why_visualization_needed_cn": "需要图示1",
                "learning_goal_cn": "目标1",
                "engine": "geogebra",
                "shared_symbols": ["A"],
                "shared_params": [],
                "depends_on": [],
                "relation_to_prev_cn": "",
                "relation_to_next_cn": "过渡",
                "caption_outline_cn": "说明1",
                "geo_target_cn": "目标1",
            },
            {
                "id": "viz-2",
                "title_cn": "图2",
                "anchor_refs": [{"kind": "solution_step", "ref": "1", "excerpt_cn": ""}],
                "difficulty_reason_cn": "难点2",
                "student_confusion_risk": "medium",
                "conceptual_jump_cn": "跳跃2",
                "why_visualization_needed_cn": "需要图示2",
                "learning_goal_cn": "目标2",
                "engine": "geogebra",
                "shared_symbols": ["A", "B"],
                "shared_params": [],
                "depends_on": ["viz-1"],
                "relation_to_prev_cn": "承接",
                "relation_to_next_cn": "",
                "caption_outline_cn": "说明2",
                "geo_target_cn": "目标2",
            },
        ],
    }


def _single_viz_payload() -> dict:
    return {
        "id": "viz-1",
        "title_cn": "图1",
        "caption_cn": "说明1",
        "learning_goal": "目标1",
        "interactive_hints": [],
        "helpers_used": [],
        "engine": "geogebra",
        "jsx_code": "",
        "ggb_commands": ["A=(0,0)", "c=Circle(A,1)"],
        "ggb_settings": {"app_name": "graphing"},
        "params": [],
        "animation": None,
    }


# ── Schema: GeoGebra payload ───────────────────────────────────────


def test_visualization_accepts_geogebra_engine():
    viz = Visualization(
        id="v1",
        title_cn="单位圆",
        caption_cn="$x^2+y^2=1$",
        learning_goal="理解单位圆",
        engine="geogebra",
        ggb_commands=[
            "c=Circle((0,0),1)",
            "P=Point(c)",
        ],
        ggb_settings=GgbSettings(
            app_name="graphing",
            coord_system=[-2, 2, -2, 2],
        ),
    )
    assert viz.engine == "geogebra"
    assert viz.jsx_code == ""
    assert viz.ggb_settings is not None
    assert viz.ggb_settings.app_name == "graphing"
    assert viz.ggb_settings.coord_system == [-2, 2, -2, 2]


def test_visualization_legacy_jsxgraph_default():
    viz = Visualization(
        id="v1",
        title_cn="t",
        caption_cn="c",
        learning_goal="g",
        jsx_code="board.create('point',[0,0]);",
    )
    # Engine defaults to jsxgraph for backward-compat with stored payloads.
    assert viz.engine == "jsxgraph"
    assert viz.ggb_commands == []
    assert viz.ggb_settings is None


def test_visualization_rejects_empty_jsxgraph_code():
    with pytest.raises(ValidationError, match="requires non-empty jsx_code"):
        Visualization(
            id="v1",
            title_cn="t",
            caption_cn="c",
            learning_goal="g",
            engine="jsxgraph",
            jsx_code="",
        )


def test_visualization_rejects_whitespace_only_jsxgraph_code():
    with pytest.raises(ValidationError, match="requires non-empty jsx_code"):
        Visualization(
            id="v1",
            title_cn="t",
            caption_cn="c",
            learning_goal="g",
            engine="jsxgraph",
            jsx_code="   \n  ",
        )


def test_visualization_list_round_trips_geogebra():
    def _v(vid, cmd):
        return {
            "id": vid,
            "title_cn": "抛物线",
            "caption_cn": "$y=x^2$",
            "learning_goal": "看顶点平移",
            "engine": "geogebra",
            "ggb_commands": [cmd],
        }

    payload = {
        "visualizations": [
            _v("v1", "a=Slider(-3,3,0.1)"),
            _v("v2", "f(x)=x^2"),
        ]
    }
    parsed = VisualizationList.model_validate(payload)
    assert parsed.visualizations[0].engine == "geogebra"
    # Serializes back to a dict with the new fields present.
    dumped = parsed.model_dump(mode="json")
    v0 = dumped["visualizations"][0]
    assert v0["engine"] == "geogebra"
    assert v0["ggb_commands"] == ["a=Slider(-3,3,0.1)"]
    assert v0["jsx_code"] == ""


def test_visualization_list_accepts_single_visualization():
    payload = {
        "visualizations": [
            {
                "id": "v1",
                "title_cn": "t",
                "caption_cn": "c",
                "learning_goal": "g",
                "engine": "geogebra",
                "ggb_commands": ["A=(0,0)"],
            }
        ]
    }
    parsed = VisualizationList.model_validate(payload)
    assert len(parsed.visualizations) == 1


def test_visualization_storyboard_accepts_valid_sequence():
    storyboard = VisualizationStoryboard(
        theme_cn="从图像关系到最值结论",
        selection_rationale_cn="选择两个最难直接脑补的关键跳跃。",
        symbol_map=[
            StoryboardSymbol(symbol="A", meaning_cn="与 x 轴交点 A"),
            StoryboardSymbol(symbol="B", meaning_cn="与 x 轴交点 B"),
        ],
        shared_params=[],
        coverage_summary=[
            StoryboardCoverageEntry(
                item_id="viz-1",
                summary_cn="建立交点与函数图像关系",
                anchor_refs=[VisualizationAnchorRef(kind="question_given", ref="given:0")],
            ),
        ],
        sequence=["viz-1", "viz-2"],
        items=[
            VisualizationStoryboardItem(
                id="viz-1",
                title_cn="交点示意",
                anchor_refs=[VisualizationAnchorRef(kind="question_given", ref="given:0")],
                difficulty_reason_cn="学生难以将文字条件映射到图像。",
                student_confusion_risk="high",
                conceptual_jump_cn="从题设到图像对象的转换。",
                why_visualization_needed_cn="文字不足以直接呈现交点与顶点相对位置。",
                learning_goal_cn="理解图像基本对象。",
                shared_symbols=["A", "B"],
                shared_params=[],
                depends_on=[],
                caption_outline_cn="对应题设中的交点关系。",
                geo_target_cn="显示抛物线与 x 轴交点。",
            ),
            VisualizationStoryboardItem(
                id="viz-2",
                title_cn="最值比较",
                anchor_refs=[VisualizationAnchorRef(kind="solution_step", ref="1")],
                difficulty_reason_cn="极值位置不容易从文字一步看出。",
                student_confusion_risk="medium",
                conceptual_jump_cn="从交点信息推到顶点与最值。",
                why_visualization_needed_cn="需要图上对比。",
                learning_goal_cn="看见顶点与最值的对应。",
                shared_symbols=["A"],
                shared_params=[],
                depends_on=["viz-1"],
                relation_to_prev_cn="在交点基础上补出顶点。",
                caption_outline_cn="对应解答中的顶点推理。",
                geo_target_cn="显示顶点与最值位置。",
            ),
        ],
    )
    assert storyboard.sequence == ["viz-1", "viz-2"]


def test_visualization_storyboard_rejects_unknown_shared_symbol():
    with pytest.raises(ValidationError, match="unknown shared symbols"):
        VisualizationStoryboard(
            theme_cn="t",
            selection_rationale_cn="r",
            symbol_map=[StoryboardSymbol(symbol="A", meaning_cn="点 A")],
            shared_params=[],
            coverage_summary=[],
            sequence=["viz-1", "viz-2"],
            items=[
                VisualizationStoryboardItem(
                    id="viz-1",
                    title_cn="1",
                    anchor_refs=[VisualizationAnchorRef(kind="question_given", ref="g1")],
                    difficulty_reason_cn="d",
                    student_confusion_risk="high",
                    conceptual_jump_cn="c",
                    why_visualization_needed_cn="w",
                    learning_goal_cn="l",
                    shared_symbols=["B"],
                    shared_params=[],
                    depends_on=[],
                    caption_outline_cn="cap",
                    geo_target_cn="geo",
                ),
                VisualizationStoryboardItem(
                    id="viz-2",
                    title_cn="2",
                    anchor_refs=[VisualizationAnchorRef(kind="solution_step", ref="1")],
                    difficulty_reason_cn="d",
                    student_confusion_risk="medium",
                    conceptual_jump_cn="c",
                    why_visualization_needed_cn="w",
                    learning_goal_cn="l",
                    shared_symbols=[],
                    shared_params=[],
                    depends_on=[],
                    caption_outline_cn="cap",
                    geo_target_cn="geo",
                ),
            ],
        )


def test_visualization_storyboard_rejects_grouped_symbol_aliases():
    with pytest.raises(ValidationError, match="Do not combine symbols like 'P, Q'"):
        VisualizationStoryboard(
            theme_cn="t",
            selection_rationale_cn="r",
            symbol_map=[
                StoryboardSymbol(symbol="P, Q", meaning_cn="线段端点"),
                StoryboardSymbol(symbol="P', Q'", meaning_cn="平移后的端点"),
                StoryboardSymbol(symbol="⊙T", meaning_cn="目标圆"),
                StoryboardSymbol(symbol="k", meaning_cn="最远平移距离"),
                StoryboardSymbol(symbol="M", meaning_cn="中点"),
                StoryboardSymbol(symbol="M'", meaning_cn="平移后的中点"),
                StoryboardSymbol(symbol="r'", meaning_cn="弦心距"),
            ],
            shared_params=[],
            coverage_summary=[],
            sequence=["viz-1", "viz-2"],
            items=[
                VisualizationStoryboardItem(
                    id="viz-1",
                    title_cn="1",
                    anchor_refs=[VisualizationAnchorRef(kind="question_given", ref="g1")],
                    difficulty_reason_cn="d",
                    student_confusion_risk="high",
                    conceptual_jump_cn="c",
                    why_visualization_needed_cn="w",
                    learning_goal_cn="l",
                    shared_symbols=["P", "Q", "P'", "Q'", "r"],
                    shared_params=[],
                    depends_on=[],
                    caption_outline_cn="cap",
                    geo_target_cn="geo",
                ),
                VisualizationStoryboardItem(
                    id="viz-2",
                    title_cn="2",
                    anchor_refs=[VisualizationAnchorRef(kind="solution_step", ref="1")],
                    difficulty_reason_cn="d",
                    student_confusion_risk="medium",
                    conceptual_jump_cn="c",
                    why_visualization_needed_cn="w",
                    learning_goal_cn="l",
                    shared_symbols=[],
                    shared_params=[],
                    depends_on=[],
                    caption_outline_cn="cap",
                    geo_target_cn="geo",
                ),
            ],
        )


# ── Pydantic anti-pattern guards (drive the LLM repair loop) ──────


def _make_viz(commands):
    return Visualization(
        id="v",
        title_cn="t",
        caption_cn="c",
        learning_goal="g",
        engine="geogebra",
        ggb_commands=commands,
    )


def test_validator_passes_clean_commands():
    viz = _make_viz(["A=(1,2)", "B=(3,4)", "s=Segment(A,B)"])
    assert len(viz.ggb_commands) == 3


def test_validator_rejects_geogebra_with_no_commands():
    with pytest.raises(ValidationError, match="requires at least one ggb_command"):
        Visualization(
            id="v",
            title_cn="t",
            caption_cn="c",
            learning_goal="g",
            engine="geogebra",
        )


def test_validator_rejects_newline():
    with pytest.raises(ValidationError, match="newline"):
        _make_viz(["A=(1,2)\nB=(3,4)"])


def test_validator_rejects_oversized_command():
    with pytest.raises(ValidationError, match="chars"):
        _make_viz(["A=" + "x" * 600])


def test_validator_rejects_too_many_commands():
    with pytest.raises(ValidationError, match="too many commands"):
        _make_viz([f"P_{i}=({i},0)" for i in range(100)])


def test_validator_rejects_view_directive_in_commands():
    with pytest.raises(ValidationError, match="ggb_settings"):
        _make_viz(["SetCoordSystem(-2,2,-2,2)", "c=Circle((0,0),1)"])


def test_validator_rejects_point_plus_tuple_shorthand():
    with pytest.raises(ValidationError, match="shorthand"):
        _make_viz(["K=(0,0)", "P=K+(2*cos(t),2*sin(t))"])


def test_validator_rejects_vector_two_scalars():
    with pytest.raises(ValidationError, match="Vector"):
        _make_viz(["K=(0,0)", "v=Vector((cos(t)),(sin(t)))"])


def test_validator_rejects_translate_inline_vector():
    with pytest.raises(ValidationError, match="Translate"):
        _make_viz(["K=(0,0)", "P=Translate(K,Vector((cos(t),sin(t))))"])


def test_validator_rejects_setcolor_named():
    with pytest.raises(ValidationError, match="RGB triple"):
        _make_viz(["c=Circle((0,0),1)", 'SetColor(c,"Red")'])


def test_validator_accepts_setcolor_rgb_triple():
    viz = _make_viz(["c=Circle((0,0),1)", "SetColor(c, 255, 0, 0)"])
    assert len(viz.ggb_commands) == 2


def test_validator_rejects_setvalue_in_commands():
    with pytest.raises(ValidationError, match="SetValue"):
        _make_viz(["a=Slider(0,1,0.1)", "SetValue(a, 0.5)"])


def test_validator_rejects_line_equation_wrapper():
    with pytest.raises(ValidationError, match="Line"):
        _make_viz(["p1=3", "l1=Line(x+y=p1)"])


def test_validator_rejects_set_condition_to_show_object():
    with pytest.raises(ValidationError, match="SetConditionToShowObject"):
        _make_viz([
            "isMin=false",
            "p=Polygon((0,0),(1,0),(0,1))",
            "SetConditionToShowObject(p, isMin==false)",
        ])


def test_validator_rejects_reserved_axis_name():
    with pytest.raises(ValidationError, match="built-in GeoGebra identifier"):
        _make_viz([
            "xAxis=Line((0,0),(1,0))",
            "yAxis=Line((0,0),(0,1))",
        ])


def test_validator_accepts_conditional_definition():
    viz = _make_viz([
        "isMin=false",
        "p=If(isMin==false, Polygon((0,0),(1,0),(0,1)))",
    ])
    assert len(viz.ggb_commands) == 2


def test_validator_accepts_coordinate_form_offset():
    viz = _make_viz([
        "K=(1,1)",
        "tParam=Slider(0,6.28,0.05)",
        "P=(x(K)+2*cos(tParam), y(K)+2*sin(tParam))",
    ])
    assert len(viz.ggb_commands) == 3


def test_validator_rejects_greek_alias_slider_name():
    with pytest.raises(ValidationError, match="Greek-letter alias"):
        _make_viz([
            "beta=Slider(0,6.28,0.05)",
            "P=(cos(beta), sin(beta))",
        ])


def test_validator_rejects_greek_alias_other_names():
    for name in ("alpha", "theta", "phi", "Alpha"):
        with pytest.raises(ValidationError, match="Greek-letter alias"):
            _make_viz([f"{name}=Slider(0,1,0.1)"])


# ── DB round-trip via _persist_viz + _serialize_viz_row ────────────


@pytest.mark.asyncio
async def test_persist_and_serialize_geogebra_row(session):
    q = _seed_question(session, f"viz-ggb-{uuid.uuid4().hex[:8]}")
    await session.flush()
    assert q.id is not None

    viz = Visualization(
        id="v-circle",
        title_cn="单位圆",
        caption_cn="$x^2+y^2=1$",
        learning_goal="理解单位圆",
        interactive_hints=["拖动 P 观察坐标"],
        engine="geogebra",
        ggb_commands=[
            "c=Circle((0,0),1)",
            "P=Point(c)",
        ],
        ggb_settings=GgbSettings(
            app_name="graphing",
            grid_visible=False,
            coord_system=[-2, 2, -2, 2],
        ),
    )
    await _persist_viz(session, q.id, viz)
    await session.flush()

    rows = (
        await session.execute(
            select(VisualizationRow).where(VisualizationRow.question_id == q.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.engine == "geogebra"
    assert row.jsx_code == ""
    assert row.ggb_commands_json[0].startswith("c=Circle")
    assert row.ggb_settings_json["app_name"] == "graphing"
    assert row.ggb_settings_json["grid_visible"] is False

    # The main frontend payload no longer exposes legacy GeoGebra command fields.
    serialized = _serialize_viz_row(row)
    assert serialized["engine"] == "geogebra"
    assert "ggb_commands" not in serialized
    assert "ggb_settings" not in serialized
    assert "jsx_code" not in serialized
    assert serialized["spec_json"] is None
    assert serialized["execution_payload"] is None
    assert serialized["degraded"] is False
    assert serialized["interactive_hints"] == ["拖动 P 观察坐标"]


@pytest.mark.asyncio
async def test_persist_and_serialize_jsxgraph_row_backward_compat(session):
    q = _seed_question(session, f"viz-jsx-{uuid.uuid4().hex[:8]}")
    await session.flush()
    viz = Visualization(
        id="v-legacy",
        title_cn="legacy",
        caption_cn="c",
        learning_goal="g",
        jsx_code="board.create('point',[0,0]);",
    )
    await _persist_viz(session, q.id, viz)
    await session.flush()

    row = (
        await session.execute(
            select(VisualizationRow).where(VisualizationRow.question_id == q.id)
        )
    ).scalars().one()
    assert row.engine == "jsxgraph"
    assert row.jsx_code.startswith("board.create")
    assert row.ggb_commands_json == []
    assert row.ggb_settings_json is None

    assert _serialize_viz_row(row) is None


@pytest.mark.asyncio
async def test_bootstrap_solution_preserves_engine_specific_fields(session):
    q = _seed_question(session, f"viz-bootstrap-{uuid.uuid4().hex[:8]}")
    await session.flush()

    viz = Visualization(
        id="v-bootstrap",
        title_cn="单位圆动点",
        caption_cn="对应解答 step 2",
        learning_goal="观察动点参数变化",
        engine="geogebra",
        ggb_commands=["c=Circle((0,0),1)", "P=Point(c)"],
        ggb_settings=GgbSettings(app_name="graphing", coord_system=[-2, 2, -2, 2]),
    )
    await _persist_viz(session, q.id, viz)
    await session.flush()

    row = (
        await session.execute(
            select(VisualizationRow)
            .where(VisualizationRow.question_id == q.id)
            .limit(1)
        )
    ).scalar_one()
    row.spec_json = {
        "id": "spec-bootstrap",
        "title": "单位圆动点",
        "recommended": True,
        "pedagogical_purpose": "观察动点参数变化",
    }
    await session.flush()

    solution = await bootstrap_solution_from_question(session, question=q)
    assert solution.visualizations_json
    first = solution.visualizations_json[0]
    assert first["engine"] == "geogebra"
    assert "ggb_commands" not in first
    assert "ggb_settings" not in first
    assert first["execution_payload"] is None
    assert first["degraded"] is False
    assert first["spec_json"]["id"] == "spec-bootstrap"
    assert solution.visualization_plan_json is not None
    assert solution.visualization_plan_json["selected_visualization"]["id"] == "spec-bootstrap"


@pytest.mark.asyncio
async def test_generate_visualizations_uses_single_batch_call_without_repair(session):
    """The batch path should use one generation call and local validation only."""
    q = _seed_question(session, f"viz-batch-{uuid.uuid4().hex[:8]}")
    q.answer_package_json = _answer_package_json()
    await session.flush()

    batch_response = {
        "visualizations": [
            {
                "id": "viz-1",
                "title_cn": "交点示意",
                "caption_cn": "对应解答 step 1",
                "learning_goal": "理解交点位置",
                "engine": "geogebra",
                "ggb_commands": ["f(x)=x^2-1", "A=(-1,0)", "B=(1,0)"],
            },
            {
                "id": "viz-2",
                "title_cn": "顶点比较",
                "caption_cn": "对应解答 step 2",
                "learning_goal": "理解顶点与最值",
                "engine": "geogebra",
                "ggb_commands": ["f(x)=x^2-1", "V=(0,-1)"],
            },
        ]
    }
    transport = _SequenceTransport([
        json.dumps(batch_response, ensure_ascii=False),
        json.dumps(batch_response, ensure_ascii=False),
    ])
    client = GeminiClient(transport)

    events = [
        ev async for ev in generate_visualizations_batch(
            session,
            question_id=q.id,
            llm=client,
        )
    ]

    assert [ev.name for ev in events] == ["visualization", "visualization"]
    assert [ev.data["id"] for ev in events] == ["viz-1", "viz-2"]
    assert len(transport.calls) == 1
    rows = (
        await session.execute(
            select(VisualizationRow)
            .where(VisualizationRow.question_id == q.id)
            .order_by(VisualizationRow.created_at)
        )
    ).scalars().all()
    assert [row.viz_ref for row in rows] == ["viz-1", "viz-2"]
    assert all(row.engine == "geogebra" for row in rows)


@pytest.mark.asyncio
async def test_generate_visualizations_sanitizes_geogebra_identifiers_locally(session):
    q = _seed_question(session, f"viz-batch-sanitize-{uuid.uuid4().hex[:8]}")
    q.answer_package_json = _answer_package_json()
    await session.flush()

    batch_response = {
        "visualizations": [
            {
                "id": "viz-1",
                "title_cn": "参数滑块",
                "caption_cn": "对应解答 step 1",
                "learning_goal": "观察参数变化",
                "engine": "geogebra",
                "ggb_commands": ["alpha=Slider(-3,3,0.1)", "f(x)=x^2+alpha"],
                "params": [
                    {
                        "name": "alpha",
                        "label_cn": "参数 alpha",
                        "kind": "slider",
                        "default": 0,
                        "min": -3,
                        "max": 3,
                        "step": 0.1,
                    }
                ],
            },
            {
                "id": "viz-2",
                "title_cn": "顶点比较",
                "caption_cn": "对应解答 step 2",
                "learning_goal": "理解顶点变化",
                "engine": "geogebra",
                "ggb_commands": ["f(x)=x^2", "V=(0,0)"],
            },
        ]
    }
    transport = _SequenceTransport([json.dumps(batch_response, ensure_ascii=False)])
    client = GeminiClient(transport)

    events = [
        ev async for ev in generate_visualizations_batch(
            session,
            question_id=q.id,
            llm=client,
        )
    ]

    assert [ev.name for ev in events] == ["visualization", "visualization"]
    assert events[0].data["ggb_commands"] == [
        "param_alpha=Slider(-3,3,0.1)",
        "f(x)=x^2+param_alpha",
    ]
    assert events[0].data["params"][0]["name"] == "param_alpha"
    assert events[0].data["title_cn"] == "参数滑块"
    rows = (
        await session.execute(
            select(VisualizationRow)
            .where(VisualizationRow.question_id == q.id)
            .order_by(VisualizationRow.created_at)
        )
    ).scalars().all()
    assert rows[0].ggb_commands_json == [
        "param_alpha=Slider(-3,3,0.1)",
        "f(x)=x^2+param_alpha",
    ]
    assert rows[0].params_json[0]["name"] == "param_alpha"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_generate_visualizations_reports_local_runtime_validation_failure(session, monkeypatch):
    q = _seed_question(session, f"viz-batch-runtime-fail-{uuid.uuid4().hex[:8]}")
    q.answer_package_json = _answer_package_json()
    await session.flush()

    batch_response = {
        "visualizations": [
            {
                "id": "viz-1",
                "title_cn": "交点示意",
                "caption_cn": "对应解答 step 1",
                "learning_goal": "理解交点位置",
                "engine": "geogebra",
                "ggb_commands": ["A=(0,0)", "B=(1,0)"],
            },
            {
                "id": "viz-2",
                "title_cn": "顶点比较",
                "caption_cn": "对应解答 step 2",
                "learning_goal": "理解顶点变化",
                "engine": "geogebra",
                "ggb_commands": ["f(x)=x^2", "V=(0,0)"],
            },
        ]
    }

    async def _fail(viz, *, timeout_s=20.0):
        raise GeoGebraValidationError([
            {"kind": "runtime", "message": f"runtime failed for {viz.id}"},
        ])

    monkeypatch.setattr("app.services.vizcoder_service.validate_geogebra_visualization", _fail)

    transport = _SequenceTransport([json.dumps(batch_response, ensure_ascii=False)])
    client = GeminiClient(transport)

    events = [
        ev async for ev in generate_visualizations_batch(
            session,
            question_id=q.id,
            llm=client,
        )
    ]

    assert events[0].name == "error"
    assert events[0].data["stage"] == "viz_validator"
    assert events[0].data["viz_id"] == "viz-1"
    assert events[1].name == "error"
    assert events[1].data["stage"] == "viz_validator"
    assert events[1].data["viz_id"] == "viz-2"
    rows = (
        await session.execute(
            select(VisualizationRow)
            .where(VisualizationRow.question_id == q.id)
            .order_by(VisualizationRow.created_at)
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_vizplanner_uses_streaming_when_enabled(session):
    q = _seed_question(session, f"vizplanner-stream-{uuid.uuid4().hex[:8]}")
    q.answer_package_json = _answer_package_json()
    await session.flush()

    transport = _SequenceTransport([json.dumps(_storyboard_payload(), ensure_ascii=False)])
    client = GeminiClient(transport)
    old_flag = settings.llm.stream_vizcoder_json
    settings.llm.stream_vizcoder_json = True
    try:
        storyboard = await plan_visualization_storyboard(
            session,
            question_id=q.id,
            llm=client,
        )
    finally:
        settings.llm.stream_vizcoder_json = old_flag

    assert storyboard is not None
    assert transport.calls
    assert transport.calls[-1].get("stream") is True


@pytest.mark.asyncio
async def test_vizitem_uses_streaming_when_enabled(session):
    q = _seed_question(session, f"vizitem-stream-{uuid.uuid4().hex[:8]}")
    q.answer_package_json = _answer_package_json()
    await session.flush()

    storyboard = VisualizationStoryboard.model_validate(_storyboard_payload())
    item = storyboard.items[0]
    transport = _SequenceTransport([json.dumps(_single_viz_payload(), ensure_ascii=False)])
    client = GeminiClient(transport)
    old_flag = settings.llm.stream_vizcoder_json
    settings.llm.stream_vizcoder_json = True
    try:
        viz = await _generate_visualization_for_storyboard_item(
            session,
            question_id=q.id,
            llm=client,
            storyboard=storyboard,
            item=item,
            previous_items=[],
        )
    finally:
        settings.llm.stream_vizcoder_json = old_flag

    assert viz.id == "viz-1"
    assert transport.calls
    assert transport.calls[-1].get("stream") is True


def test_sanitize_geogebra_visualization_rewrites_reserved_names_and_bindings():
    viz = sanitize_geogebra_visualization({
        "id": "viz-1",
        "title_cn": "坐标轴冲突",
        "caption_cn": "测试",
        "learning_goal": "测试",
        "engine": "geogebra",
        "jsx_code": "",
        "ggb_commands": ["xAxis=Slider(-3,3,0.1)", "P=(xAxis,0)"],
        "params": [
            {
                "name": "xAxis",
                "label_cn": "参数 xAxis",
                "kind": "slider",
                "default": 0,
                "min": -3,
                "max": 3,
                "step": 0.1,
            }
        ],
        "animation": {"kind": "loop", "duration_ms": 1200, "drives": ["xAxis"]},
    })

    assert viz.ggb_commands == ["obj_xAxis=Slider(-3,3,0.1)", "P=(obj_xAxis,0)"]
    assert viz.params[0].name == "obj_xAxis"
    assert viz.animation is not None
    assert viz.animation.drives == ["obj_xAxis"]


@pytest.mark.asyncio
async def test_vizitem_does_not_retry_invalid_payload_when_repairs_are_disabled(session):
    q = _seed_question(session, f"vizitem-no-repair-{uuid.uuid4().hex[:8]}")
    q.answer_package_json = _answer_package_json()
    await session.flush()

    storyboard = VisualizationStoryboard.model_validate(_storyboard_payload())
    item = storyboard.items[0]
    bad_payload = _single_viz_payload().copy()
    bad_payload.pop("caption_cn")
    transport = _SequenceTransport([json.dumps(bad_payload, ensure_ascii=False)])
    client = GeminiClient(transport)

    old_stream = settings.llm.stream_vizcoder_json
    settings.llm.stream_vizcoder_json = True
    try:
        with pytest.raises(LLMError, match="caption_cn"):
            await _generate_visualization_for_storyboard_item(
                session,
                question_id=q.id,
                llm=client,
                storyboard=storyboard,
                item=item,
                previous_items=[],
            )
    finally:
        settings.llm.stream_vizcoder_json = old_stream

    assert len(transport.calls) == 1
    assert transport.calls[0].get("stream") is True
