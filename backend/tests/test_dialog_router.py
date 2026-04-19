"""Dialog router tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.db import models
from app.db.session import session_scope
from app.main import app
from app.services.llm_client import FakeTransport, GeminiClient
from app.services.llm_client import StreamChunk
from app.services.llm_deps import set_llm_client


def _answer_package(marker: str) -> dict:
    return {
        "question_understanding": {
            "restated_question": f"{marker}: 求抛物线顶点坐标",
            "givens": ["$y=x^2-4x+3$"],
            "unknowns": ["顶点坐标"],
            "implicit_conditions": [],
        },
        "key_points_of_question": ["识别二次函数并转为顶点式"],
        "solution_steps": [
            {
                "step_index": 1,
                "statement": "把函数配方成顶点式。",
                "rationale": "顶点式能直接读出顶点坐标。",
                "formula": "$y=(x-2)^2-1$",
                "why_this_step": "这是当前解法的核心入口。",
                "viz_ref": "",
            }
        ],
        "key_points_of_answer": ["顶点是 $(2,-1)$"],
        "method_pattern": {
            "pattern_id_suggested": "new:二次函数>配方法",
            "name_cn": "配方法",
            "when_to_use": "当需要从一般式快速识别顶点或最值时。",
            "general_procedure": ["提出二次项系数", "配方", "改写成顶点式"],
            "pitfalls": ["常数项补偿容易漏掉"],
        },
        "similar_questions": [
            {
                "statement": "已知 $y=x^2+2x+5$, 求顶点。",
                "answer_outline": "配方法。",
                "same_pattern": True,
                "difficulty_delta": -1,
            },
            {
                "statement": "已知 $y=2x^2-8x+1$, 求最小值。",
                "answer_outline": "配方法后读最值。",
                "same_pattern": True,
                "difficulty_delta": 0,
            },
            {
                "statement": "已知 $y=-x^2+4x-7$, 求顶点和对称轴。",
                "answer_outline": "配方法并结合开口方向。",
                "same_pattern": True,
                "difficulty_delta": 1,
            },
        ],
        "knowledge_points": [
            {"node_ref": "new:代数>二次函数>顶点式", "weight": 1.0},
        ],
        "self_check": ["再核对平方展开是否正确。"],
    }


class _FailingDialogTransport:
    async def generate_json(self, *, model, messages, response_schema, timeout_s):
        raise RuntimeError("dialog transport failure")

    async def generate_json_stream(self, *, model, messages, response_schema, timeout_s):
        raise RuntimeError("dialog transport failure")

    async def generate_json_stream_iter(self, *, model, messages, response_schema, timeout_s):
        raise RuntimeError("dialog transport failure")

    async def embed(self, *, model, texts, task_type=None):
        return []


class _DelayedDialogTransport:
    def __init__(self, response_json: str):
        self._response_json = response_json
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_json(self, *, model, messages, response_schema, timeout_s):
        self.started.set()
        await self.release.wait()
        return self._response_json, 0, 0

    async def generate_json_stream(self, *, model, messages, response_schema, timeout_s):
        self.started.set()
        await self.release.wait()
        return self._response_json, 0, 0

    async def generate_json_stream_iter(self, *, model, messages, response_schema, timeout_s):
        self.started.set()
        await self.release.wait()
        yield StreamChunk(text=self._response_json, prompt_tokens=0, completion_tokens=0)

    async def embed(self, *, model, texts, task_type=None):
        return []


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
    solution_id = None
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
            status="review_solve",
        )
        s.add(q)
        await s.flush()
        qid = q.id
        solution = models.QuestionSolution(
            question_id=qid,
            ordinal=1,
            title="解法 1",
            is_current=True,
            status="review_solve",
            answer_package_json=_answer_package(marker),
            visualizations_json=[],
            sediment_json=None,
            stage_reviews_json={},
        )
        s.add(solution)
        await s.flush()
        solution_id = solution.id

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            create_res = await c.post(
                "/api/dialog/sessions",
                json={"question_id": str(qid), "solution_id": str(solution_id)},
            )
            assert create_res.status_code == 200, create_res.text
            created = create_res.json()
            sid = uuid.UUID(created["session"]["id"])
            assert created["session"]["question_id"] == str(qid)
            assert created["session"]["solution_id"] == str(solution_id)
            assert created["question_context"]["parsed_question"]["question_text"].startswith(marker)
            assert created["question_context"]["solution_id"] == str(solution_id)
            assert created["question_context"]["answer_context"]["method_pattern"]["name_cn"] == "配方法"

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
            if solution_id is not None:
                await s.execute(delete(models.QuestionSolution).where(
                    models.QuestionSolution.id == solution_id
                ))
            if qid is not None:
                await s.execute(delete(models.Question).where(models.Question.id == qid))
            await s.commit()


@pytest.mark.asyncio
async def test_dialog_session_requires_completed_answer_solution():
    marker = f"dialog-unsolved-{uuid.uuid4().hex[:8]}"
    qid = None
    try:
        async with session_scope() as s:
            q = models.Question(
                parsed_json={
                    "subject": "math",
                    "grade_band": "senior",
                    "topic_path": ["代数"],
                    "question_text": f"{marker}: 求解一元二次方程。",
                    "given": ["$x^2-4x+3=0$"],
                    "find": ["x 的值"],
                    "diagram_description": "",
                    "difficulty": 2,
                    "tags": [],
                    "confidence": 0.9,
                },
                answer_package_json=None,
                subject="math",
                grade_band="senior",
                difficulty=2,
                dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
                seen_count=1,
                status="parsed",
            )
            s.add(q)
            await s.flush()
            qid = q.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            res = await c.post("/api/dialog/sessions", json={"question_id": str(qid)})
            assert res.status_code == 400
            assert "completed answer solution" in res.text
    finally:
        async with session_scope() as s:
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


@pytest.mark.asyncio
async def test_dialog_failure_keeps_atomic_placeholder_instead_of_orphaning_user_message():
    sid: uuid.UUID | None = None
    set_llm_client(GeminiClient(_FailingDialogTransport()))

    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            create_res = await c.post("/api/dialog/sessions", json={"title": "失败测试"})
            assert create_res.status_code == 200, create_res.text
            sid = uuid.UUID(create_res.json()["session"]["id"])

            msg_res = await c.post(
                f"/api/dialog/sessions/{sid}/messages",
                json={"content": "这次会失败吗"},
            )
            assert msg_res.status_code == 500

            detail_res = await c.get(f"/api/dialog/sessions/{sid}")
            assert detail_res.status_code == 200, detail_res.text
            detail = detail_res.json()
            assert len(detail["messages"]) == 2
            assert detail["messages"][0]["role"] == "user"
            assert detail["messages"][1]["role"] == "system"
            assert detail["messages"][1]["metadata"]["error"] is True
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
            await s.commit()


@pytest.mark.asyncio
async def test_dialog_success_persists_messages_only_after_llm_finishes():
    sid: uuid.UUID | None = None
    delayed = _DelayedDialogTransport(_dialog_response())
    set_llm_client(GeminiClient(delayed))

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            create_res = await c.post("/api/dialog/sessions", json={"title": "原子成功测试"})
            assert create_res.status_code == 200, create_res.text
            sid = uuid.UUID(create_res.json()["session"]["id"])

            task = asyncio.create_task(c.post(
                f"/api/dialog/sessions/{sid}/messages",
                json={"content": "先别写入数据库，等 LLM 完成"},
            ))
            await delayed.started.wait()

            async with session_scope() as s:
                count = int((await s.execute(
                    select(func.count(models.ConversationMessage.id))
                    .where(models.ConversationMessage.conversation_id == sid)
                )).scalar_one() or 0)
                assert count == 0

            delayed.release.set()
            msg_res = await task
            assert msg_res.status_code == 200, msg_res.text

            detail_res = await c.get(f"/api/dialog/sessions/{sid}")
            assert detail_res.status_code == 200, detail_res.text
            detail = detail_res.json()
            assert [item["role"] for item in detail["messages"]] == ["user", "assistant"]
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
            await s.commit()
