"""Repository layer for ingest & question persistence (M2, §3.1 + §5.5.1).

Thin async functions over SQLAlchemy — keeps routers and services free
of session/transaction boilerplate.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IngestImage, Question
from app.schemas import ParsedQuestion


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def get_image_by_sha(session: AsyncSession, sha: str) -> IngestImage | None:
    stmt = select(IngestImage).where(IngestImage.sha256 == sha).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def save_image_blob(
    session: AsyncSession,
    *,
    path: Path,
    mime: str,
    size: int,
    sha: str,
) -> IngestImage:
    existing = await get_image_by_sha(session, sha)
    if existing:
        return existing
    row = IngestImage(path=str(path), mime=mime, size=size, sha256=sha)
    session.add(row)
    await session.flush()
    return row


async def get_question_by_dedup(session: AsyncSession, dedup_hash: str) -> Question | None:
    stmt = select(Question).where(Question.dedup_hash == dedup_hash).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_question_from_parsed(
    session: AsyncSession,
    *,
    image_id: uuid.UUID | None,
    parsed: ParsedQuestion,
    dedup_hash: str,
) -> Question:
    """Create a draft question from a ParsedQuestion.

    If a question with the same dedup_hash already exists, bump its
    `seen_count` instead and return it (§3.1 dedup).
    """
    existing = await get_question_by_dedup(session, dedup_hash)
    if existing:
        existing.seen_count += 1
        await session.flush()
        return existing

    row = Question(
        image_id=image_id,
        parsed_json=parsed.model_dump(mode="json"),
        subject=parsed.subject,
        grade_band=parsed.grade_band,
        difficulty=parsed.difficulty,
        dedup_hash=dedup_hash,
        status="parsed",
    )
    session.add(row)
    await session.flush()
    return row


async def get_question(session: AsyncSession, question_id: uuid.UUID) -> Question | None:
    return await session.get(Question, question_id)


async def update_parsed(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    patch: dict,
) -> Question:
    """Merge a partial ParsedQuestion patch onto the stored parsed_json.

    Validates the merged document against the pydantic model so we never
    persist a malformed ParsedQuestion.
    """
    q = await session.get(Question, question_id)
    if q is None:
        raise KeyError(f"question {question_id} not found")
    merged = {**q.parsed_json, **patch}
    validated = ParsedQuestion.model_validate(merged)
    q.parsed_json = validated.model_dump(mode="json")
    # Keep denormalized mirror columns aligned.
    q.subject = validated.subject
    q.grade_band = validated.grade_band
    q.difficulty = validated.difficulty
    await session.flush()
    return q
