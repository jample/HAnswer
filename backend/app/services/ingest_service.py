"""Ingest service (M2, §3.1).

Pipeline: bytes → disk → Gemini Parser → ParsedQuestion → DB rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repo
from app.db.models import IngestImage, Question
from app.prompts import PromptRegistry
from app.schemas import ParsedQuestion
from app.services.llm_client import GeminiClient

MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "image/webp": "webp",
}


@dataclass
class IngestResult:
    question: Question
    image: IngestImage
    parsed: ParsedQuestion
    deduped: bool  # True if a prior question with same image hash was reused


def _persist_blob(data: bytes, mime: str, sha: str) -> Path:
    ext = MIME_EXT[mime]
    root = Path(settings.storage.image_dir)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{sha}.{ext}"
    if not dest.exists():
        dest.write_bytes(data)
    return dest


async def ingest_image(
    session: AsyncSession,
    *,
    data: bytes,
    mime: str,
    llm: GeminiClient,
    subject_hint: str | None = None,
) -> IngestResult:
    """End-to-end ingest: blob → parser → persistence.

    Dedup is by image SHA-256 (§3.1): a second upload of the same file
    short-circuits to the existing question with `seen_count += 1`.
    """
    if mime not in MIME_EXT:
        raise ValueError(f"unsupported mime: {mime}")

    sha = repo.sha256_bytes(data)
    path = _persist_blob(data, mime, sha)

    # Dedup path: same image already parsed.
    existing_img = await repo.get_image_by_sha(session, sha)
    if existing_img is not None:
        existing_q = await repo.get_question_by_dedup(session, sha)
        if existing_q is not None:
            existing_q.seen_count += 1
            await session.flush()
            parsed = ParsedQuestion.model_validate(existing_q.parsed_json)
            return IngestResult(existing_q, existing_img, parsed, deduped=True)

    image_row = await repo.save_image_blob(
        session, path=path, mime=mime, size=len(data), sha=sha,
    )

    parser = PromptRegistry.get("parser")
    kwargs = {"subject_hint": subject_hint} if subject_hint else {}
    messages = parser.build_multimodal(data, mime, **kwargs)

    parsed = await llm.call_structured(
        template=parser,
        model=settings.gemini.model_parser,
        model_cls=ParsedQuestion,
        template_kwargs=kwargs,
        messages_override=messages,
    )

    question = await repo.create_question_from_parsed(
        session,
        image_id=image_row.id,
        parsed=parsed,
        dedup_hash=sha,
    )
    return IngestResult(question, image_row, parsed, deduped=False)


async def edit_parsed(
    session: AsyncSession, *, question_id: uuid.UUID, patch: dict,
) -> Question:
    return await repo.update_parsed(session, question_id=question_id, patch=patch)
