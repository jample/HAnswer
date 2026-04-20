"""Practice exam service (M7, §3.5).

Builds an exam by:
  1. Selecting candidate `Question` rows from PG matching filters
     (subjects/grade_band, topics via KP links, patterns via pattern
     links, difficulty distribution).
  2. If the bank is short, calling the VariantSynth prompt to produce
     method-pattern-preserving variants based on representative
     source questions.
  3. Persisting an `Exam` + `ExamItem` rows in deterministic order.

Exam items reference a real `Question.id` (when sourced from the bank)
OR carry a `synthesized_payload_json` (when LLM-generated). Each item
also carries an `answer_outline` and a `rubric` hint for self-check.
"""

from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Exam,
    ExamItem,
    Question,
    QuestionKPLink,
    QuestionPatternLink,
)
from app.prompts import VariantSynthPrompt
from app.schemas import AnswerPackage, VariantList, VariantQuestion
from app.services.llm_client import GeminiClient, PromptLogContext

log = logging.getLogger(__name__)


@dataclass
class ExamConfig:
    name: str = "练习卷"
    subjects: list[str] = field(default_factory=list)          # ["math"]
    grade_bands: list[str] = field(default_factory=list)       # ["junior","senior"]
    topic_kp_ids: list[str] = field(default_factory=list)      # filter by KP membership
    pattern_ids: list[str] = field(default_factory=list)       # filter by pattern membership
    source_question_ids: list[str] = field(default_factory=list)
    count: int = 5
    difficulty_dist: dict[int, int] = field(default_factory=dict)   # {diff: count}
    allow_synthesis: bool = True
    seed: int | None = None


@dataclass
class ExamItemDraft:
    position: int
    source_question_id: uuid.UUID | None
    synthesized_payload: dict | None
    answer_outline: str
    rubric: str


# ── Candidate selection ─────────────────────────────────────────────


async def _pull_candidates(
    session: AsyncSession, cfg: ExamConfig, *, exclude: set[uuid.UUID],
) -> list[Question]:
    """Return questions matching the filters, de-duped by id.

    Difficulty is not forced here — the caller samples per-bucket to
    honor `difficulty_dist`.
    """
    stmt = select(Question).order_by(Question.created_at.desc())
    if cfg.subjects:
        stmt = stmt.where(Question.subject.in_(cfg.subjects))
    if cfg.grade_bands:
        stmt = stmt.where(Question.grade_band.in_(cfg.grade_bands))

    # Must have been answered (§3.5: exam items need an answer outline).
    stmt = stmt.where(Question.answer_package_json.is_not(None))

    # If explicit sources provided, pull exactly those (still subject to
    # filters above via the SQL compiler).
    if cfg.source_question_ids:
        ids = [uuid.UUID(x) for x in cfg.source_question_ids]
        stmt = stmt.where(Question.id.in_(ids))

    rows = list((await session.execute(stmt)).scalars().all())

    # KP / pattern filter — done in Python to avoid many-to-many SQL churn.
    if cfg.topic_kp_ids:
        kp_ids = {uuid.UUID(x) for x in cfg.topic_kp_ids}
        kp_rows = (await session.execute(
            select(QuestionKPLink.question_id).where(QuestionKPLink.kp_id.in_(kp_ids))
        )).scalars().all()
        allowed = set(kp_rows)
        rows = [r for r in rows if r.id in allowed]
    if cfg.pattern_ids:
        p_ids = {uuid.UUID(x) for x in cfg.pattern_ids}
        p_rows = (await session.execute(
            select(QuestionPatternLink.question_id).where(
                QuestionPatternLink.pattern_id.in_(p_ids)
            )
        )).scalars().all()
        allowed = set(p_rows)
        rows = [r for r in rows if r.id in allowed]

    return [r for r in rows if r.id not in exclude]


def _distribute(rng: random.Random, pool: list[Question],
                dist: dict[int, int]) -> list[Question]:
    """Pick questions honoring per-difficulty counts. Leftover slots
    fall back to any-difficulty picks from the remainder."""
    if not dist:
        return list(pool)
    picked: list[Question] = []
    remaining = list(pool)
    for diff, n in sorted(dist.items()):
        bucket = [q for q in remaining if q.difficulty == diff]
        rng.shuffle(bucket)
        take = bucket[:n]
        picked.extend(take)
        taken_ids = {q.id for q in take}
        remaining = [q for q in remaining if q.id not in taken_ids]
    return picked + remaining


def _answer_outline_from_package(pkg_json: dict | None) -> tuple[str, str]:
    """Extract a short answer_outline + rubric from stored AnswerPackage."""
    if not pkg_json:
        return "", ""
    try:
        pkg = AnswerPackage.model_validate(pkg_json)
    except Exception:
        return "", ""
    outline_lines = [
        f"{s.step_index}. {s.statement}" for s in pkg.solution_steps
    ]
    outline = "\n".join(outline_lines) or "(无步骤概要)"
    rubric_lines = [
        f"得分点: {kp}" for kp in pkg.key_points_of_answer
    ] + [f"易错: {p}" for p in pkg.method_pattern.pitfalls]
    rubric = "\n".join(rubric_lines) or "(无评分提示)"
    return outline, rubric


# ── Variant synthesis ───────────────────────────────────────────────


async def _synthesize_variants(
    llm: GeminiClient, *, source: Question, count: int,
    difficulty_target: int | None,
) -> list[VariantQuestion]:
    if count <= 0:
        return []
    pkg_json = source.answer_package_json
    if not pkg_json:
        return []
    try:
        pkg = AnswerPackage.model_validate(pkg_json)
    except Exception:
        return []

    source_payload = {
        "statement": (source.parsed_json or {}).get("question_text", ""),
        "subject": source.subject,
        "grade_band": source.grade_band,
        "difficulty": source.difficulty,
        "pattern_name": pkg.method_pattern.name_cn,
        "pattern_when_to_use": pkg.method_pattern.when_to_use,
        "pattern_procedure": pkg.method_pattern.general_procedure,
        "knowledge_points": [kp.node_ref for kp in pkg.knowledge_points],
    }
    template = VariantSynthPrompt()
    result = await llm.call_structured(
        template=template,
        model=settings.gemini.model_solver,
        model_cls=VariantList,
        template_kwargs={
            "source": source_payload,
            "count": count,
            "difficulty_target": difficulty_target,
        },
        prompt_context=PromptLogContext(
            phase_description="生成练习变式",
            question_id=str(source.id),
            related={"difficulty_target": difficulty_target, "count": count},
        ),
        timeout_s=settings.llm.solver_timeout_s,
    )
    # Keep only same_pattern=true items — hard constraint.
    return [v for v in result.variants if v.same_pattern]


# ── Public entry point ──────────────────────────────────────────────


async def build_exam(
    session: AsyncSession,
    *,
    cfg: ExamConfig,
    llm: GeminiClient,
) -> Exam:
    rng = random.Random(cfg.seed)

    candidates = await _pull_candidates(session, cfg, exclude=set())
    log.info("exam: %d candidates matched filters", len(candidates))

    # Bank-sourced items honoring difficulty distribution.
    picked = _distribute(rng, candidates, cfg.difficulty_dist)[: cfg.count]
    drafts: list[ExamItemDraft] = []
    for i, q in enumerate(picked):
        outline, rubric = _answer_outline_from_package(q.answer_package_json)
        drafts.append(ExamItemDraft(
            position=i + 1, source_question_id=q.id,
            synthesized_payload=None,
            answer_outline=outline, rubric=rubric,
        ))

    # Fill remaining slots with LLM variants built from representative
    # picked questions. If nothing was picked, the caller simply asked
    # for more than we have — skip synthesis silently.
    needed = cfg.count - len(drafts)
    if needed > 0 and cfg.allow_synthesis and picked:
        per_source = max(1, (needed + len(picked) - 1) // len(picked))
        synth_idx = len(drafts)
        for source in picked:
            if synth_idx >= cfg.count:
                break
            remaining = cfg.count - synth_idx
            take = min(per_source, remaining)
            try:
                variants = await _synthesize_variants(
                    llm, source=source, count=take, difficulty_target=None,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("variant synth failed for %s: %s", source.id, e)
                continue
            for v in variants:
                drafts.append(ExamItemDraft(
                    position=synth_idx + 1,
                    source_question_id=source.id,       # trace provenance
                    synthesized_payload={
                        "statement": v.statement,
                        "difficulty": v.difficulty,
                        "source_question_id": str(source.id),
                    },
                    answer_outline=v.answer_outline,
                    rubric=v.rubric,
                ))
                synth_idx += 1
                if synth_idx >= cfg.count:
                    break

    if not drafts:
        raise ValueError("exam: no candidates match filters and no synthesis source available")

    exam = Exam(
        name=cfg.name or "练习卷",
        config_json={
            "subjects": cfg.subjects,
            "grade_bands": cfg.grade_bands,
            "topic_kp_ids": cfg.topic_kp_ids,
            "pattern_ids": cfg.pattern_ids,
            "source_question_ids": cfg.source_question_ids,
            "count": cfg.count,
            "difficulty_dist": cfg.difficulty_dist,
            "allow_synthesis": cfg.allow_synthesis,
            "seed": cfg.seed,
        },
    )
    session.add(exam)
    await session.flush()

    for d in drafts:
        session.add(ExamItem(
            exam_id=exam.id,
            position=d.position,
            source_question_id=d.source_question_id
            if d.synthesized_payload is None else None,
            synthesized_payload_json=d.synthesized_payload,
            answer_outline=d.answer_outline,
            rubric=d.rubric,
        ))
    await session.flush()
    return exam


async def get_exam_detail(
    session: AsyncSession, exam_id: uuid.UUID,
) -> dict | None:
    exam = await session.get(Exam, exam_id)
    if exam is None:
        return None
    rows = (await session.execute(
        select(ExamItem).where(ExamItem.exam_id == exam_id)
        .order_by(ExamItem.position)
    )).scalars().all()

    items = []
    for item in rows:
        statement = ""
        if item.synthesized_payload_json:
            statement = item.synthesized_payload_json.get("statement", "")
        elif item.source_question_id:
            src = await session.get(Question, item.source_question_id)
            if src and src.parsed_json:
                statement = src.parsed_json.get("question_text", "")
        items.append({
            "id": str(item.id),
            "position": item.position,
            "source_question_id": (
                str(item.source_question_id) if item.source_question_id else None
            ),
            "synthesized": item.synthesized_payload_json is not None,
            "statement": statement,
            "answer_outline": item.answer_outline,
            "rubric": item.rubric,
        })
    return {
        "exam_id": str(exam.id),
        "name": exam.name,
        "config": exam.config_json,
        "created_at": exam.created_at.isoformat() if exam.created_at else None,
        "items": items,
    }
