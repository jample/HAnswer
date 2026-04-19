# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HAnswer is a local-first learning companion for Chinese middle/high-school math and physics students. It takes a photo of a problem, uses Google Gemini to parse it into structured data, generates teaching-oriented answers with interactive JSXGraph visualizations, and sediments every solved question into a personal knowledge base with semantic retrieval.

## Common Commands

### Infrastructure
```bash
docker compose up -d                    # Milvus + etcd + MinIO + Attu
docker compose ps                       # Check service health
```

### Backend (Python 3.11+, FastAPI)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .                        # Install dependencies
pip install -e '.[dev]'                 # Install with dev tools (pytest, ruff, mypy)
pip install -e '.[retrieval]'           # Install bge-m3 local embeddings (optional)
cp config.example.toml config.toml      # First-time config
alembic upgrade head                    # Run DB migrations
python -m scripts.seed_knowledge        # Seed ~60 curriculum knowledge points
python -m scripts.rebuild_retrieval_index  # Rebuild Milvus from PostgreSQL
uvicorn app.main:app --reload --port 8787  # Run API server
```

### Backend Testing & Linting
```bash
cd backend
pytest                                  # Run all tests (requires local PostgreSQL)
pytest tests/test_prompts.py            # Run specific test file
ruff check .                            # Lint
python -m app.prompts.cli list          # List registered prompt templates
python -m app.prompts.cli explain solver  # Explain prompt design decisions
```

### Frontend (Next.js 16, React 19, TypeScript)
```bash
cd frontend
npm install
npm run dev                             # Dev server on :3333
npm run build                           # Production build
npm run lint                            # ESLint
npm run typecheck                       # TypeScript type check
```

### Viz AST Validator (Node.js)
```bash
cd backend/viz_validator && npm install  # Installs acorn for AST validation
```

## Architecture

### Stack
- **Backend**: FastAPI + async SQLAlchemy + asyncpg + Alembic + Pydantic v2
- **Frontend**: Next.js 16 App Router + React 19 + TypeScript
- **Database**: PostgreSQL (all structured data + cost ledger)
- **Vector DB**: Milvus 2.6 (12 collections: 6 dense HNSW + 6 sparse)
- **LLM**: Google Gemini (sole provider) for parsing, solving, visualization code, dialog, and embeddings
- **Infra**: Docker Compose runs Milvus stack (etcd + MinIO + milvus-standalone + Attu GUI on :1212)

### Backend Structure (`backend/app/`)
- **`routers/`** — 7 API routers (ingest, answer, dialog, retrieve, practice, knowledge, admin). All routes are in `backend/app/routers/`.
- **`services/`** — Business logic layer, fully decoupled from routes. Key services:
  - `llm_client.py` + `gemini_transport.py` — Gemini gateway with retry, repair loop, structured output validation, cost tracking, true incremental streaming (`call_structured_streaming`)
  - `streaming_json.py` — `TopLevelStreamParser`: incremental JSON parser that yields each top-level field the moment it completes
  - `answer_job_service.py` — Background 4-stage pipeline (parsed → solving → visualizing → indexing) with stage review/confirm/rerun workflow. Persists sections incrementally so `/resume` polls see progress during streaming.
  - `solver_service.py` — Solver orchestration with true incremental SSE: streams each `AnswerPackage` field as it completes via `TopLevelStreamParser`, falls back to bulk+repair on validation failure. Also `vizcoder_service.py`, `ingest_service.py` — other per-LLM-call orchestration
  - `sediment_service.py` — Pattern/KP resolution, near-dup detection (τ=0.96), Milvus upsert
  - `retrieval_service.py` — Multi-route hybrid retrieval (dense + sparse + structural) with RRF fusion
  - `embedding.py` + `sparse_encoder.py` — Dense (Gemini or bge-m3) and sparse (BM25 or bge-m3) encoders
  - `vector_store.py` — Milvus + InMemory abstraction
  - `dialog_service.py` — Persistent multi-turn dialog with rolling memory snapshots
- **`prompts/`** — PromptTemplate framework: every LLM call goes through a versioned `PromptTemplate` subclass with documented `DesignDecision`s. 5 registered prompts: `parser`, `solver`, `vizcoder`, `variant_synth`, `dialog`. Few-shot examples in `prompts/fewshot/<subject>/<grade_band>/*.json`.
- **`schemas/llm.py`** — Pydantic models for all LLM I/O contracts (ParsedQuestion, AnswerPackage, Visualization, etc.)
- **`db/models.py`** — 20 SQLAlchemy ORM models
- **`db/repo.py`** — Repository layer (CRUD functions)
- **`db/session.py`** — Async engine + session factory
- **`config.py`** — Pydantic settings loader from `config.toml`, API key from `$GEMINI_API_KEY` env var only
- **`viz_validator/`** — Node.js subprocess using acorn for AST validation of LLM-generated visualization code

### Frontend Structure (`frontend/`)
- **App Router pages**: `/` (Ask — camera capture + recent uploads strip), `/q/[id]` (Answer view), `/library` (with date range filters), `/knowledge` (with pattern detail panel + pitfalls), `/practice` (with search-add to basket), `/dialog`, `/settings`
- **Components**: `MathText.tsx` (MathJax rendering), `VizSandbox.tsx` (sandboxed iframe for JSXGraph)
- **`public/viz/`** — CSP-locked sandbox runtime (`sandbox.html`), H helper library (`h-library.js`), JSXGraph vendor files
- API proxy: `next.config.js` proxies `/api/*` to backend on :8787

### Answer Pipeline (4 stages)
1. **parsed** — Image → Gemini Parser → ParsedQuestion (user can edit)
2. **solving** — ParsedQuestion → Gemini Solver → AnswerPackage (teaching-first: method pattern before numeric answer)
3. **visualizing** — AnswerPackage → Gemini VizCoder → JSXGraph visualizations (AST-validated, rendered in sandboxed iframe)
4. **indexing** — Sediment: resolve patterns/KPs, build retrieval profile, batch embed, near-dup check, upsert Milvus

Each stage has a review/confirm/rerun workflow. The frontend polls `GET /api/answer/{id}/resume` for progress. The solving stage uses true incremental streaming: each `AnswerPackage` field appears as an SSE event within ~1.5s of generation, and `answer_job_service` persists each section in its own transaction so polls see progressive results.

### Retrieval Strategy
Multi-route hybrid: dense (4 embedding surfaces) + sparse (BM25/bge-m3) + structural (shared pattern + KP overlap) → RRF fusion → PG hydrate. Configurable via `[retrieval]` in config.toml.

### Database Schema (20 tables)
Core: `questions`, `question_solutions`, `answer_packages`, `question_stage_reviews`. Taxonomy: `knowledge_points`, `method_patterns`, `pitfalls`, `question_kp_link`, `question_pattern_link`. Retrieval: `question_retrieval_profiles`, `retrieval_units`, `solution_steps`. Visualization: `visualizations`. Exam: `exams`, `exam_items`. Dialog: `conversation_sessions`, `conversation_messages`, `conversation_memory_snapshots`. Tracking: `llm_calls`, `ingest_images`.

## Key Conventions

- Python: snake_case, 4-space indent, 100-char line length (Ruff), target 3.11+
- Frontend: PascalCase components, lowercase route folders per Next.js conventions
- All backend config in `config.toml` (git-ignored), API key only via `$GEMINI_API_KEY`
- Settings page is read-only; editing requires file changes + uvicorn restart
- Tests run against real local PostgreSQL with SAVEPOINT rollback (not mocks)
- Viz validator requires Node.js + acorn at runtime; tests auto-skip if `node` is absent
- No ad-hoc prompt strings in application code — all LLM prompts go through the PromptTemplate framework

## Configuration

Backend config (`config.toml`) sections: `[gemini]`, `[postgres]`, `[milvus]`, `[server]`, `[storage]`, `[llm]`, `[retrieval]`, `[dialog]`. Override path via `$HANSWER_CONFIG` env var. Embedding model can be Gemini (`gemini-embedding-2-preview`, default 768-dim) or local bge-m3 (1024-dim); switch requires Milvus dense collection rebuild.

## Known Issues (from P2S.md)

See `P2S.md` for the full problem tracker. Key open items:
- B2: In-memory job state lost on process restart — questions can get stuck in intermediate status
- B5: `list_questions` fetches max 500 rows then filters in Python — no pagination
- B7: SSE stream holds a single uncommitted transaction for the entire solve duration
- F5/F6: No loading/error boundaries in routes; Library has no pagination or virtualization

## Milestone Status

| Milestone | Scope | Status |
|---|---|---|
| M1 | Prompt framework, Gemini gateway, schema, Milvus, viz sandbox | Done |
| M2 | Ingest + Parser + Ask page | Done |
| M3 | Solver SSE + Answer view | Done |
| M4 | VizCoder + AST validator + iframe sandbox | Done |
| M5 | Hybrid retrieval + RRF + Library page | Done |
| M6 | Sediment + Knowledge tree + taxonomy seed | Done |
| M7 | Practice exams + variant synthesis | Done |
| M8 | Cost ledger + Admin API + Settings | Done |
| M9 | Multi-turn dialog + rolling memory | Done |

See `Unfinished.md` for remaining gaps.
