"""GeoGebra render-engine support for VizCoder (M4+).

Covers:
  - Schema accepts ``engine="geogebra"`` payloads with ``ggb_commands``
    and an empty ``jsx_code``.
  - Schema still accepts legacy ``engine="jsxgraph"`` payloads (default).
  - The Pydantic ``Visualization`` validator catches GeoGebra anti-patterns
    (view directives in commands, ``P=K+(...)`` shorthand, ``Vector((a),(b))``,
    color names in ``SetColor``, oversized payloads). Failures raise
    ``ValidationError`` so the LLM repair loop can fix them.
  - ``_persist_viz`` writes the new columns and ``_serialize_viz_row``
    surfaces them in the answer-resume payload.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db import models
from app.db.models import VisualizationRow
from app.schemas.llm import GgbSettings, Visualization, VisualizationList
from app.services.answer_job_service import _serialize_viz_row
from app.services.vizcoder_service import _persist_viz


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


def test_visualization_list_round_trips_geogebra():
    payload = {
        "visualizations": [
            {
                "id": "v1",
                "title_cn": "抛物线",
                "caption_cn": "$y=x^2$",
                "learning_goal": "看顶点平移",
                "engine": "geogebra",
                "ggb_commands": ["a=Slider(-3,3,0.1)", "f(x)=(x-a)^2"],
            }
        ]
    }
    parsed = VisualizationList.model_validate(payload)
    assert parsed.visualizations[0].engine == "geogebra"
    # Serializes back to a dict with the new fields present.
    dumped = parsed.model_dump(mode="json")
    v0 = dumped["visualizations"][0]
    assert v0["engine"] == "geogebra"
    assert v0["ggb_commands"] == ["a=Slider(-3,3,0.1)", "f(x)=(x-a)^2"]
    assert v0["jsx_code"] == ""


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

    # Frontend payload mirrors the DB row.
    serialized = _serialize_viz_row(row)
    assert serialized["engine"] == "geogebra"
    assert serialized["ggb_commands"] == row.ggb_commands_json
    assert serialized["ggb_settings"] == row.ggb_settings_json
    assert serialized["jsx_code"] == ""


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

    serialized = _serialize_viz_row(row)
    assert serialized["engine"] == "jsxgraph"
    assert serialized["ggb_commands"] == []
    assert serialized["ggb_settings"] is None
