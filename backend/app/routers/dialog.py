"""Persistent multi-turn dialog API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services.dialog_service import (
    append_message,
    create_session,
    get_dialog_stats,
    get_session_detail,
    list_sessions,
)
from app.services.llm_deps import get_llm_client

router = APIRouter(prefix="/api/dialog", tags=["dialog"])
LLM_DEP = Depends(get_llm_client)


class CreateDialogSessionRequest(BaseModel):
    title: str | None = None
    question_id: UUID | None = None


class CreateDialogMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


@router.get("/sessions")
async def list_dialog_sessions() -> dict:
    return {"sessions": await list_sessions()}


@router.post("/sessions")
async def create_dialog_session(payload: CreateDialogSessionRequest) -> dict:
    try:
        return await create_session(title=payload.title, question_id=payload.question_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/sessions/{conversation_id}")
async def get_dialog_session(conversation_id: UUID) -> dict:
    try:
        return await get_session_detail(conversation_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/sessions/{conversation_id}/messages")
async def create_dialog_message(
    conversation_id: UUID,
    payload: CreateDialogMessageRequest,
    llm=LLM_DEP,
) -> dict:
    try:
        return await append_message(
            conversation_id=conversation_id,
            content=payload.content,
            llm=llm,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/stats")
async def dialog_stats() -> dict:
    return await get_dialog_stats()
