"""Dialog router tests."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.db import models
from app.db.session import session_scope
from app.main import app
from app.services.llm_client import FakeTransport, GeminiClient
from app.services.llm_deps import set_llm_client


def _dialog_response() -> str:
    return json.dumps(
        {
            "title_suggested": "顶点式追问",
            "assistant_reply": "把二次函数化成顶点式 $y=a(x-h)^2+k$ 后, 顶点就是 $(h,k)$。",
            "follow_up_suggestions": ["继续问如何配方", "比较一般式与顶点式", "追问对称轴"],
            "memory": {
                "summary": "本轮已经解释顶点式与顶点坐标的关系。",
                "key_facts": ["题目主题是二次函数顶点式", "用户关心如何读取顶点坐标"],
                "open_questions": ["用户可能继续问如何从一般式配方得到顶点式"],
            },
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_dialog_session_persists_messages_and_memory():
    marker = f"dialog-{uuid.uuid4().hex[:8]}"
    qid = None
    sid: uuid.UUID | None = None
    set_llm_client(GeminiClient(FakeTransport({"gemini-3.1-pro-preview": _dialog_response()})))

    async with session_scope() as s:
        q = models.Question(
            parsed_json={
                "subject": "math",
                "grade_band": "senior",
                "topic_path": ["代数", "二次函数"],
                "question_text": f"{marker}: 已知抛物线, 求顶点坐标。",
                "given": ["$y=x^2-4x+3$"],
                "find": ["顶点坐标"],
                "diagram_description": "",
                "difficulty": 2,
                "tags": ["二次函数"],
                "confidence": 0.9,
            },
            answer_package_json=None,
            subject="math",
            grade_band="senior",
            difficulty=2,
            dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
            seen_count=1,
            status="answered",
        )
        s.add(q)
        await s.flush()
        qid = q.id

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            create_res = await c.post("/api/dialog/sessions", json={"question_id": str(qid)})
            assert create_res.status_code == 200, create_res.text
            created = create_res.json()
            sid = uuid.UUID(created["session"]["id"])
            assert created["session"]["question_id"] == str(qid)
            assert created["question_context"]["parsed_question"]["question_text"].startswith(marker)

            msg_res = await c.post(
                f"/api/dialog/sessions/{sid}/messages",
                json={"content": "继续解释为什么要先化成顶点式"},
            )
            assert msg_res.status_code == 200, msg_res.text
            body = msg_res.json()
            assert body["assistant_message"]["role"] == "assistant"
            assert "顶点式" in body["assistant_message"]["content"]
            assert body["memory"]["summary"]
            assert len(body["messages"]) == 2

            detail_res = await c.get(f"/api/dialog/sessions/{sid}")
            assert detail_res.status_code == 200, detail_res.text
            detail = detail_res.json()
            assert len(detail["messages"]) == 2
            assert detail["memory"]["key_facts"]

            stats_res = await c.get("/api/dialog/stats")
            assert stats_res.status_code == 200
            stats = stats_res.json()
            assert stats["sessions"] >= 1
            assert stats["messages"] >= 2
            assert stats["memory_snapshots"] >= 1
    finally:
        set_llm_client(None)
        async with session_scope() as s:
            if sid is not None:
                await s.execute(delete(models.ConversationMemorySnapshot).where(
                    models.ConversationMemorySnapshot.conversation_id == sid
                ))
                await s.execute(delete(models.ConversationMessage).where(
                    models.ConversationMessage.conversation_id == sid
                ))
                await s.execute(delete(models.ConversationSession).where(
                    models.ConversationSession.id == sid
                ))
            if qid is not None:
                await s.execute(delete(models.Question).where(models.Question.id == qid))
            await s.commit()


@pytest.mark.asyncio
async def test_dialog_session_404_for_missing_session():
    set_llm_client(GeminiClient(FakeTransport({"gemini-3.1-pro-preview": _dialog_response()})))
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            res = await c.get(f"/api/dialog/sessions/{uuid.uuid4()}")
            assert res.status_code == 404
    finally:
        set_llm_client(None)
