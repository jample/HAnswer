# HAnswer · 学习伙伴

> Local-first learning companion for Chinese middle/high-school math & physics.
> Turns a photo of a problem into a **teaching-first** structured answer with
> interactive visualizations, and sediments every solved question into a
> reusable knowledge base.

See [HAnswerR.md](HAnswerR.md) for the full stage-1 specification.

See [Unfinished.md](Unfinished.md) for the current requirement-audit log,
the remaining Stage-1 gaps, and the README updates recorded from that audit.

---

## Audit Status

Last audited against `HAnswerR.md`: `2026-04-18`.

- Fixed in this audit: solver few-shot example loading, persisted answer
  replay on `/q/[id]`, viz helper board wiring plus runtime budgets, Knowledge
  merge UI, Library route-rank visibility for multi-route retrieval, and a
  persistent multi-turn dialog system with rolling memory.
- Still not fully complete: true incremental section streaming, several
  Stage-1 UX surfaces, and some spec-listed filters/admin affordances. Those
  are tracked in [Unfinished.md](Unfinished.md).

---

## What the app does

| You do | HAnswer does |
|---|---|
| Drop a photo of a math/physics problem on `/` | Gemini multimodal → structured `ParsedQuestion` you can edit before answering |
| Hit **开始解答** | Starts a background 4-call Gemini pipeline. The UI shows `1/4 解析题面 → 2/4 生成解答 → 3/4 生成可视化 → 4/4 建立索引`, and `/q/[id]` polls persisted progress/sections so long solves survive refreshes while results continue to appear. The repeated `/resume` calls are frontend polling only; they do not mean extra Gemini solver calls |
| Open `/dialog` or click **进入多轮追问对话** on `/q/[id]` | Starts a persistent study conversation. Each turn sends `题目上下文 + 滚动摘要 + 关键事实 + 最近消息` so Gemini keeps continuity without replaying the whole transcript; transcripts and memory snapshots are stored in PostgreSQL for later analysis |
| Watch the right panel | Sandboxed JSXGraph visualizations (AST-validated) with sliders/toggles |
| Browse `/library` | Filter bank; semantic search with RRF fusion over **whole-question + whole-answer + pedagogical-facet** dense/sparse routes plus structural links, including per-route rank traces |
| Click a node on `/knowledge` | See related questions (weighted), co-occurring method patterns, and promote / merge / reject pending KPs or patterns |
| Build a set on `/practice` | Add questions to a basket, configure count + difficulty distribution, get an exam (LLM synthesizes pattern-preserving variants when the bank is short), self-check with ✓/✗/? |
| Open `/settings` | See masked Gemini key, models per task, retrieval/dialog memory knobs, LLM cost ledger (totals / by prompt·version / by day), and dialog analytics counts |

### Key design principles

- **Teacher-first, solver-second.** Every `AnswerPackage` puts the
  reusable `method_pattern` (方法模式) ahead of the numeric answer.
- **Prompt Template framework** (§7.1): every LLM call goes through a
  versioned `PromptTemplate` subclass with documented `DesignDecision`s.
  No ad-hoc prompt strings in application code.
- **Safety by construction.** LLM-emitted visualization code is:
  AST-validated against an allow-list, then executed in a CSP-locked,
  cross-origin `<iframe sandbox>` communicating via typed postMessage.
- **Knowledge sediment.** Every solved question writes a dedup'd row,
  resolves/creates `KnowledgePoint` + `MethodPattern` rows, stores a
  `question_retrieval_profile` plus `retrieval_units`, upserts whole-question,
  whole-answer, and pedagogical-facet embeddings into Milvus (dense + sparse),
  and promotes pending nodes only on explicit user review.
- **Conversation memory, not transcript replay.** Multi-turn dialog stores full
  messages, but Gemini receives a compressed context package: optional
  question anchor, rolling summary, cached key facts, open questions, and the
  latest raw turns. Each assistant turn refreshes that memory in one call.
- **Local-first.** Everything runs on your machine: Postgres, Milvus,
  Next.js, FastAPI. The only outbound network call is to Gemini.

---

## Milestone status

| M | Scope | Status |
|---|---|---|
| M1 | Prompt framework · Gemini gateway · PG schema · Milvus collections · viz sandbox scaffold | ✅ |
| M2 | `POST /api/ingest/image` · Parser prompt · `/` Ask page with editable parse | ✅ |
| M3 | `POST /api/answer/{id}` SSE · Solver prompt · `/q/[id]` KaTeX incremental render | ✅ |
| M4 | VizCoder prompt · Node/acorn AST validator · iframe sandbox · `<VizSandbox/>` host | ✅ |
| M5 | Pluggable embedder (Gemini / **bge-m3**) · BM25 or bge-m3 sparse · Milvus dense+sparse · **multi-route + RRF** · `/library` | ◐ |
| M6 | Sediment (resolve/create patterns + KPs) · near-dup (τ=0.96) · `/api/knowledge/*` · taxonomy seed · `/knowledge` | ◐ |
| M7 | `ExamConfig` filter · `VariantSynthPrompt` · `/api/practice/exam` · `/practice` basket + runner | ✅ |
| M8 | PG `CostLedger` · `/api/admin/{llm-cost,config,prompts}` · `/api/answer/{id}/resume` · `/settings` | ◐ |
| M9 | Persistent multi-turn dialog · rolling memory snapshots · `/api/dialog/*` · `/dialog` | ✅ |

**Verification status:** prompt-framework tests pass locally (`22 passed`
without requiring PostgreSQL). DB-backed backend tests still require a real
local PostgreSQL instance with sandbox access; run `cd backend && pytest`
in your local environment to verify the full suite end-to-end.

---

## Prerequisites

- **Python** 3.11+
- **Node.js** 18+ (required at runtime by the viz AST validator)
- **PostgreSQL** running locally (any 13+)
- **Milvus** standalone 2.4+ (the bundled `docker-compose.yml` ships 2.6.14)
- **Google Gemini** API key (<https://aistudio.google.com/app/apikey>)

---

## Configuration

All backend configuration lives in `backend/config.toml` (git-ignored).
Copy the example, then export your LLM and embedding API keys as environment variables:

```bash
cd backend
cp config.example.toml config.toml
export GEMINI_API_KEY="your_key_here"   # never put the key in config.toml
export OAI_API_KEY="your_openai_key"    # used when [llm].provider = "openai"
export EMB_URL="https://your-embedding-gateway/v1"
export EMB_API_KEY="your_embedding_key"
```

```toml
[gemini]
# API key is read from $GEMINI_API_KEY — do not put it in this file.
model_parser   = "gemini-3.1-pro-preview" # image → ParsedQuestion
model_solver   = "gemini-3.1-pro-preview" # ParsedQuestion → AnswerPackage
model_vizcoder = "gemini-3.1-pro-preview" # AnswerPackage → visualizations[]

[embedding]
# Dedicated dense embedding service for indexing/retrieval.
# Endpoint/key come from EMB_URL and EMB_API_KEY. Do not put them here.
# For Azure OpenAI-compatible v1, set EMB_URL to:
# https://<resource>.openai.azure.com/openai/v1/
# and use the deployment name as `model`.
provider   = "openai"                  # "openai" | "gemini" | "bge-m3"
model      = "text-embedding-3-large"
dimensions = 1536

[postgres]
dsn = "postgresql+asyncpg://jianbo@localhost:5432/jianbo"  # asyncpg driver required

[milvus]
host     = "localhost"
port     = 19530
database = "default"
auto_bootstrap = true                   # create collections on FastAPI startup
recreate_dense_on_dim_mismatch = true   # recreate stale dense collections when dim changes
auto_reindex_on_bootstrap_change = true # rebuild Milvus from PostgreSQL after bootstrap changes

[server]
host         = "127.0.0.1"
port         = 8787
cors_origins = ["http://localhost:3333"]

[storage]
image_dir = "./data/images"            # original uploads

[llm]
max_retries         = 2                 # transport-level retries
max_repair_attempts = 2                 # schema-repair loop (§3 reliability)
request_timeout_s   = 60                # generic fallback
parser_timeout_s    = 60
solver_timeout_s    = 180               # long teaching answers can exceed 60s
vizcoder_timeout_s  = 120
dialog_timeout_s    = 90
embed_timeout_s     = 60
stream_solver_json  = true              # Gemini structured-output streaming
stream_vizcoder_json = true

[retrieval]                             # §3.4
sparse_encoder     = "bm25"            # "bm25"   | "bge-m3"
multi_route        = true              # RRF over dense + sparse + structural
rrf_k              = 60
route_weights_dense       = 1.0
route_weights_sparse      = 1.0
route_weights_structural  = 1.0
bge_m3_model       = "BAAI/bge-m3"
bge_m3_device      = "cpu"             # "cpu" | "cuda" | "mps"
bge_m3_dense_dim   = 1024
wide_k_multiplier  = 3                 # per-route top-K = max(30, k · m)

[dialog]                                # multi-turn tutoring memory
model_chat                 = "gemini-3.1-pro-preview"
recent_messages            = 10         # raw recent messages kept per turn
max_question_context_chars = 12000      # anchor question/answer context budget
max_summary_chars          = 3000       # rolling summary budget
max_key_facts              = 12
max_open_questions         = 8
```

You can always inspect the active config at <http://localhost:3333/settings>
(key is masked) or `GET /api/admin/config`. Editing is **file-only and
requires a uvicorn restart** — HTTP editing of secrets is intentionally
not supported.

---

## First-time setup

### 1. PostgreSQL

```bash
createdb jianbo           # or edit [postgres].dsn for your setup
cd backend
alembic upgrade head
```

The test suite uses the same DSN and rolls back each test inside a
SAVEPOINT, so nothing is persisted.

### 2. Milvus

```bash
docker compose up -d      # etcd + minio + milvus-standalone + attu UI
```

The compose file uses `depends_on: condition: service_healthy`, so
Milvus waits for etcd + MinIO and Attu waits for Milvus. First boot
takes ~90s.

Collections auto-bootstrap on the first FastAPI startup (twelve total:
six dense HNSW + six sparse `SPARSE_INVERTED_INDEX`). Manually:

```bash
cd backend
python -m app.services.milvus_setup            # idempotent create
python -m app.services.milvus_setup --doctor   # list + row counts
```

### 3. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
alembic upgrade head                     # rerun after pulling schema changes
python -m scripts.seed_knowledge         # ~60 curriculum KnowledgePoints
uvicorn app.main:app --reload --port 8787
```

### 4. Viz AST validator

```bash
cd backend/viz_validator
npm install                               # installs acorn
```

The Python wrapper (`app/services/viz_validator.py`) spawns
`node validate.mjs` once per visualization.

Curated solver few-shot examples live under
`backend/app/prompts/fewshot/<subject>/<grade_band>/*.json` and are selected
automatically by `ParsedQuestion.topic_path`.

Each answered question also gets a deterministic pedagogical retrieval index:
whole question text, whole rendered answer text, and semantic units such as
`question_focus`, `answer_focus`, `method`, `step`, `extension`, and
`keyword_profile`.

### 5. Frontend

```bash
cd frontend
npm install

# JSXGraph vendor files (loaded inside the sandboxed iframe)
curl -o public/viz/jsxgraphcore.js https://jsxgraph.org/distrib/jsxgraphcore.js
curl -o public/viz/jsxgraph.css    https://jsxgraph.org/distrib/jsxgraph.css

npm run dev                                # listens on :3333
```

Open <http://localhost:3333>. `next.config.js` proxies `/api/*` to the
backend on port 8787 and attaches a strict CSP to `/viz/sandbox.html`
(§3.3.2).

---

## Daily startup (after first-time setup)

```bash
# terminal 1
docker compose up -d

# terminal 2
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8787

# terminal 3
cd frontend && npm run dev
```

---

## Developer command reference

Use these when working on the repo beyond the basic startup flow above.

### Infrastructure

```bash
docker compose up -d
docker compose ps
```

### Backend development

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
pip install -e '.[dev]'
pip install -e '.[retrieval]'
cp config.example.toml config.toml
alembic upgrade head
python -m scripts.seed_knowledge
python -m scripts.rebuild_retrieval_index
uvicorn app.main:app --reload --port 8787
```

### Backend validation

```bash
cd backend
pytest
pytest tests/test_prompts.py
ruff check .
python -m app.prompts.cli list
python -m app.prompts.cli explain solver
```

### Frontend validation

```bash
cd frontend
npm install
npm run dev
npm run build
npm run lint
npm run typecheck
```

### Visualization validator runtime

```bash
cd backend/viz_validator
npm install
```

---

## Pages

| Path | Purpose |
|---|---|
| `/` (Ask) | Upload image, edit parsed fields, and see Gemini `1/4 解析题面` complete before starting answer generation. |
| `/q/[id]` | **Three-column layout**: left TOC rail with section completion markers · center sections · right sticky viz panel with tabs per visualization, sliders/toggles. The page starts the remaining Gemini calls in the background (`2/4 生成解答`, `3/4 生成可视化`, `4/4 建立索引`) and polls `/api/answer/{id}/resume` for persisted `status` + section rows so long solves show clear stage progress and survive refreshes. Polling `/resume` is a UI progress read, not another solver invocation. Timeout failures now render a stage-specific error card with a retry action. |
| `/dialog` | Persistent multi-turn tutoring chat. Sessions can be free-form or anchored to a solved question; each turn refreshes a rolling summary, key facts, and open questions, while full messages remain stored for analysis. |
| `/library` | Filter by subject/grade/difficulty. Semantic search runs through RRF over legacy question text, whole question, whole answer, pedagogical retrieval units, and structural pattern/KP overlap; hits show per-route rank and matched unit kinds when available. Falls back to `0.5·cos + 0.3·pattern + 0.2·kp` when `multi_route=false`. |
| `/knowledge` | Tree view (live/pending colors) with a right **detail panel** showing related questions (by weight) + co-occurring method patterns. **Pending** tab now supports promote / merge / reject for both KPs and patterns. **Prompts** tab shows the `PromptRegistry`. |
| `/practice` | 练习篮 (localStorage) · config form (count, `难度:个数` distribution, synthesis toggle) · exam runner with 答案大纲 reveal and ✓/✗/? self-check summary. |
| `/settings` | Current config cards (masked API key, models per task, retrieval knobs, dialog-memory budgets, LLM retry budgets), cost ledger dashboard (totals / per prompt·version / per day, window 1–90 days), dialog analytics counts, Prompt registry. |

---

## API surface

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/ingest/image` | Upload + Parser; returns draft `question_id` + `ParsedQuestion` |
| PATCH | `/api/ingest/{id}` | Persist edits to parsed fields |
| POST | `/api/answer/{id}/start` | Start or reattach to the background answer job |
| POST | `/api/answer/{id}` | **SSE** stream of `AnswerPackage` sections + visualizations + sediment |
| GET | `/api/answer/{id}/resume` | Stored status + sections + visualizations — replay/poll view progress after refresh |
| GET | `/api/questions/{id}` | Full persisted `AnswerPackage` |
| GET | `/api/questions?subject=&grade_band=&difficulty_min=&difficulty_max=` | Library list |
| POST | `/api/retrieve/similar` | `{mode: auto\|text\|kp\|pattern, query, k, filters}` |
| POST | `/api/practice/exam` | Build exam (bank + LLM variants) |
| GET | `/api/practice/exam/{id}` | Hydrated exam with statements, outlines, rubrics |
| GET | `/api/knowledge/tree` | Taxonomy; filter `subject`, `grade_band`, `status` |
| GET | `/api/knowledge/pending` | Pending KPs + patterns |
| GET | `/api/knowledge/patterns` | List method patterns; filter `subject`, `grade_band`, `status` |
| GET | `/api/knowledge/kp/{id}/detail` | Related questions + co-occurring patterns |
| GET | `/api/knowledge/pattern/{id}/detail` | Related questions + pitfalls |
| POST | `/api/knowledge/promote` | `{kind: kp\|pattern, id}` → status=live |
| POST | `/api/knowledge/reject` | Cascade delete |
| POST | `/api/knowledge/merge` | Repoint links + transfer seen_count |
| GET | `/api/admin/llm-cost?days=N` | Totals / by (task, prompt_version) / by day |
| GET | `/api/admin/config` | Current config (key + DSN password masked) |
| GET | `/api/admin/prompts` | Registry summary |
| GET | `/api/admin/prompts/{name}/explain` | Design decisions + schema |
| POST | `/api/admin/prompts/{name}/preview` | Render prompt with `kwargs` — no LLM call |

---

## End-to-end flow

```
image → POST /api/ingest/image
         → Parser prompt → ParsedQuestion → PG (status="parsed")
/ (Ask) → user edits parsed → PATCH /api/ingest/{id}
/q/[id] → POST /api/answer/{id}/start  (background job)
         → poll GET /api/answer/{id}/resume
         → Solver prompt  → AnswerPackage     → PG (status="solving" → "visualizing" → "indexing" → "answered")
         → emit: question_understanding, key_points_of_question,
                 solution_step×N, key_points_of_answer, method_pattern,
                 similar_questions, knowledge_points, self_check
         → VizCoder prompt → visualizations[]
         → per-viz acorn AST validation → PG + `visualization` events
         → Sediment: resolve/create pattern + KP, build retrieval profile,
                     batch-embed full question/answer + semantic units,
                     near-dup (τ=0.96), upsert Milvus (dense+sparse)
         → `sediment` event (pattern_id, kp_ids, near_dup_of)
         → `done`

On refresh, /q/[id] calls GET /api/answer/{id}/resume to rehydrate.

/library  → GET /api/questions · POST /api/retrieve/similar
/knowledge → tree · pending · kp/{id}/detail · promote | reject | merge
/practice  → POST /api/practice/exam · GET /api/practice/exam/{id}
/dialog    → GET/POST /api/dialog/sessions · GET /api/dialog/stats · POST /api/dialog/sessions/{id}/messages
/settings  → GET /api/admin/{config,llm-cost,prompts}
```

---

## Architecture snapshot

### Stack

- Backend: FastAPI + async SQLAlchemy + asyncpg + Alembic + Pydantic v2
- Frontend: Next.js App Router + React + TypeScript
- Database: PostgreSQL for questions, solutions, reviews, dialog, and cost ledger
- Vector store: Milvus dense + sparse collections
- LLM provider: configurable OpenAI-compatible or Gemini transports for parser/solver/viz/dialog
- Embedding provider: dedicated `[embedding]` service, defaulting to OpenAI-compatible remote embeddings
- Infra: local Docker Compose for etcd + MinIO + Milvus + Attu

### Core backend modules

- `backend/app/routers/`: ingest, answer, dialog, retrieve, practice, knowledge, admin
- `backend/app/services/llm_client.py` + `gemini_transport.py`: transport, retries, repair loop, prompt logging, cost tracking
- `backend/app/services/streaming_json.py`: top-level JSON streaming parser for incremental solve delivery
- `backend/app/services/answer_job_service.py`: background 4-stage pipeline with confirm/rerun review flow
- `backend/app/services/solver_service.py`: solver orchestration and persisted incremental answer sections
- `backend/app/services/visualization_spec_service.py` + `jsxgraph_codegen_service.py`: HAVizNew two-stage visualization planning and codegen
- `backend/app/services/sediment_service.py`: pattern / knowledge-point resolution, dedup, retrieval-profile build, Milvus upsert
- `backend/app/services/retrieval_service.py`: dense + sparse + structural retrieval with RRF fusion
- `backend/app/services/dialog_service.py`: persistent multi-turn study chat with rolling memory snapshots
- `backend/app/prompts/`: versioned prompt templates and few-shot assets
- `backend/app/db/`: ORM models, async session, repository helpers

### Frontend structure

- `frontend/app/`: routes for Ask, Answer, Library, Knowledge, Practice, Dialog, Settings
- `frontend/components/MathText.tsx`: math rendering
- `frontend/components/VizSandbox.tsx`: visualization dispatcher and sandbox host
- `frontend/public/viz/`: sandbox runtime, helper library, JSXGraph assets
- `frontend/next.config.js`: `/api/*` proxy and sandbox CSP wiring

### Answer pipeline

1. `parsed`: image to `ParsedQuestion`
2. `solving`: `ParsedQuestion` to teaching-first `AnswerPackage`
3. `visualizing`: `AnswerPackage` to `VisualizationSpecBundle`, then selected spec to JSXGraph code
4. `indexing`: sediment into taxonomy + retrieval units + Milvus vectors

Each stage supports review, confirm, and rerun. The frontend uses `GET /api/answer/{id}/resume` to reconstruct progress and artifacts after refresh.

### Data model landmarks

- Core: `questions`, `question_solutions`, `answer_packages`, `question_stage_reviews`
- Taxonomy: `knowledge_points`, `method_patterns`, `pitfalls`, question-to-taxonomy link tables
- Retrieval: `question_retrieval_profiles`, `retrieval_units`, `solution_steps`
- Visualization: `visualizations`
- Practice: `exams`, `exam_items`
- Dialog: `conversation_sessions`, `conversation_messages`, `conversation_memory_snapshots`
- Tracking: `llm_calls`, `ingest_images`

---

## Retrieval strategy (M5, §3.4)

```
                 ┌── dense   (legacy q_emb + question_full + answer_full + retrieval_units)
query / anchor ──┼── sparse  (BM25 / bge-m3 lexical over the same four surfaces)
                 └── struct  (PG: shared pattern + KP overlap)
                          │
                          ▼
              RRF fuse (k = retrieval.rrf_k)
                          │
                          ▼
              PG hydrate + difficulty filters → top-K
```

Fast remote mode: OpenAI-compatible dense (`text-embedding-3-large`) + BM25
sparse + structural — no local model load on each query.

```toml
[embedding]
provider   = "openai"
model      = "text-embedding-3-large"
dimensions = 1536

[retrieval]
sparse_encoder = "bm25"
```

For stronger local mixed-language recall, flip to bge-m3:

```toml
[embedding]
provider = "bge-m3"

[retrieval]
sparse_encoder = "bge-m3"
bge_m3_device  = "cpu"         # or "cuda" / "mps"
bge_m3_dense_dim = 1024
```

```bash
cd backend && pip install -e ".[retrieval]"   # FlagEmbedding + compatible transformers<5 stack
```

For an existing deployment, a clean Gemini → bge-m3 migration is:

```bash
cd backend
pip install -e ".[retrieval]"
python -m app.services.milvus_setup --doctor
python -m scripts.rebuild_retrieval_index --recreate-dense --recreate-sparse
```

Truthfully, Milvus itself does not need a special server-side config
change for `bge-m3`. What matters is that:

- you are on Milvus 2.4+ so `SPARSE_INVERTED_INDEX` is supported
- the backend can reach `host:port`
- dense collections are recreated when the active embedder dim changes
  from Gemini 1536 to bge-m3 1024

In the current repo defaults, FastAPI startup will auto-bootstrap Milvus,
recreate stale dense collections on a dim mismatch, and then rebuild the
Milvus retrieval rows from PostgreSQL automatically. The manual
`scripts.rebuild_retrieval_index` flow is still useful for one-off repairs
or explicit operator control.

`python -m app.services.milvus_setup --doctor` now reports the active
dense dim and any collection mismatches. `scripts.rebuild_retrieval_index`
rebuilds `question_full_emb`, `answer_full_emb`, `retrieval_unit_emb`,
`pattern_emb`, and `kp_emb` from PostgreSQL so you do not have to
re-answer every old question manually.

If you switch sparse encoder family (`bge-m3` ↔ `bm25`), use
`--recreate-sparse` as well so Milvus does not keep stale sparse rows
from the previous encoder.

When `[embedding].provider="gemini"` and `model="gemini-embedding-2-preview"`,
the backend prepends task prefixes to the text (`task: search result | query: ...`
for queries, `title: none | text: ...` for documents), matching Google's
Embedding v2 guidance. Legacy Gemini models (`text-embedding-004`,
`gemini-embedding-001`) continue to use the `task_type` API parameter.

Set `multi_route = false` to fall back to the single-route formula
`0.5·cos + 0.3·pattern_match + 0.2·kp_overlap` (safe when sparse
collections aren't provisioned yet).

---

## Prompt CLI (§7.1)

```bash
python -m app.prompts.cli list
python -m app.prompts.cli explain solver
python -m app.prompts.cli preview parser --kwargs '{"subject_hint":"math"}'
python -m app.prompts.cli preview variant_synth \
    --kwargs '{"source": {"statement":"解 x^2-5x+6=0","pattern_name":"因式分解法","pattern_procedure":["观察","分解","令零"]}, "count": 3}'
```

Registered templates: `parser`, `solver`, `vizcoder`, `variant_synth`.

Current prompt modules in the repo also include dialog and visualization-stage templates such as `dialog`, `vizspec`, `jsxgraph_codegen`, `vizplanner`, and `vizitem`.

---

## Tests

Run against the **real local Postgres** (per Appendix B). Prereq:
`alembic upgrade head` once against the configured DSN. Node + acorn
are required by the viz validator tests — if `node` is absent those
tests are skipped automatically.

```bash
cd backend
pytest           # 86 tests
```

Tests run against a real local PostgreSQL instance with SAVEPOINT rollback rather than mocks. Visualization validator tests require Node.js; they auto-skip if `node` is unavailable.

Smoke test against a real image (calls live Gemini):

```bash
cd backend
python -m scripts.smoke_parse ../data/samples/q1.jpg --subject math
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `uvicorn` fails at import with `AssertionError` on `config.toml` | `backend/config.toml` missing | `cp config.example.toml config.toml` and set `export GEMINI_API_KEY=...` |
| `/api/ingest/image` returns `502 parser LLM failed` | Gemini key empty / invalid / rate-limited | Check `/api/admin/config` (`api_key_configured: false`?); set `export GEMINI_API_KEY=<your_key>` and restart uvicorn |
| `/api/answer/{id}` stream immediately emits `error` | Solver repair loop exhausted (bad schema) | Tail uvicorn logs for the pydantic `ValidationError`; consider bumping `[llm].max_repair_attempts` |
| `/api/answer/{id}` background job fails with `TransientLLMError(timeout...)` during `2/4 生成解答` | Solver answer is taking longer than the configured timeout, or Gemini stalled before sending chunks | Raise `[llm].solver_timeout_s`; keep `stream_solver_json=true` so long JSON answers use Gemini structured streaming instead of one large blocking wait |
| Sediment / indexing crashes with an embedding provider `400` / `404` | `EMB_URL`, `EMB_API_KEY`, `[embedding].provider`, model, or dimensions do not match the gateway/key | Set `EMB_URL`/`EMB_API_KEY` and `[embedding]` to a model supported by that endpoint/key, then rebuild Milvus if dimensions changed |
| Remote embeddings are configured but retrieval is still slow | `retrieval.sparse_encoder` is still set to `bge-m3`, so the local model still loads for sparse search | Set `[retrieval].sparse_encoder="bm25"`, then rebuild Milvus indexes |
| Index rebuild crashes with Milvus `[flush] rate limit exceeded[rate=0.1]` | Explicit flushes are too frequent for local Milvus standalone | Keep `[milvus].flush_on_write=false` and restart the backend; PostgreSQL remains the source of truth and vectors are queryable without flushing every write |
| `bge-m3` import fails inside `FlagEmbedding` with a `transformers` symbol error | `FlagEmbedding 1.x` was installed alongside an incompatible `transformers` 5.x build | Re-run `cd backend && python -m pip install -e '.[retrieval]'`; the repo now pins `transformers<5` for the retrieval extra |
| `pytest` fails with `connection refused: 5432` | Postgres not running or DSN wrong | `pg_isready -p 5432`; fix `[postgres].dsn` |
| Milvus collections missing / `get_collection_stats` errors | Milvus still starting (~90s first boot), or `auto_bootstrap=false` | `docker compose ps` — wait for `healthy`; run `python -m app.services.milvus_setup` |
| Milvus doctor reports a dense-dim mismatch after an embedder switch | Existing dense collections were created under a different embedding dimension and are now incompatible with the active embedder | Restart the backend to let auto-bootstrap recreate and reindex Milvus, or run `python -m scripts.rebuild_retrieval_index --recreate-dense --recreate-sparse` for a manual rebuild |
| `/library` hits return empty despite questions in PG | Milvus collections not populated or still contain old vectors from a previous embedder or sparse encoder | Run `python -m scripts.rebuild_retrieval_index`; if you changed embedder or sparse-encoder family, use `--recreate-dense --recreate-sparse` |
| Viz tile shows `可视化失败: Evaluating a string as JavaScript ... 'unsafe-eval'` | Old sandbox runtime path still loaded, or frontend dev/build server was not restarted after the sandbox runtime changed | Restart the frontend so `/viz/sandbox.html` serves the updated runtime; the current implementation no longer uses the `Function` constructor |
| Viz never renders but no error | `public/viz/jsxgraphcore.js` missing | Re-run the two `curl` commands in [first-time setup](#5-frontend) |
| Practice exam endpoint returns `400 no candidates match filters` | Question bank empty or over-filtered | Lower filters or answer more questions first; set `allow_synthesis=true` |
| `/settings` cost ledger is empty | No LLM calls yet, or `PgCostLedger` failed silently | Tail uvicorn logs for `cost ledger write failed`; check Postgres connectivity |
| `npm run dev` → EADDRINUSE 3333 | Another instance already running | `lsof -i :3333` and kill, or set `PORT=3334 npm run dev` and update CORS |
| Browser shows page but `/api/*` returns 404 | Backend not running or on the wrong port | Confirm uvicorn on 8787 and that `frontend/next.config.js` proxies match |

---

## Conventions and trackers

- Python uses snake_case, 4-space indentation, and the backend Ruff configuration.
- Frontend components use PascalCase; App Router folders stay lowercase.
- Keep backend config in `backend/config.toml`; Gemini API key comes from `$GEMINI_API_KEY`.
- Settings are read-only in the app; config changes require file edits and a backend restart.
- Do not add ad-hoc prompt strings in application code; route all LLM calls through the prompt-template framework.

Project trackers:

- `Unfinished.md`: current requirement audit and remaining implementation gaps
- `P2S.md`: problem tracker / bug backlog
- `HAnswerR.md`: detailed stage-1 specification

Historically recurring operational gaps are tracked in `P2S.md`, including restart resilience for in-memory job state, list pagination limits, and some frontend loading / pagination polish.

---

## Layout

```
backend/
  app/
    config.py              # typed settings loader
    main.py                # FastAPI + lifespan (Milvus auto-bootstrap)
    routers/               # ingest · answer · retrieve · practice · knowledge · admin
    prompts/               # PromptTemplate framework + parser / solver / vizcoder / variant_synth
    schemas/               # pydantic contracts (ParsedQuestion, AnswerPackage, Variant*)
    services/
      llm_client.py        # Gemini gateway + repair loop + FakeTransport + CostLedger Protocol
      gemini_transport.py  # google-genai transport
      ingest_service.py    # M2 ingest pipeline + dedup
      solver_service.py    # M3 Solver orchestration + SSE events
      vizcoder_service.py  # M4 VizCoder + AST validation
      viz_validator.py     # subprocess wrapper for validate.mjs
      embedding.py         # M5 dense embedders (OpenAI-compatible + Gemini + bge-m3)
      embedding_deps.py    # dedicated embedding-client wiring
      bge_m3_runtime.py    # shared local bge-m3 loader for dense+sparse heads
      sparse_encoder.py    # M5 sparse lexical (BM25 + bge-m3)
      vector_store.py      # Milvus / InMemory, dense + sparse
      rrf.py               # Reciprocal-Rank Fusion (§3.4)
      retrieval_service.py # single- + multi-route retrieval
      sediment_service.py  # M6 pattern/kp resolve + near-dup (§3.6)
      reindex_service.py   # rebuild Milvus vectors from PostgreSQL
      exam_service.py      # M7 bank selection + LLM variant synthesis
      cost_ledger.py       # M8 PG-backed CostLedger (writes llm_calls)
      milvus_setup.py
    db/                    # SQLAlchemy models + session + repo
  viz_validator/           # Node/acorn AST validator (§3.3.3)
  migrations/              # Alembic
  scripts/
    seed_knowledge.py      # M6 taxonomy seed (~60 KPs)
    rebuild_retrieval_index.py
    smoke_parse.py
  tests/                   # 86 tests

frontend/
  app/
    page.tsx               # / Ask
    q/[id]/page.tsx        # Answer view (3-column, sticky viz panel)
    library/page.tsx       # List + RRF search
    knowledge/page.tsx     # Tree + detail panel + pending + prompts
    practice/page.tsx      # Basket + exam builder + runner + self-check
    settings/page.tsx      # Config + cost dashboard + prompt registry
  components/VizSandbox.tsx
  public/viz/              # sandbox.html + H helper library (§3.3.5)
```

---

## What's next (Stage 2, HAnswerR.md §12)

Multi-user auth + roles · 错题本 spaced-repetition scheduler · handwriting
stylus OCR · multiple questions per image · mobile PWA · PDF exam export ·
knowledge-graph prerequisite edges · offline packaging.
