"""Pydantic models for LLM output contracts.

These mirror the JSON Schemas in `app.prompts.schemas` and are used for
runtime validation, DB persistence, and API responses. Single source of
truth: if a field changes here, update `prompts/schemas.py` in lock-step.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


class Visualization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title_cn: str
    caption_cn: str
    learning_goal: str
    interactive_hints: list[str] = Field(default_factory=list)
    helpers_used: list[str] = Field(default_factory=list)
    jsx_code: str
    params: list[VizParam] = Field(default_factory=list)
    animation: VizAnimation | None = None


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
