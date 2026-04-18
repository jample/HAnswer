"""Ingest router — image upload + Parser (§6, §3.1)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.schemas import ParsedQuestion
from app.services.ingest_service import MIME_EXT, edit_parsed, ingest_image
from app.services.llm_client import LLMError
from app.services.llm_deps import get_llm_client

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

MAX_IMAGE_BYTES = 8 * 1024 * 1024


async def _session():
    async with session_scope() as s:
        yield s


@router.post("/image")
async def ingest_image_endpoint(
    file: UploadFile = File(...),
    subject_hint: str | None = Form(None),
    session: AsyncSession = Depends(_session),
    llm=Depends(get_llm_client),
) -> dict:
    if file.content_type not in MIME_EXT:
        raise HTTPException(415, f"Unsupported MIME: {file.content_type}")
    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image exceeds 8 MB")
    if subject_hint not in (None, "math", "physics"):
        raise HTTPException(400, "subject_hint must be 'math', 'physics', or omitted")

    try:
        result = await ingest_image(
            session,
            data=data,
            mime=file.content_type,
            llm=llm,
            subject_hint=subject_hint,
        )
    except LLMError as e:
        raise HTTPException(502, f"parser LLM failed: {e}")

    return {
        "question_id": str(result.question.id),
        "parsed": result.parsed.model_dump(mode="json"),
        "image_sha256": result.image.sha256,
        "deduped": result.deduped,
    }


@router.patch("/{question_id}")
async def edit_parsed_endpoint(
    question_id: UUID,
    patch: dict,
    session: AsyncSession = Depends(_session),
) -> dict:
    allowed = set(ParsedQuestion.model_fields.keys())
    unknown = set(patch) - allowed
    if unknown:
        raise HTTPException(400, f"unknown fields: {sorted(unknown)}")
    try:
        q = await edit_parsed(session, question_id=question_id, patch=patch)
    except KeyError:
        raise HTTPException(404, "question not found")
    except ValueError as e:
        raise HTTPException(422, f"invalid patch: {e}")
    return {"question_id": str(q.id), "parsed": q.parsed_json, "status": q.status}
