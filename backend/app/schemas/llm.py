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
StoryboardAnchorKind = Literal[
    "question_given",
    "solution_step",
    "formula",
    "pitfall",
    "final_answer",
    "method_pattern",
]
StoryboardRisk = Literal["low", "medium", "high"]


class VisualizationAnchorRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: StoryboardAnchorKind
    ref: str
    excerpt_cn: str = ""


class StoryboardSymbol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    meaning_cn: str
    source_ref: str = ""


class StoryboardCoverageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    summary_cn: str
    anchor_refs: list[VisualizationAnchorRef] = Field(default_factory=list)


class VisualizationStoryboardItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title_cn: str
    anchor_refs: list[VisualizationAnchorRef] = Field(min_length=1)
    difficulty_reason_cn: str
    student_confusion_risk: StoryboardRisk
    conceptual_jump_cn: str
    why_visualization_needed_cn: str
    learning_goal_cn: str
    engine: VizEngine = "geogebra"
    shared_symbols: list[str] = Field(default_factory=list)
    shared_params: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    relation_to_prev_cn: str = ""
    relation_to_next_cn: str = ""
    caption_outline_cn: str
    geo_target_cn: str


class VisualizationStoryboard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme_cn: str
    selection_rationale_cn: str
    symbol_map: list[StoryboardSymbol] = Field(default_factory=list)
    shared_params: list[VizParam] = Field(default_factory=list)
    coverage_summary: list[StoryboardCoverageEntry] = Field(default_factory=list)
    sequence: list[str] = Field(min_length=3, max_length=4)
    items: list[VisualizationStoryboardItem] = Field(min_length=3, max_length=4)

    @model_validator(mode="after")
    def _check_storyboard_integrity(self) -> VisualizationStoryboard:
        item_ids = [item.id for item in self.items]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("storyboard item ids must be unique")
        if set(self.sequence) != set(item_ids) or len(self.sequence) != len(item_ids):
            raise ValueError("sequence must contain every storyboard item id exactly once")

        sequence_pos = {item_id: idx for idx, item_id in enumerate(self.sequence)}
        known_symbols = {item.symbol for item in self.symbol_map}
        known_params = {param.name for param in self.shared_params}
        known_items = set(item_ids)

        for item in self.items:
            for dependency in item.depends_on:
                if dependency not in known_items:
                    raise ValueError(f"storyboard item '{item.id}' depends on unknown id '{dependency}'")
                if sequence_pos[dependency] >= sequence_pos[item.id]:
                    raise ValueError(
                        f"storyboard item '{item.id}' depends on '{dependency}' which must appear earlier in sequence"
                    )
            missing_symbols = [symbol for symbol in item.shared_symbols if symbol not in known_symbols]
            if missing_symbols:
                raise ValueError(
                    f"storyboard item '{item.id}' references unknown shared symbols: {missing_symbols}"
                )
            missing_params = [param for param in item.shared_params if param not in known_params]
            if missing_params:
                raise ValueError(
                    f"storyboard item '{item.id}' references unknown shared params: {missing_params}"
                )

        for coverage in self.coverage_summary:
            if coverage.item_id not in known_items:
                raise ValueError(
                    f"coverage_summary references unknown storyboard item '{coverage.item_id}'"
                )

        return self


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
_GGB_SETVALUE = re.compile(r"^SetValue\s*\(")
_GGB_LINE_EQUATION_WRAPPER = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*Line\s*\(\s*[^,]+=[^,]+\s*\)$"
)
_GGB_SETCONDITION_TO_SHOW = re.compile(r"^SetConditionToShowObject\s*\(")
# GeoGebra reserves Greek-letter aliases (it auto-renames a `beta=Slider(...)`
# to `beta_1` because `beta` ↔ β collides with built-ins). Any subsequent
# `cos(beta)` then fails to resolve. Force ASCII identifiers that don't
# spell Greek letters.
_GGB_GREEK_ALIASES = frozenset({
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi", "rho",
    "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
})
# GeoGebra reserves a handful of built-in object names. Re-defining them via
# `xAxis=Line(...)` collides with the built-in axes/views and the assignment
# is silently rejected (or auto-renamed), breaking every later reference.
_GGB_RESERVED_NAMES = frozenset({
    "xAxis", "yAxis", "zAxis", "xOyPlane", "xOzPlane", "yOzPlane",
    "e", "i",
})
_GGB_LHS_NAME = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*=")


class Visualization(BaseModel):
    """One interactive visualization for an AnswerPackage.

    Two render engines are supported (selected via ``engine``):

    - ``geogebra``: the LLM emits a list of GeoGebra command
      strings (``ggb_commands``) that the GeoGebra Apps API interprets.
      No JavaScript evaluation involved → safer + math-professional
      rendering with built-in animation. ``jsx_code`` should be empty.
    - ``jsxgraph``: the LLM emits a JavaScript function
      body in ``jsx_code`` that runs in the JSXGraph sandbox. For older
      payloads or animation-heavy / custom cases the GeoGebra command set
      cannot express cleanly.

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
            if _GGB_SETVALUE.match(cmd):
                problems.append(
                    f"#{i} '{cmd[:80]}': do not emit SetValue(...) inside ggb_commands. "
                    f"Define the object with 'name=Slider(...)' or 'flag=true/false', "
                    f"then put the initial UI value into params[].default so the "
                    f"frontend applies it via the GeoGebra API."
                )
            if _GGB_LINE_EQUATION_WRAPPER.match(cmd):
                problems.append(
                    f"#{i} '{cmd[:80]}': do not wrap an equation inside Line(...). "
                    f"GeoGebra Line accepts point-point / point-direction inputs, not "
                    f"Line(ax+by=c). Re-express the line with two points, for example "
                    f"'l=Line((0,c),(c,0))' for x+y=c, or use a point plus direction vector."
                )
            if _GGB_SETCONDITION_TO_SHOW.match(cmd):
                problems.append(
                    f"#{i} '{cmd[:80]}': SetConditionToShowObject is a GUI-only object "
                    f"property; the GeoGebra Apps API rejects it through evalCommand. "
                    f"Express conditional visibility through conditional definition "
                    f"instead, e.g. 'polyA=If(flag==false, Polygon(...))' or "
                    f"'segA=If(flag, Segment(P,Q))'. When the condition is false the "
                    f"object is undefined and GeoGebra automatically hides it."
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
                if name in _GGB_RESERVED_NAMES:
                    problems.append(
                        f"#{i} '{cmd[:80]}': '{name}' is a built-in GeoGebra identifier "
                        f"(coordinate axis / plane / constant). Re-defining it collides "
                        f"with the built-in and the assignment is rejected. Rename to "
                        f"something like 'xAxisRef' / 'lineX' / 'lineY'. To draw the "
                        f"coordinate axes, leave them on via ggb_settings.axes_visible."
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
        if self.engine == "jsxgraph" and not self.jsx_code.strip():
            raise ValueError("engine='jsxgraph' requires non-empty jsx_code")
        return self


class VisualizationList(BaseModel):
    """Wrapper so VizCoder can return a single JSON root object."""

    model_config = ConfigDict(extra="forbid")

    visualizations: list[Visualization] = Field(min_length=3, max_length=4)


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
