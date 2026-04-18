"""Persistent multi-turn dialog with rolling memory."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.config import settings
from app.db import repo
from app.db.models import (
    ConversationMemorySnapshot,
    ConversationMessage,
    ConversationSession,
)
from app.db.session import session_scope
from app.prompts import PromptRegistry
from app.schemas import AnswerPackage, ConversationTurnResult
from app.services.llm_client import GeminiClient

log = logging.getLogger(__name__)

_DEFAULT_TITLE = "新对话"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _clip(text: str, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _clip_items(items: list[str] | None, *, limit: int, item_limit: int = 240) -> list[str]:
    out: list[str] = []
    for raw in items or []:
        item = _clip(str(raw), item_limit)
        if item:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _normalize_title(title: str | None, *, fallback: str = _DEFAULT_TITLE) -> str:
    value = _clip(title or "", 80)
    return value or fallback


def _question_title_from_parsed(parsed_json: dict[str, Any] | None) -> str:
    text = ""
    if parsed_json:
        text = str(parsed_json.get("question_text") or "")
    return _normalize_title(_clip(text.replace("\n", " "), 36), fallback=_DEFAULT_TITLE)


def _compact_answer_context(answer_json: dict[str, Any]) -> dict[str, Any]:
    try:
        pkg = AnswerPackage.model_validate(answer_json)
    except Exception:  # noqa: BLE001
        return {"answer_available": True, "summary": "answer_package_json 存在, 但无法完整解析。"}

    return {
        "question_understanding": {
            "restated_question": _clip(pkg.question_understanding.restated_question, 800),
            "givens": _clip_items(pkg.question_understanding.givens, limit=8),
            "unknowns": _clip_items(pkg.question_understanding.unknowns, limit=8),
            "implicit_conditions": _clip_items(
                pkg.question_understanding.implicit_conditions, limit=8,
            ),
        },
        "key_points_of_question": _clip_items(pkg.key_points_of_question, limit=8),
        "key_points_of_answer": _clip_items(pkg.key_points_of_answer, limit=8),
        "method_pattern": {
            "name_cn": _clip(pkg.method_pattern.name_cn, 120),
            "when_to_use": _clip(pkg.method_pattern.when_to_use, 600),
            "general_procedure": _clip_items(pkg.method_pattern.general_procedure, limit=6),
            "pitfalls": _clip_items(pkg.method_pattern.pitfalls, limit=6),
        },
        "solution_steps": [
            {
                "step_index": step.step_index,
                "statement": _clip(step.statement, 200),
                "rationale": _clip(step.rationale, 320),
                "formula": _clip(step.formula, 180),
                "why_this_step": _clip(step.why_this_step, 220),
            }
            for step in pkg.solution_steps[:6]
        ],
        "self_check": _clip_items(pkg.self_check, limit=6),
    }


def _build_question_context(question) -> dict[str, Any]:
    parsed = question.parsed_json or {}
    ctx: dict[str, Any] = {
        "question_id": str(question.id),
        "subject": question.subject,
        "grade_band": question.grade_band,
        "difficulty": question.difficulty,
        "status": question.status,
        "parsed_question": {
            "topic_path": _clip_items(parsed.get("topic_path") or [], limit=8, item_limit=80),
            "question_text": _clip(str(parsed.get("question_text") or ""), 4000),
            "given": _clip_items(parsed.get("given") or [], limit=12, item_limit=240),
            "find": _clip_items(parsed.get("find") or [], limit=8, item_limit=240),
            "diagram_description": _clip(str(parsed.get("diagram_description") or ""), 1200),
            "tags": _clip_items(parsed.get("tags") or [], limit=12, item_limit=80),
        },
    }
    if question.answer_package_json:
        ctx["answer_context"] = _compact_answer_context(question.answer_package_json)

    serialized = json.dumps(ctx, ensure_ascii=False)
    if len(serialized) <= settings.dialog.max_question_context_chars:
        return ctx

    answer_context = ctx.get("answer_context")
    if isinstance(answer_context, dict):
        answer_context.pop("solution_steps", None)
        answer_context.pop("self_check", None)
    serialized = json.dumps(ctx, ensure_ascii=False)
    if len(serialized) <= settings.dialog.max_question_context_chars:
        return ctx

    ctx["parsed_question"]["question_text"] = _clip(
        ctx["parsed_question"]["question_text"],
        max(800, settings.dialog.max_question_context_chars // 2),
    )
    return ctx


def _serialize_message(row: ConversationMessage) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "role": row.role,
        "sequence_no": row.sequence_no,
        "content": row.content,
        "metadata": row.metadata_json,
        "created_at": row.created_at.isoformat(),
    }


def _serialize_session(row: ConversationSession) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "question_id": str(row.question_id) if row.question_id else None,
        "title": row.title,
        "latest_summary": row.latest_summary,
        "key_facts": list(row.key_facts_json or []),
        "open_questions": list(row.open_questions_json or []),
        "last_message_at": row.last_message_at.isoformat(),
        "created_at": row.created_at.isoformat(),
    }


async def list_sessions() -> list[dict[str, Any]]:
    async with session_scope() as session:
        rows = (await session.execute(
            select(ConversationSession).order_by(
                ConversationSession.last_message_at.desc(), ConversationSession.created_at.desc(),
            )
        )).scalars().all()
        return [_serialize_session(row) for row in rows]


async def create_session(*, title: str | None = None, question_id: uuid.UUID | None = None) -> dict[str, Any]:
    async with session_scope() as session:
        question = None
        resolved_title = _normalize_title(title)
        if question_id is not None:
            question = await repo.get_question(session, question_id)
            if question is None:
                raise KeyError(f"question {question_id} not found")
            if not title:
                resolved_title = _question_title_from_parsed(question.parsed_json)

        row = ConversationSession(
            question_id=question_id,
            title=resolved_title,
            last_message_at=_utcnow(),
        )
        session.add(row)
        await session.flush()
        question_context = _build_question_context(question) if question is not None else None
        return {
            "session": _serialize_session(row),
            "messages": [],
            "memory": {
                "summary": row.latest_summary,
                "key_facts": list(row.key_facts_json or []),
                "open_questions": list(row.open_questions_json or []),
            },
            "question_context": question_context,
        }


async def get_session_detail(conversation_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope() as session:
        convo = await session.get(ConversationSession, conversation_id)
        if convo is None:
            raise KeyError(f"conversation {conversation_id} not found")

        messages = (await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.sequence_no)
        )).scalars().all()

        question_context = None
        if convo.question_id:
            question = await repo.get_question(session, convo.question_id)
            if question is not None:
                question_context = _build_question_context(question)

        return {
            "session": _serialize_session(convo),
            "messages": [_serialize_message(msg) for msg in messages],
            "memory": {
                "summary": convo.latest_summary,
                "key_facts": list(convo.key_facts_json or []),
                "open_questions": list(convo.open_questions_json or []),
            },
            "question_context": question_context,
        }


async def get_dialog_stats() -> dict[str, int]:
    async with session_scope() as session:
        sessions_total = int((await session.execute(
            select(func.count(ConversationSession.id))
        )).scalar_one() or 0)
        question_linked_sessions = int((await session.execute(
            select(func.count(ConversationSession.id))
            .where(ConversationSession.question_id.is_not(None))
        )).scalar_one() or 0)
        messages_total = int((await session.execute(
            select(func.count(ConversationMessage.id))
        )).scalar_one() or 0)
        snapshots_total = int((await session.execute(
            select(func.count(ConversationMemorySnapshot.id))
        )).scalar_one() or 0)
        return {
            "sessions": sessions_total,
            "question_linked_sessions": question_linked_sessions,
            "messages": messages_total,
            "memory_snapshots": snapshots_total,
        }


async def append_message(
    *,
    conversation_id: uuid.UUID,
    content: str,
    llm: GeminiClient,
) -> dict[str, Any]:
    user_content = content.strip()
    if not user_content:
        raise ValueError("message content is empty")

    prompt_template = PromptRegistry.get("dialog")

    async with session_scope() as session:
        convo = await session.get(ConversationSession, conversation_id)
        if convo is None:
            raise KeyError(f"conversation {conversation_id} not found")
        session_title = convo.title

        question_context = None
        if convo.question_id:
            question = await repo.get_question(session, convo.question_id)
            if question is not None:
                question_context = _build_question_context(question)

        rows = (await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.sequence_no.desc())
            .limit(settings.dialog.recent_messages)
        )).scalars().all()
        recent_messages = [
            {"role": row.role, "content": _clip(row.content, 1200)}
            for row in reversed(rows)
        ]

        next_sequence = int((await session.execute(
            select(func.coalesce(func.max(ConversationMessage.sequence_no), 0))
            .where(ConversationMessage.conversation_id == conversation_id)
        )).scalar_one()) + 1

        user_row = ConversationMessage(
            conversation_id=conversation_id,
            role="user",
            sequence_no=next_sequence,
            content=user_content,
            metadata_json={"source": "ui"},
        )
        convo.last_message_at = _utcnow()
        session.add(user_row)
        await session.flush()

        prior_summary = _clip(convo.latest_summary or "", settings.dialog.max_summary_chars)
        prior_key_facts = _clip_items(
            list(convo.key_facts_json or []), limit=settings.dialog.max_key_facts,
        )
        prior_open_questions = _clip_items(
            list(convo.open_questions_json or []), limit=settings.dialog.max_open_questions,
        )

    llm_result: ConversationTurnResult
    try:
        llm_result = await llm.call_structured(
            template=prompt_template,
            model=settings.dialog.model_chat,
            model_cls=ConversationTurnResult,
            template_kwargs={
                "session_title": session_title,
                "question_context": question_context,
                "summary": prior_summary,
                "key_facts": prior_key_facts,
                "open_questions": prior_open_questions,
                "recent_messages": [
                    *recent_messages,
                    {"role": "user", "content": _clip(user_content, 1200)},
                ],
                "user_message": user_content,
            },
            timeout_s=settings.llm.dialog_timeout_s,
        )
    except Exception as exc:
        async with session_scope() as session:
            convo = await session.get(ConversationSession, conversation_id)
            if convo is None:
                raise
            err_seq = int((await session.execute(
                select(func.coalesce(func.max(ConversationMessage.sequence_no), 0))
                .where(ConversationMessage.conversation_id == conversation_id)
            )).scalar_one()) + 1
            convo.last_message_at = _utcnow()
            session.add(ConversationMessage(
                conversation_id=conversation_id,
                role="system",
                sequence_no=err_seq,
                content=f"对话生成失败: {exc}",
                metadata_json={"error": True},
            ))
        raise

    async with session_scope() as session:
        convo = await session.get(ConversationSession, conversation_id)
        if convo is None:
            raise KeyError(f"conversation {conversation_id} not found")

        assistant_sequence = int((await session.execute(
            select(func.coalesce(func.max(ConversationMessage.sequence_no), 0))
            .where(ConversationMessage.conversation_id == conversation_id)
        )).scalar_one()) + 1

        refreshed_summary = _clip(
            llm_result.memory.summary, settings.dialog.max_summary_chars,
        )
        refreshed_key_facts = _clip_items(
            llm_result.memory.key_facts, limit=settings.dialog.max_key_facts,
        )
        refreshed_open_questions = _clip_items(
            llm_result.memory.open_questions, limit=settings.dialog.max_open_questions,
        )
        suggested_title = _normalize_title(
            llm_result.title_suggested, fallback=convo.title or _DEFAULT_TITLE,
        )
        if convo.title == _DEFAULT_TITLE or not convo.title.strip():
            convo.title = suggested_title
        elif llm_result.title_suggested.strip():
            convo.title = suggested_title

        convo.latest_summary = refreshed_summary
        convo.key_facts_json = refreshed_key_facts
        convo.open_questions_json = refreshed_open_questions
        convo.last_message_at = _utcnow()

        assistant_row = ConversationMessage(
            conversation_id=conversation_id,
            role="assistant",
            sequence_no=assistant_sequence,
            content=llm_result.assistant_reply.strip(),
            metadata_json={
                "follow_up_suggestions": llm_result.follow_up_suggestions[:3],
            },
        )
        session.add(assistant_row)
        await session.flush()

        session.add(ConversationMemorySnapshot(
            conversation_id=conversation_id,
            sequence_no=assistant_sequence,
            summary=refreshed_summary,
            key_facts_json=refreshed_key_facts,
            open_questions_json=refreshed_open_questions,
        ))

        question_context = None
        if convo.question_id:
            question = await repo.get_question(session, convo.question_id)
            if question is not None:
                question_context = _build_question_context(question)

        messages = (await session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.sequence_no)
        )).scalars().all()

        return {
            "session": _serialize_session(convo),
            "assistant_message": _serialize_message(assistant_row),
            "memory": {
                "summary": refreshed_summary,
                "key_facts": refreshed_key_facts,
                "open_questions": refreshed_open_questions,
            },
            "follow_up_suggestions": llm_result.follow_up_suggestions[:3],
            "messages": [_serialize_message(msg) for msg in messages],
            "question_context": question_context,
        }
