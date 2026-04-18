"""Sediment + retrieval tests (M5 + M6).

Uses the real local PostgreSQL (§Appendix B) and the InMemoryVectorStore
so the tests don't require a running Milvus. FakeTransport is wired to
return embeddings directly from a canned dictionary indexed by the text
passed in, which lets us assert on near-duplicate behavior and rerank
math deterministically.
"""

from __future__ import annotations

import json
import math

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import models, repo
from app.schemas import AnswerPackage
from app.services.embedding import EmbeddingService
from app.services.ingest_service import ingest_image
from app.services.llm_client import FakeTransport, GeminiClient
from app.services.question_solution_service import create_solution
from app.services.retrieval_service import SimilarQuery, similar_questions
from app.services.sediment_service import NEAR_DUP_THRESHOLD, sediment
from app.services.solver_service import generate_answer
from app.services.vector_store import InMemoryVectorStore

# ── Fake embedding transport ────────────────────────────────────────


class _CannedEmbedTransport(FakeTransport):
    """Returns a fixed vector per input string.

    Unknown strings map to a unique deterministic random-like vector so
    cosine between distinct inputs is low but nonzero.
    """

    def __init__(self, vectors: dict[str, list[float]], json_by_model=None):
        super().__init__(json_by_model=json_by_model)
        self.vectors = vectors
        self._fallback_counter = 0

    async def embed(self, *, model, texts, task_type=None):
        out = []
        dim = settings.gemini.embed_dim
        for t in texts:
            if t in self.vectors:
                out.append(list(self.vectors[t]))
                continue
            # Deterministic-ish unit vector from hash, ignoring content.
            self._fallback_counter += 1
            base = [0.0] * dim
            base[self._fallback_counter % dim] = 1.0
            out.append(base)
        return out


def _unit(values: list[float]) -> list[float]:
    n = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / n for v in values]


def _pad(v: list[float]) -> list[float]:
    dim = settings.gemini.embed_dim
    return _unit(v + [0.0] * (dim - len(v)))


# ── Fixtures ────────────────────────────────────────────────────────


_PARSED_A = {
    "subject": "math", "grade_band": "senior",
    "topic_path": ["代数", "方程", "一元二次方程"],
    "question_text": "解 x^2 - 5x + 6 = 0",
    "given": ["二次方程 x^2 - 5x + 6 = 0"],
    "find": ["x 的实数解"],
    "diagram_description": "",
    "difficulty": 2, "tags": [], "confidence": 0.95,
}

_PARSED_B = dict(_PARSED_A, question_text="解 x^2 - 7x + 12 = 0",
                 given=["x^2 - 7x + 12 = 0"], find=["x 的实数解"])
_PARSED_B_NEAR = dict(_PARSED_A, question_text="解 x^2 - 5x + 6.0 = 0",
                      given=["接近 A 的题面"], find=["x 的实数解"])

_ANSWER_PACKAGE_A = {
    "question_understanding": {
        "restated_question": "求解一元二次方程",
        "givens": ["a=1,b=-5,c=6"], "unknowns": ["x"],
        "implicit_conditions": ["x 实数"],
    },
    "key_points_of_question": ["识别二次", "因式分解"],
    "solution_steps": [
        {"step_index": 1, "statement": "因式分解",
         "rationale": "(x-2)(x-3)", "formula": "", "why_this_step": "积零",
         "viz_ref": ""},
    ],
    "key_points_of_answer": ["两实根"],
    "method_pattern": {
        "pattern_id_suggested": "new:代数>方程>一元二次方程>因式分解法",
        "name_cn": "因式分解法",
        "when_to_use": "系数较小",
        "general_procedure": ["观察系数", "十字相乘"],
        "pitfalls": [],
    },
    "similar_questions": [
        {"statement": "x^2-7x+12=0", "answer_outline": "(x-3)(x-4)=0",
         "same_pattern": True, "difficulty_delta": 0},
        {"statement": "x^2+x-6=0", "answer_outline": "(x+3)(x-2)=0",
         "same_pattern": True, "difficulty_delta": 0},
        {"statement": "2x^2-5x+2=0", "answer_outline": "(2x-1)(x-2)=0",
         "same_pattern": True, "difficulty_delta": 1},
    ],
    "knowledge_points": [
        {"node_ref": "new:代数>方程>一元二次方程", "weight": 0.9},
        {"node_ref": "new:代数>因式分解", "weight": 0.6},
    ],
    "self_check": ["代回验证"],
}


async def _seed_question(session, parsed: dict, *, marker: bytes) -> str:
    llm = GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_parser: json.dumps(parsed)}
    ))
    res = await ingest_image(
        session, data=b"\x89PNG" + marker, mime="image/png", llm=llm,
    )
    return res.question.id


async def _run_solver(session, qid, llm) -> AnswerPackage:
    async for _ in generate_answer(session, question_id=qid, llm=llm):
        pass
    q = await repo.get_question(session, qid)
    assert q is not None and q.answer_package_json is not None
    return AnswerPackage.model_validate(q.answer_package_json)


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sediment_creates_pattern_and_kp_rows(session, tmp_image_dir):
    qid = await _seed_question(session, _PARSED_A, marker=b"sed-a")

    solver_llm = GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_solver: json.dumps(_ANSWER_PACKAGE_A)}
    ))
    pkg = await _run_solver(session, qid, solver_llm)

    vs = InMemoryVectorStore()
    embed_llm = GeminiClient(_CannedEmbedTransport(vectors={
        _PARSED_A["question_text"]: _pad([1.0, 0.0]),
    }))
    result = await sediment(
        session, question_id=qid, package=pkg,
        embedding=EmbeddingService(llm=embed_llm), vector_store=vs,
    )

    # Pattern row created pending.
    mp = await session.get(models.MethodPatternRow, result.pattern_id)
    assert mp is not None
    assert mp.name_cn == "因式分解法"
    assert mp.status == "pending"
    assert mp.seen_count == 1

    # KP paths walked: the two requested leaves both exist.
    kps = (await session.execute(
        select(models.KnowledgePoint).where(
            models.KnowledgePoint.subject == "math",
            models.KnowledgePoint.grade_band == "senior",
        )
    )).scalars().all()
    paths = {k.path_cached for k in kps}
    assert "代数>方程>一元二次方程" in paths
    assert "代数>因式分解" in paths
    # Parent nodes were walked in too.
    assert "代数" in paths
    assert "代数>方程" in paths

    # Link rows exist with weights.
    kp_links = (await session.execute(
        select(models.QuestionKPLink).where(
            models.QuestionKPLink.question_id == qid
        )
    )).scalars().all()
    assert len(kp_links) == 2

    profile = (await session.execute(
        select(models.QuestionRetrievalProfile).where(
            models.QuestionRetrievalProfile.question_id == qid
        )
    )).scalar_one_or_none()
    assert profile is not None
    retrieval_units = (await session.execute(
        select(models.RetrievalUnitRow).where(models.RetrievalUnitRow.question_id == qid)
    )).scalars().all()
    assert len(retrieval_units) >= 5
    unit_kinds = {row.unit_kind for row in retrieval_units}
    assert {"question_focus", "answer_focus", "method", "keyword_profile"} <= unit_kinds

    assert str(qid) in vs._rows["q_emb"]
    assert str(qid) in vs._rows["question_full_emb"]
    assert str(qid) in vs._rows["answer_full_emb"]
    for row in retrieval_units:
        assert str(row.id) in vs._rows["retrieval_unit_emb"]


@pytest.mark.asyncio
async def test_sediment_idempotent(session, tmp_image_dir):
    qid = await _seed_question(session, _PARSED_A, marker=b"sed-idem")
    solver_llm = GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_solver: json.dumps(_ANSWER_PACKAGE_A)}
    ))
    pkg = await _run_solver(session, qid, solver_llm)

    vs = InMemoryVectorStore()
    embed_llm = GeminiClient(_CannedEmbedTransport(vectors={
        _PARSED_A["question_text"]: _pad([1.0, 0.0]),
    }))
    es = EmbeddingService(llm=embed_llm)

    await sediment(session, question_id=qid, package=pkg, embedding=es, vector_store=vs)
    await sediment(session, question_id=qid, package=pkg, embedding=es, vector_store=vs)

    # Still exactly 2 kp links.
    kp_links = (await session.execute(
        select(models.QuestionKPLink).where(
            models.QuestionKPLink.question_id == qid
        )
    )).scalars().all()
    assert len(kp_links) == 2

    # Still exactly 1 pattern link.
    p_links = (await session.execute(
        select(models.QuestionPatternLink).where(
            models.QuestionPatternLink.question_id == qid
        )
    )).scalars().all()
    assert len(p_links) == 1

    profiles = (await session.execute(
        select(models.QuestionRetrievalProfile).where(
            models.QuestionRetrievalProfile.question_id == qid
        )
    )).scalars().all()
    assert len(profiles) == 1
    retrieval_units = (await session.execute(
        select(models.RetrievalUnitRow).where(models.RetrievalUnitRow.question_id == qid)
    )).scalars().all()
    assert retrieval_units


@pytest.mark.asyncio
async def test_sediment_detects_near_duplicate(session, tmp_image_dir):
    # Two questions with *almost identical* embeddings.
    qid_a = await _seed_question(session, _PARSED_A, marker=b"sed-dup-a")
    qid_b = await _seed_question(session, _PARSED_B_NEAR, marker=b"sed-dup-b")

    solver_llm = GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_solver: json.dumps(_ANSWER_PACKAGE_A)}
    ))
    pkg_a = await _run_solver(session, qid_a, solver_llm)
    pkg_b = await _run_solver(session, qid_b, solver_llm)

    # Very similar unit vectors: cosine ≈ 0.999 > threshold.
    vec_a = _pad([1.0, 0.01])
    vec_b = _pad([1.0, 0.02])
    vs = InMemoryVectorStore()
    embed_llm = GeminiClient(_CannedEmbedTransport(vectors={
        _PARSED_A["question_text"]: vec_a,
        _PARSED_B_NEAR["question_text"]: vec_b,
    }))
    es = EmbeddingService(llm=embed_llm)

    res_a = await sediment(
        session, question_id=qid_a, package=pkg_a, embedding=es, vector_store=vs,
    )
    assert res_a.near_dup_of is None

    res_b = await sediment(
        session, question_id=qid_b, package=pkg_b, embedding=es, vector_store=vs,
    )
    assert res_b.near_dup_of is not None
    assert res_b.near_dup_of == qid_a
    # Cosine must have actually been above threshold for this scenario.
    cos = sum(x * y for x, y in zip(vec_a, vec_b, strict=False))
    assert cos >= NEAR_DUP_THRESHOLD


@pytest.mark.asyncio
async def test_retrieval_reranks_by_pattern_match(session, tmp_image_dir):
    # Seed two separate questions; sediment both.
    qid_a = await _seed_question(session, _PARSED_A, marker=b"ret-a")
    qid_b = await _seed_question(session, _PARSED_B, marker=b"ret-b")

    solver_llm = GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_solver: json.dumps(_ANSWER_PACKAGE_A)}
    ))
    pkg_a = await _run_solver(session, qid_a, solver_llm)
    pkg_b = await _run_solver(session, qid_b, solver_llm)

    # A and B: clearly different embeddings — cosine low.
    vs = InMemoryVectorStore()
    vectors = {
        _PARSED_A["question_text"]: _pad([1.0, 0.0]),
        _PARSED_B["question_text"]: _pad([0.3, 0.95]),
    }
    embed_llm = GeminiClient(_CannedEmbedTransport(vectors=vectors))
    es = EmbeddingService(llm=embed_llm)

    await sediment(session, question_id=qid_a, package=pkg_a,
                   embedding=es, vector_store=vs)
    await sediment(session, question_id=qid_b, package=pkg_b,
                   embedding=es, vector_store=vs)

    # Mode=auto from A should prefer B because they share the same pattern.
    hits = await similar_questions(
        session,
        query=SimilarQuery(mode="auto", question_id=str(qid_a), k=5),
        embedding=es, vector_store=vs,
    )
    assert any(h.question_id == str(qid_b) for h in hits)
    top = next(h for h in hits if h.question_id == str(qid_b))
    # Score should include pattern_match=1 boost.
    assert top.pattern_match == 1.0
    assert top.score >= 0.3  # at least the pattern boost contribution


@pytest.mark.asyncio
async def test_retrieval_filters_by_subject(session, tmp_image_dir):
    qid_a = await _seed_question(session, _PARSED_A, marker=b"ret-filter-a")
    qid_b = await _seed_question(
        session,
        {**_PARSED_A, "subject": "physics", "question_text": "质量 m 的小球..."},
        marker=b"ret-filter-b",
    )

    solver_llm = GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_solver: json.dumps(_ANSWER_PACKAGE_A)}
    ))
    pkg_a = await _run_solver(session, qid_a, solver_llm)
    pkg_b = await _run_solver(session, qid_b, solver_llm)

    vs = InMemoryVectorStore()
    embed_llm = GeminiClient(_CannedEmbedTransport(vectors={
        _PARSED_A["question_text"]: _pad([1.0, 0.0]),
        "质量 m 的小球...": _pad([0.98, 0.2]),
    }))
    es = EmbeddingService(llm=embed_llm)
    await sediment(session, question_id=qid_a, package=pkg_a,
                   embedding=es, vector_store=vs)
    await sediment(session, question_id=qid_b, package=pkg_b,
                   embedding=es, vector_store=vs)

    hits = await similar_questions(
        session,
        query=SimilarQuery(mode="text", query="x^2", subject="math", k=5),
        embedding=es, vector_store=vs,
    )
    ids = {h.question_id for h in hits}
    assert str(qid_a) in ids or str(qid_b) not in ids
    assert str(qid_b) not in ids  # physics filtered out


@pytest.mark.asyncio
async def test_retrieval_returns_solution_id_for_solution_scoped_index(session, tmp_image_dir):
    qid_a = await _seed_question(session, _PARSED_A, marker=b"ret-sol-a")
    qid_b = await _seed_question(session, _PARSED_B, marker=b"ret-sol-b")

    solver_llm = GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_solver: json.dumps(_ANSWER_PACKAGE_A)}
    ))
    pkg_a = await _run_solver(session, qid_a, solver_llm)
    pkg_b = await _run_solver(session, qid_b, solver_llm)

    sol_a = await create_solution(session, question_id=qid_a, make_current=True)
    sol_b = await create_solution(session, question_id=qid_b, make_current=True)

    vs = InMemoryVectorStore()
    embed_llm = GeminiClient(_CannedEmbedTransport(vectors={
        _PARSED_A["question_text"]: _pad([1.0, 0.0]),
        _PARSED_B["question_text"]: _pad([0.8, 0.2]),
    }))
    es = EmbeddingService(llm=embed_llm)
    await sediment(
        session,
        question_id=qid_a,
        solution_id=sol_a.id,
        package=pkg_a,
        embedding=es,
        vector_store=vs,
    )
    await sediment(
        session,
        question_id=qid_b,
        solution_id=sol_b.id,
        package=pkg_b,
        embedding=es,
        vector_store=vs,
    )

    hits = await similar_questions(
        session,
        query=SimilarQuery(
            mode="auto",
            question_id=str(qid_a),
            solution_id=str(sol_a.id),
            k=5,
        ),
        embedding=es,
        vector_store=vs,
    )
    target = next(h for h in hits if h.question_id == str(qid_b))
    assert target.solution_id == str(sol_b.id)
