"""Pydantic models for LLM output contracts.

These mirror the JSON Schemas in `app.prompts.schemas` and are used for
runtime validation, DB persistence, and API responses. Single source of
truth: if a field changes here, update `prompts/schemas.py` in lock-step.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── ParsedQuestion ──────────────────────────────────────────────────

Subject = Literal["math", "physics"]
GradeBand = Literal["junior", "senior"]


class ParsedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Subject
    grade_band: GradeBand
    topic_path: list[str] = Field(default_factory=list)
    question_text: str
    given: list[str] = Field(default_factory=list)
    find: list[str] = Field(default_factory=list)
    diagram_description: str = ""
    difficulty: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


# ── AnswerPackage ───────────────────────────────────────────────────


class QuestionUnderstanding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restated_question: str
    givens: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    implicit_conditions: list[str] = Field(default_factory=list)


class SolutionStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_index: int
    statement: str
    rationale: str
    formula: str = ""
    why_this_step: str
    viz_ref: str = ""


class MethodPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern_id_suggested: str
    name_cn: str
    when_to_use: str
    general_procedure: list[str]
    pitfalls: list[str] = Field(default_factory=list)


class SimilarQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str
    answer_outline: str
    same_pattern: bool = True
    difficulty_delta: int = Field(ge=-2, le=2)


class KnowledgePointRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ref: str  # existing id or "new:path>to>node"
    weight: float = Field(ge=0.0, le=1.0)


class AnswerPackage(BaseModel):
    """Teaching-first answer bundle. Visualizations are appended separately."""

    model_config = ConfigDict(extra="forbid")

    question_understanding: QuestionUnderstanding
    key_points_of_question: list[str]
    solution_steps: list[SolutionStep]
    key_points_of_answer: list[str]
    method_pattern: MethodPattern
    similar_questions: list[SimilarQuestion] = Field(min_length=3, max_length=3)
    knowledge_points: list[KnowledgePointRef]
    self_check: list[str]


# ── Multi-turn dialog memory ─────────────────────────────────────────


class ConversationMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    key_facts: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class ConversationTurnResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_suggested: str = ""
    assistant_reply: str
    follow_up_suggestions: list[str] = Field(default_factory=list)
    memory: ConversationMemory


# ── Visualization ───────────────────────────────────────────────────


class VizParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label_cn: str
    kind: Literal["slider", "toggle"]
    min: float | None = None
    max: float | None = None
    step: float | None = None
    default: float | bool | int


class VizAnimation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["loop", "once"]
    duration_ms: int
    drives: list[str] = Field(default_factory=list)


VizEngine = Literal["jsxgraph", "geogebra"]


class GgbSettings(BaseModel):
    """Per-visualization GeoGebra applet configuration.

    All fields optional; the frontend supplies sensible defaults when
    omitted. Limited surface area to keep the LLM honest.
    """

    model_config = ConfigDict(extra="forbid")

    app_name: Literal["graphing", "geometry", "3d", "classic", "suite"] = "graphing"
    perspective: str | None = None  # see GeoGebra SetPerspective command
    coord_system: list[float] | None = None  # [xmin,xmax,ymin,ymax] or 6 entries for 3D
    axes_visible: bool = True
    grid_visible: bool = True
    show_algebra_input: bool = False
    show_tool_bar: bool = False
    show_menu_bar: bool = False


# ── GeoGebra ggb_commands anti-pattern guards ───────────────────────
# These compile-time constants drive the Pydantic validator below.
# When a payload trips one of these guards Pydantic raises ValidationError
# with an actionable message; GeminiClient's repair loop then re-prompts the
# LLM with the diagnostic until the payload is clean (or attempts exhausted).
_GGB_VIEW_DIRECTIVES = frozenset({
    "SetCoordSystem", "SetAxesVisible", "SetGridVisible",
    "SetPerspective", "ShowAxes", "ShowGrid",
})
_GGB_MAX_COMMANDS = 64
_GGB_MAX_COMMAND_LEN = 512
_GGB_POINT_PLUS_TUPLE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*\s*=\s*[A-Za-z][A-Za-z0-9_]*\s*[+\-]\s*\("
)
_GGB_VECTOR_TWO_SCALARS = re.compile(
    # Vector( (..) , (..) )  with up to one level of nested parens inside
    # each bracketed scalar (catches Vector((cos(t)),(sin(t))) etc.).
    r"Vector\s*\(\s*\((?:[^()]|\([^()]*\))*\)\s*,\s*\((?:[^()]|\([^()]*\))*\)\s*\)"
)
_GGB_TRANSLATE_INLINE_VECTOR = re.compile(
    r"Translate\s*\([^,]+,\s*Vector\s*\("
)
_GGB_SETCOLOR_NAMED = re.compile(
    r"SetColor\s*\([^,]+,\s*[\"\'][A-Za-z]+[\"\']\s*\)"
)
# GeoGebra reserves Greek-letter aliases (it auto-renames a `beta=Slider(...)`
# to `beta_1` because `beta` ↔ β collides with built-ins). Any subsequent
# `cos(beta)` then fails to resolve. Force ASCII identifiers that don't
# spell Greek letters.
_GGB_GREEK_ALIASES = frozenset({
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi", "rho",
    "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
})
_GGB_LHS_NAME = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*=")


class Visualization(BaseModel):
    """One interactive visualization for an AnswerPackage.

    Two render engines are supported (selected via ``engine``):

    - ``geogebra`` (preferred): the LLM emits a list of GeoGebra command
      strings (``ggb_commands``) that the GeoGebra Apps API interprets.
      No JavaScript evaluation involved → safer + math-professional
      rendering with built-in animation. ``jsx_code`` should be empty.
    - ``jsxgraph`` (legacy fallback): the LLM emits a JavaScript function
      body in ``jsx_code`` that runs in the JSXGraph sandbox. For older
      payloads or niche use cases the GeoGebra command set cannot express.

    For backward compatibility ``engine`` defaults to ``"jsxgraph"`` and
    ``jsx_code`` defaults to ``""`` so older / partial payloads validate
    without modification.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    title_cn: str
    caption_cn: str
    learning_goal: str
    interactive_hints: list[str] = Field(default_factory=list)
    helpers_used: list[str] = Field(default_factory=list)
    engine: VizEngine = "jsxgraph"
    jsx_code: str = ""
    ggb_commands: list[str] = Field(default_factory=list)
    ggb_settings: GgbSettings | None = None
    params: list[VizParam] = Field(default_factory=list)
    animation: VizAnimation | None = None

    # ── Anti-pattern guards (trigger LLM repair loop on failure) ──────
    # These catch GeoGebra command shapes that the Apps API's evalCommand
    # silently rejects, cascading into broken downstream constructions.
    # Error messages are written to be directly actionable for the LLM.

    @field_validator("ggb_commands")
    @classmethod
    def _validate_ggb_command_shapes(cls, commands: list[str]) -> list[str]:
        problems: list[str] = []
        if len(commands) > _GGB_MAX_COMMANDS:
            problems.append(
                f"too many commands ({len(commands)} > {_GGB_MAX_COMMANDS}); "
                f"split the visualization or remove redundant lines"
            )
        for i, raw in enumerate(commands):
            if not isinstance(raw, str):
                problems.append(f"#{i}: not a string")
                continue
            cmd = raw.strip()
            if not cmd:
                continue
            if len(cmd) > _GGB_MAX_COMMAND_LEN:
                problems.append(
                    f"#{i} ({len(cmd)} chars > {_GGB_MAX_COMMAND_LEN}): "
                    f"split into smaller commands"
                )
                continue
            if "\n" in cmd or "\r" in cmd:
                problems.append(f"#{i}: contains newline (split into separate commands)")
            head = cmd.split("(", 1)[0].strip()
            if head in _GGB_VIEW_DIRECTIVES:
                problems.append(
                    f"#{i} '{cmd[:80]}': view/axes/grid/perspective directive must "
                    f"go into ggb_settings, not ggb_commands"
                )
                continue
            if _GGB_POINT_PLUS_TUPLE.match(cmd):
                problems.append(
                    f"#{i} '{cmd[:80]}': do not use 'P=base+(dx,dy)' shorthand. "
                    f"Write 'P=(x(base)+dx, y(base)+dy)' instead — coordinate-form "
                    f"point definitions render reliably with slider-driven expressions."
                )
                continue
            if _GGB_VECTOR_TWO_SCALARS.search(cmd):
                problems.append(
                    f"#{i} '{cmd[:80]}': Vector((a),(b)) is invalid — Vector takes "
                    f"either one point literal Vector((x,y)) or two points Vector(P,Q)."
                )
            if _GGB_TRANSLATE_INLINE_VECTOR.search(cmd):
                problems.append(
                    f"#{i} '{cmd[:80]}': avoid Translate(point, Vector((...))) — "
                    f"the GeoGebra Apps API does not reliably parse inline Vector "
                    f"tuples with slider expressions. Use 'P=(x(base)+dx, y(base)+dy)'."
                )
            if _GGB_SETCOLOR_NAMED.search(cmd):
                problems.append(
                    f"#{i} '{cmd[:80]}': SetColor expects an RGB triple, e.g. "
                    f"SetColor(obj, 255, 0, 0). Color names are not portable."
                )
            lhs = _GGB_LHS_NAME.match(cmd)
            if lhs:
                name = lhs.group(1)
                if name.lower() in _GGB_GREEK_ALIASES:
                    problems.append(
                        f"#{i} '{cmd[:80]}': '{name}' is a Greek-letter alias that "
                        f"GeoGebra auto-renames to '{name}_1' (collides with built-in "
                        f"{name}↔Greek). Every later command referencing '{name}' will "
                        f"fail. Use a non-Greek ASCII name like 'tParam', 'angA', 'k1'."
                    )
        if problems:
            raise ValueError(
                "ggb_commands contain forms that the GeoGebra Apps API rejects. "
                "Fix every command listed and re-emit the full visualization JSON:\n  - "
                + "\n  - ".join(problems)
            )
        return commands

    @model_validator(mode="after")
    def _check_engine_payload(self) -> Visualization:
        if self.engine == "geogebra" and not self.ggb_commands:
            raise ValueError("engine='geogebra' requires at least one ggb_command")
        return self


class VisualizationList(BaseModel):
    """Wrapper so VizCoder can return a single JSON root object."""

    model_config = ConfigDict(extra="forbid")

    visualizations: list[Visualization]


# ── Variant synthesis (M7) ──────────────────────────────────────────


class VariantQuestion(BaseModel):
    """A LLM-synthesized variant that preserves a method pattern."""

    model_config = ConfigDict(extra="forbid")

    statement: str
    answer_outline: str
    rubric: str
    difficulty: int = Field(ge=1, le=5)
    same_pattern: bool = True


class VariantList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variants: list[VariantQuestion]


# ── Pedagogical retrieval index (deterministic stage-1 implementation) ──


class RetrievalQueryTexts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_full_text: str
    answer_full_text: str
    method_text: str
    step_texts: list[str] = Field(default_factory=list)
    extension_text: str = ""


class PedagogicalIndexProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Subject
    grade_band: GradeBand
    textbook_stage: str = ""
    topic_path: list[str] = Field(default_factory=list)
    novelty_flags: list[str] = Field(default_factory=list)
    object_entities: list[str] = Field(default_factory=list)
    target_types: list[str] = Field(default_factory=list)
    condition_signals: list[str] = Field(default_factory=list)
    question_focus: list[str] = Field(default_factory=list)
    answer_focus: list[str] = Field(default_factory=list)
    method_labels: list[str] = Field(default_factory=list)
    extension_ideas: list[str] = Field(default_factory=list)
    pitfalls: list[str] = Field(default_factory=list)
    lexical_aliases: list[str] = Field(default_factory=list)
    query_texts: RetrievalQueryTexts


class RetrievalUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_kind: str
    title: str
    text: str
    keywords: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    source_section: str = ""
