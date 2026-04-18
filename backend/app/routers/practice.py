"""Practice router — exam generation (§6, §3.5, M7)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.services.exam_service import ExamConfig, build_exam, get_exam_detail
from app.services.llm_client import GeminiClient
from app.services.llm_deps import get_llm_client

router = APIRouter(prefix="/api/practice", tags=["practice"])


async def _session() -> AsyncSession:  # type: ignore[override]
    async with session_scope() as s:
        yield s


class ExamRequest(BaseModel):
    name: str = "练习卷"
    sources: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    grade_bands: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)           # KP ids
    patterns: list[str] = Field(default_factory=list)         # pattern ids
    count: int = Field(default=5, ge=1, le=50)
    difficulty_dist: dict[int, int] = Field(default_factory=dict)
    allow_synthesis: bool = True
    seed: int | None = None


@router.post("/exam")
async def create_exam(
    req: ExamRequest,
    session: AsyncSession = Depends(_session),
    llm: GeminiClient = Depends(get_llm_client),
) -> dict:
    cfg = ExamConfig(
        name=req.name,
        subjects=req.subjects,
        grade_bands=req.grade_bands,
        topic_kp_ids=req.topics,
        pattern_ids=req.patterns,
        source_question_ids=req.sources,
        count=req.count,
        difficulty_dist=req.difficulty_dist,
        allow_synthesis=req.allow_synthesis,
        seed=req.seed,
    )
    try:
        exam = await build_exam(session, cfg=cfg, llm=llm)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    detail = await get_exam_detail(session, exam.id)
    return detail or {"exam_id": str(exam.id), "items": []}


@router.get("/exam/{exam_id}")
async def read_exam(
    exam_id: UUID, session: AsyncSession = Depends(_session),
) -> dict:
    detail = await get_exam_detail(session, exam_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="exam not found")
    return detail
