"""Ingest service (M2, §3.1).

Pipeline: bytes → disk → Gemini Parser → ParsedQuestion → DB rows.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repo
from app.db.models import IngestImage, Question
from app.prompts import PromptRegistry
from app.schemas import ParsedQuestion
from app.services.llm_client import GeminiClient, PromptLogContext
from app.services.stage_review_service import ensure_parsed_stage_review

log = logging.getLogger(__name__)

MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "image/webp": "webp",
}

_PARSER_MAX_IMAGE_DIM = 2200
_PARSER_REENCODE_THRESHOLD_BYTES = 1_500_000
_PARSER_JPEG_QUALITY = 82


@dataclass
class IngestResult:
    question: Question
    image: IngestImage
    parsed: ParsedQuestion
    deduped: bool  # True if a prior question with same image hash was reused


def _prepare_parser_image(
    data: bytes,
    mime: str,
) -> tuple[bytes, str, dict[str, object]]:
    details: dict[str, object] = {
        "original_mime": mime,
        "original_bytes": len(data),
        "preprocessed": False,
    }
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except Exception as exc:  # noqa: BLE001
        details["reason"] = f"pillow_unavailable:{exc.__class__.__name__}"
        return data, mime, details

    try:
        with Image.open(BytesIO(data)) as src:
            image = ImageOps.exif_transpose(src)
            orig_w, orig_h = image.size
            details["original_size"] = [orig_w, orig_h]
            needs_resize = max(orig_w, orig_h) > _PARSER_MAX_IMAGE_DIM
            needs_reencode = (
                mime != "image/jpeg"
                or len(data) > _PARSER_REENCODE_THRESHOLD_BYTES
                or image.mode not in {"RGB", "L"}
            )
            if not needs_resize and not needs_reencode:
                details["reason"] = "kept_original"
                return data, mime, details

            image = image.copy()
            if needs_resize:
                image.thumbnail(
                    (_PARSER_MAX_IMAGE_DIM, _PARSER_MAX_IMAGE_DIM),
                    Image.Resampling.LANCZOS,
                )
            if image.mode not in {"RGB", "L"}:
                background = Image.new("RGB", image.size, (255, 255, 255))
                alpha = image.getchannel("A") if "A" in image.getbands() else None
                background.paste(image.convert("RGBA"), mask=alpha)
                image = background
            elif image.mode == "L":
                image = image.convert("RGB")

            buf = BytesIO()
            image.save(
                buf,
                format="JPEG",
                quality=_PARSER_JPEG_QUALITY,
                optimize=True,
            )
            new_bytes = buf.getvalue()
            details["parser_size"] = [image.size[0], image.size[1]]
            details["parser_bytes"] = len(new_bytes)
            details["preprocessed"] = True
            details["reason"] = "resized_or_reencoded_for_parser"

            if not needs_resize and len(new_bytes) >= len(data):
                details["preprocessed"] = False
                details["reason"] = "kept_original_smaller_than_jpeg"
                return data, mime, details
            return new_bytes, "image/jpeg", details
    except UnidentifiedImageError:
        details["reason"] = "unidentified_image_kept_original"
        return data, mime, details
    except Exception as exc:  # noqa: BLE001
        details["reason"] = f"preprocess_failed:{exc.__class__.__name__}"
        return data, mime, details


async def _parse_image_with_llm(
    *,
    llm: GeminiClient,
    parser,
    image_bytes: bytes,
    mime: str,
    subject_hint: str | None,
    user_guidance: str | None,
    image_name: str,
    phase_description: str,
    question_id: uuid.UUID | None = None,
) -> ParsedQuestion:
    kwargs = {"subject_hint": subject_hint} if subject_hint else {}
    parser_bytes, parser_mime, preprocess = _prepare_parser_image(image_bytes, mime)
    messages = parser.build_multimodal(parser_bytes, parser_mime, **kwargs)
    if user_guidance and user_guidance.strip():
        messages.append({
            "role": "user",
            "content": (
                "以下是用户在人工审核阶段给出的额外解析要求。"
                "请在不违背图片内容和 JSON Schema 的前提下遵守：\n"
                f"{user_guidance.strip()}"
            ),
        })

    log.info(
        "parser request phase=%s question_id=%s original_mime=%s parser_mime=%s "
        "original_bytes=%s parser_bytes=%s preprocessed=%s reason=%s",
        phase_description,
        str(question_id) if question_id else "-",
        mime,
        parser_mime,
        len(image_bytes),
        len(parser_bytes),
        preprocess.get("preprocessed"),
        preprocess.get("reason"),
    )
    t0 = time.perf_counter()
    parsed = await llm.call_structured(
        template=parser,
        model=settings.llm_model("parser"),
        model_cls=ParsedQuestion,
        template_kwargs=kwargs,
        messages_override=messages,
        prompt_context=PromptLogContext(
            phase_description=phase_description,
            question_id=str(question_id) if question_id else None,
            image_names=[image_name],
            related={
                "subject_hint": subject_hint,
                "user_guidance": user_guidance or "",
                "parser_mime": parser_mime,
                "parser_bytes": len(parser_bytes),
                "preprocessed": bool(preprocess.get("preprocessed")),
                "preprocess_reason": str(preprocess.get("reason") or ""),
            },
        ),
        timeout_s=settings.llm.parser_timeout_s,
        stream=False,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    log.info(
        "parser completed phase=%s question_id=%s elapsed_ms=%s subject=%s grade_band=%s difficulty=%s "
        "confidence=%.3f",
        phase_description,
        str(question_id) if question_id else "-",
        elapsed_ms,
        parsed.subject,
        parsed.grade_band,
        parsed.difficulty,
        parsed.confidence,
    )
    return parsed


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
    user_guidance: str | None = None,
    image_name: str | None = None,
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
    parsed = await _parse_image_with_llm(
        llm=llm,
        parser=parser,
        image_bytes=data,
        mime=mime,
        subject_hint=subject_hint,
        user_guidance=user_guidance,
        image_name=image_name or path.name,
        phase_description="解析题目",
    )

    question = await repo.create_question_from_parsed(
        session,
        image_id=image_row.id,
        parsed=parsed,
        dedup_hash=sha,
    )
    await ensure_parsed_stage_review(session, question=question, review_note=user_guidance)
    return IngestResult(question, image_row, parsed, deduped=False)


async def edit_parsed(
    session: AsyncSession, *, question_id: uuid.UUID, patch: dict,
) -> Question:
    return await repo.update_parsed(session, question_id=question_id, patch=patch)


async def rescan_question(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    llm: GeminiClient,
    subject_hint: str | None = None,
    user_guidance: str | None = None,
) -> IngestResult:
    q = await repo.get_question(session, question_id)
    if q is None:
        raise KeyError(f"question {question_id} not found")

    image_row = await repo.get_image_for_question(session, question_id)
    if image_row is None:
        raise FileNotFoundError(f"question {question_id} has no stored source image")

    image_path = Path(image_row.path)
    if not image_path.exists():
        raise FileNotFoundError(f"source image missing on disk: {image_row.path}")

    data = image_path.read_bytes()
    parser = PromptRegistry.get("parser")
    parsed = await _parse_image_with_llm(
        llm=llm,
        parser=parser,
        image_bytes=data,
        mime=image_row.mime,
        subject_hint=subject_hint,
        user_guidance=user_guidance,
        image_name=image_path.name,
        phase_description="重新解析题目",
        question_id=question_id,
    )

    await repo.clear_generated_content(session, question_id=question_id)
    question = await repo.replace_parsed(session, question_id=question_id, parsed=parsed)
    await ensure_parsed_stage_review(session, question=question, review_note=user_guidance)
    return IngestResult(question, image_row, parsed, deduped=False)


async def replace_question_image(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    data: bytes,
    mime: str,
    llm: GeminiClient,
    subject_hint: str | None = None,
    user_guidance: str | None = None,
    image_name: str | None = None,
) -> IngestResult:
    if mime not in MIME_EXT:
        raise ValueError(f"unsupported mime: {mime}")

    q = await repo.get_question(session, question_id)
    if q is None:
        raise KeyError(f"question {question_id} not found")

    sha = repo.sha256_bytes(data)
    path = _persist_blob(data, mime, sha)
    image_row = await repo.save_image_blob(
        session, path=path, mime=mime, size=len(data), sha=sha,
    )

    parser = PromptRegistry.get("parser")
    parsed = await _parse_image_with_llm(
        llm=llm,
        parser=parser,
        image_bytes=data,
        mime=mime,
        subject_hint=subject_hint,
        user_guidance=user_guidance,
        image_name=image_name or path.name,
        phase_description="替换图片后解析题目",
        question_id=question_id,
    )

    await repo.clear_generated_content(session, question_id=question_id)
    await repo.set_question_image(
        session,
        question_id=question_id,
        image_id=image_row.id,
        dedup_hash=sha,
    )
    question = await repo.replace_parsed(session, question_id=question_id, parsed=parsed)
    await ensure_parsed_stage_review(session, question=question, review_note=user_guidance)
    return IngestResult(question, image_row, parsed, deduped=False)
