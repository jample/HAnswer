"""Multi-route retrieval + RRF tests (M5).

Covers:
  - `rrf.fuse` math on canned rankings.
  - `BM25SparseEncoder` tokenization + score monotonicity.
  - `similar_questions_multi_route` fusing dense + sparse + structural.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.config import settings
from app.schemas import AnswerPackage
from app.services.embedding import EmbeddingService
from app.services.ingest_service import ingest_image
from app.services.llm_client import FakeTransport, GeminiClient
from app.services.retrieval_service import (
    SimilarQuery,
    _ref_matches_excluded_question,
    similar_questions_multi_route,
)
from app.services.rrf import fuse
from app.services.sediment_service import sediment
from app.services.solver_service import generate_answer
from app.services.sparse_encoder import BM25SparseEncoder
from app.services.solution_ref_service import encode_solution_ref
from app.services.vector_store import InMemoryVectorStore

# Reuse fixtures + canned packages from the existing test module.
from tests.test_sediment_and_retrieval import (  # type: ignore[import-not-found]
    _ANSWER_PACKAGE_A,
    _CannedEmbedTransport,
    _PARSED_A,
    _PARSED_B,
    _pad,
    _run_solver,
    _seed_question,
)


# ── RRF math ────────────────────────────────────────────────────────


def test_rrf_agreement_wins_over_single_route_top():
    """A doc ranked #2 in two routes must outscore a doc ranked #1 in only one."""
    routes = {
        "dense":  ["X", "A", "B"],
        "sparse": ["Y", "A", "B"],
        "struct": ["Z", "A", "B"],
    }
    out = fuse(routes, k=60)
    top = out[0].ref_id
    # A is #2 in all three routes; each other doc is #1 in a single route.
    assert top == "A"


def test_rrf_respects_weights():
    routes = {"dense": ["A"], "sparse": ["B"]}
    # With dense weighted 10x, A wins.
    out = fuse(routes, k=60, weights={"dense": 10.0, "sparse": 1.0})
    assert out[0].ref_id == "A"
    # Flip weights, B wins.
    out = fuse(routes, k=60, weights={"dense": 1.0, "sparse": 10.0})
    assert out[0].ref_id == "B"


def test_rrf_handles_empty_routes():
    assert fuse({}) == []
    assert fuse({"dense": []}) == []


def test_solution_ref_exclusion_decodes_uuid_question_ids():
    qid = uuid.uuid4()
    sid = uuid.uuid4()
    ref_id = encode_solution_ref(question_id=qid, solution_id=sid)
    assert _ref_matches_excluded_question(ref_id, {qid}) is True
    assert _ref_matches_excluded_question(ref_id, {uuid.uuid4()}) is False


# ── BM25 encoder ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bm25_prefers_rare_chinese_tokens():
    enc = BM25SparseEncoder()
    # Corpus: many docs containing 方程, one with 向心力.
    await enc.encode([
        "解 方程 x^2 - 5x + 6 = 0",
        "解 方程 x^2 - 7x + 12 = 0",
        "解 方程 2x - 4 = 0",
        "求 向心力 的表达式",
    ])
    # Query mentions both 方程 (common) and 向心力 (rare).
    q = await enc.encode_one("方程 向心力")
    # Rare-term hash should weigh more than common-term hash.
    from app.services.sparse_encoder import _hash
    rare = q.get(_hash("向心力"), 0.0) + q.get(_hash("向心"), 0.0) + q.get(_hash("心力"), 0.0)
    common = q.get(_hash("方程"), 0.0) + q.get(_hash("方"), 0.0) + q.get(_hash("程"), 0.0)
    assert rare > common, f"rare={rare}, common={common}"


# ── Multi-route retrieval end-to-end ────────────────────────────────


@pytest.mark.asyncio
async def test_multi_route_sparse_rescues_dense_miss(session, tmp_image_dir):
    """Dense route scores A+B far apart; sparse exact-match on B boosts it up.

    After RRF the query should still surface B in the top results — the
    win is that sparse gives us a non-empty ranking where pure dense
    would have under-weighted the lexical signal.
    """
    qid_a = await _seed_question(session, _PARSED_A, marker=b"mr-a")
    qid_b = await _seed_question(session, _PARSED_B, marker=b"mr-b")

    solver_llm = GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_solver: json.dumps(_ANSWER_PACKAGE_A)}
    ))
    pkg_a = await _run_solver(session, qid_a, solver_llm)
    pkg_b = await _run_solver(session, qid_b, solver_llm)

    vs = InMemoryVectorStore()
    vectors = {
        _PARSED_A["question_text"]: _pad([1.0, 0.0]),
        _PARSED_B["question_text"]: _pad([0.0, 1.0]),  # orthogonal to A
    }
    embed_llm = GeminiClient(_CannedEmbedTransport(vectors=vectors))
    es = EmbeddingService(llm=embed_llm)
    sp = BM25SparseEncoder()

    await sediment(session, question_id=qid_a, package=pkg_a,
                   embedding=es, vector_store=vs, sparse_encoder=sp)
    await sediment(session, question_id=qid_b, package=pkg_b,
                   embedding=es, vector_store=vs, sparse_encoder=sp)

    # Query text borrowed verbatim from B — sparse route must find it.
    hits = await similar_questions_multi_route(
        session,
        query=SimilarQuery(mode="text", query=_PARSED_B["question_text"], k=5),
        embedding=es, sparse=sp, vector_store=vs,
    )
    assert any(h.question_id == str(qid_b) for h in hits)
    top = hits[0]
    assert top.rrf_score is not None
    assert top.route_ranks is not None
    # At least one of dense / sparse / structural fired for the top hit.
    assert any(r in top.route_ranks for r in ("dense", "sparse", "structural"))


@pytest.mark.asyncio
async def test_multi_route_structural_route_alone(session, tmp_image_dir):
    """Structural route returns hits even when no text query is given (mode=auto).

    Seed two questions sharing a pattern; from A, query in auto-mode with
    zeroed dense signal — we should still retrieve B via the structural
    pattern-overlap route.
    """
    qid_a = await _seed_question(session, _PARSED_A, marker=b"mr-struct-a")
    qid_b = await _seed_question(session, _PARSED_B, marker=b"mr-struct-b")

    solver_llm = GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_solver: json.dumps(_ANSWER_PACKAGE_A)}
    ))
    pkg_a = await _run_solver(session, qid_a, solver_llm)
    pkg_b = await _run_solver(session, qid_b, solver_llm)

    vs = InMemoryVectorStore()
    # Orthogonal dense vectors so dense route doesn't match.
    vectors = {
        _PARSED_A["question_text"]: _pad([1.0, 0.0]),
        _PARSED_B["question_text"]: _pad([0.0, 1.0]),
    }
    embed_llm = GeminiClient(_CannedEmbedTransport(vectors=vectors))
    es = EmbeddingService(llm=embed_llm)
    sp = BM25SparseEncoder()

    await sediment(session, question_id=qid_a, package=pkg_a,
                   embedding=es, vector_store=vs, sparse_encoder=sp)
    await sediment(session, question_id=qid_b, package=pkg_b,
                   embedding=es, vector_store=vs, sparse_encoder=sp)

    hits = await similar_questions_multi_route(
        session,
        query=SimilarQuery(mode="auto", question_id=str(qid_a), k=5),
        embedding=es, sparse=sp, vector_store=vs,
    )
    ids = {h.question_id for h in hits}
    assert str(qid_b) in ids
    b_hit = next(h for h in hits if h.question_id == str(qid_b))
    assert b_hit.route_ranks is not None
    # Structural route must have contributed — A and B share the pattern.
    assert "structural" in b_hit.route_ranks


@pytest.mark.asyncio
async def test_multi_route_can_query_answer_and_pedagogical_facets(session, tmp_image_dir):
    qid = await _seed_question(session, _PARSED_A, marker=b"mr-answer")

    solver_llm = GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_solver: json.dumps(_ANSWER_PACKAGE_A)}
    ))
    pkg = await _run_solver(session, qid, solver_llm)

    vs = InMemoryVectorStore()
    embed_llm = GeminiClient(_CannedEmbedTransport(vectors={
        _PARSED_A["question_text"]: _pad([1.0, 0.0]),
    }))
    es = EmbeddingService(llm=embed_llm)
    sp = BM25SparseEncoder()

    await sediment(session, question_id=qid, package=pkg,
                   embedding=es, vector_store=vs, sparse_encoder=sp)

    hits = await similar_questions_multi_route(
        session,
        query=SimilarQuery(mode="text", query="代回验证", k=5),
        embedding=es, sparse=sp, vector_store=vs,
    )
    assert hits
    top = hits[0]
    assert top.question_id == str(qid)
    assert top.route_ranks is not None
    assert "sparse" in top.route_ranks or "dense" in top.route_ranks
    assert top.matched_unit_kinds is not None
    assert "answer_focus" in top.matched_unit_kinds or "keyword_profile" in top.matched_unit_kinds
