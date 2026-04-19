# HASys.md — HAnswer System Architecture & Design Detail

> Last updated: 2026-04-19
> Purpose: Comprehensive reference for the system architecture, design decisions, data flows, and implementation details.

---

## 1. System Overview

HAnswer is a local-first learning companion for Chinese middle-school and high-school math and physics students. It is **not** an answer machine — it is a teaching tool that:

1. Takes a photo of a math/physics problem and uses Google Gemini multimodal LLM to parse it into structured data
2. Generates a teaching-oriented answer that prioritizes reusable method patterns over numeric answers
3. Creates interactive JSXGraph visualizations (AST-validated for safety) rendered in a sandboxed iframe
4. Builds a personal knowledge base by sedimenting every solved question into PostgreSQL + Milvus vector database
5. Supports semantic retrieval with multi-route RRF fusion (dense + sparse + structural)
6. Enables practice exam generation with LLM-synthesized variant questions
7. Provides persistent multi-turn dialog with rolling memory for follow-up tutoring
8. Tracks LLM cost/usage per prompt version in a PostgreSQL ledger

---

## 2. Technology Stack

| Layer | Technology | Version | Notes |
|---|---|---|---|
| Backend | FastAPI | >=0.115 | Async, Python 3.11+ |
| ORM | SQLAlchemy | >=2.0 | Async engine with asyncpg driver |
| Migrations | Alembic | >=1.13 | 6 migration files |
| Validation | Pydantic v2 | >=2.7 | All data contracts |
| Database | PostgreSQL | 13+ | All structured data + cost ledger |
| Vector DB | Milvus | 2.6.14 | 12 collections (6 dense + 6 sparse) |
| LLM | Google Gemini | gemini-3.1-pro-preview | Sole LLM provider |
| Embeddings | gemini-embedding-2-preview or bge-m3 | 768-dim (default) or 1024-dim | Dense vectors; v2 model uses text prefixes, not task_type param |
| Sparse | BM25 or bge-m3 lexical | — | Chinese-friendly bigram tokenizer |
| Frontend | Next.js 16 + React 19 | App Router | TypeScript, port 3333 |
| Math Rendering | MathJax 3 | CDN | LaTeX inline ($...$) and block ($$...$$) |
| Visualization | JSXGraph | Vendor | Sandboxed iframe |
| AST Validation | Node.js + acorn | 18+ | Subprocess per viz |
| Containerization | Docker Compose | — | Milvus stack (etcd + MinIO + Milvus + Attu) |

---

## 3. Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        Browser (:3333)                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ Ask /     │ │ Answer   │ │ Library  │ │ Knowledge│ ...       │
│  │ Upload    │ │ /q/[id]  │ │          │ │ Tree     │           │
│  └─────┬─────┘ └─────┬────┘ └─────┬────┘ └─────┬────┘           │
│        │              │            │             │                │
│        └──────────────┴────────────┴─────────────┘                │
│                         /api/*                                     │
│                    (Next.js proxy → :8787)                         │
└────────────────────────────┬─────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────┐
│                    FastAPI Backend (:8787)                        │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │ Routers (7)                                                  │  │
│  │ ingest · answer · dialog · retrieve · practice · knowledge  │  │
│  │ · admin                                                      │  │
│  └──────────────────────┬──────────────────────────────────────┘  │
│                         │                                         │
│  ┌──────────────────────▼──────────────────────────────────────┐  │
│  │ Services (22 files)                                          │  │
│  │                                                              │  │
│  │  LLM Gateway:                                                │  │
│  │    llm_client.py → gemini_transport.py → google-genai SDK   │  │
│  │    (retry + repair loop + structured output + cost ledger   │  │
│  │     + incremental streaming via TopLevelStreamParser)       │  │
│  │                                                              │  │
│  │  Pipeline:                                                   │  │
│  │    ingest_service → solver_service → vizcoder_service       │  │
│  │    → sediment_service → indexer_service                     │  │
│  │                                                              │  │
│  │  Retrieval:                                                  │  │
│  │    embedding.py → vector_store.py → rrf.py                  │  │
│  │    → retrieval_service.py                                   │  │
│  │                                                              │  │
│  │  Other:                                                      │  │
│  │    dialog_service · exam_service · answer_job_service       │  │
│  │    · cost_ledger · milvus_setup · viz_validator             │  │
│  └──────────────────────┬──────────────────────────────────────┘  │
│                         │                                         │
│  ┌──────────────────────▼──────────────────────────────────────┐  │
│  │ Prompts (5 registered templates)                            │  │
│  │ parser · solver · vizcoder · variant_synth · dialog         │  │
│  │ Each: versioned PromptTemplate with DesignDecisions         │  │
│  └──────────────────────┬──────────────────────────────────────┘  │
│                         │                                         │
│  ┌──────────────────────▼──────────────────────────────────────┐  │
│  │ Data Layer                                                  │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │  │
│  │  │ PostgreSQL   │  │ Milvus       │  │ Gemini API   │      │  │
│  │  │ (20 tables)  │  │ (12 colls)   │  │ (LLM+embed)  │      │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘      │  │
│  └─────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

---

## 4. Data Flow: End-to-End Answer Pipeline

```
User drops image on /
        │
        ▼
POST /api/ingest/image
        │
        ├── Save image to disk (data/images/)
        ├── Gemini Parser (multimodal: image → ParsedQuestion JSON)
        ├── Dedup check (sha256 hash)
        └── Store in PostgreSQL (status="draft")
        │
        ▼
User edits parsed fields on / → PATCH /api/ingest/{id}
        │
        ▼
POST /api/answer/{id}/start → Background job spawns
        │
        ├─ Stage 1/4: parsed (already done from ingest)
        │   └── User confirms parsed result
        │
        ├─ Stage 2/4: solving
        │   ├── Gemini Solver: ParsedQuestion → AnswerPackage
        │   │   Sections: question_understanding, key_points_of_question,
        │   │             solution_step×N, key_points_of_answer,
        │   │             method_pattern, similar_questions,
        │   │             knowledge_points, self_check
        │   ├── Persist to answer_packages (section rows)
        │   └── User confirms solution
        │
        ├─ Stage 3/4: visualizing
        │   ├── Gemini VizCoder: AnswerPackage → Visualization[]
        │   ├── AST validation (Node.js + acorn)
        │   ├── Persist to visualizations table
        │   └── User confirms visualizations
        │
        └─ Stage 4/4: indexing (sediment)
            ├── Resolve/create MethodPattern + KnowledgePoints
            ├── Build pedagogical retrieval profile
            ├── Generate retrieval units (method, step, focus, keywords)
            ├── Batch embed (dense + sparse)
            ├── Near-dup detection (τ=0.96 cosine similarity)
            ├── Upsert to Milvus (6 dense + 6 sparse collections)
            └── Mark question status="answered"
```

---

## 5. Database Schema (20 Tables)

### Core Question Tables

| Table | Purpose | Key Fields |
|---|---|---|
| `ingest_images` | Uploaded image blobs | path, mime, size, sha256 |
| `questions` | Core question table | parsed_json (JSONB), answer_package_json (JSONB), subject, grade_band, difficulty (1-5), dedup_hash (unique), status, seen_count |
| `question_solutions` | Per-question solution variants | ordinal, is_current, answer_package_json, visualizations_json, sediment_json, stage_reviews_json, status |
| `answer_packages` | Streamed section storage | question_id, section (string), payload_json — enables resume-after-refresh |
| `question_stage_reviews` | Stage review state | stage, review_status (pending/confirmed/rejected), artifact_version, run_count, summary_json, refs_json, review_note |

### Taxonomy Tables

| Table | Purpose | Key Fields |
|---|---|---|
| `knowledge_points` | Taxonomy tree | parent_id (self-ref), name_cn, path_cached, subject, grade_band, status (pending/live), seen_count, embedding_ref |
| `method_patterns` | Named method patterns | name_cn, subject, grade_band, when_to_use, procedure_json, pitfalls_json, status, seen_count |
| `pitfalls` | Pitfalls linked to patterns | name_cn, description, pattern_id |
| `question_kp_link` | Question ↔ KP (M:N) | question_id, kp_id, weight |
| `question_pattern_link` | Question ↔ Pattern (M:N) | question_id, pattern_id, weight |

### Retrieval Tables

| Table | Purpose | Key Fields |
|---|---|---|
| `question_retrieval_profiles` | Pedagogical index profile | question_id, solution_id (unique), profile_json |
| `retrieval_units` | Semantic retrieval units | question_id, solution_id, unit_kind, title, text, keywords_json, weight, source_section |
| `solution_steps` | Individual solution steps | question_id, step_index, statement, rationale, formula, why_this_step, viz_ref |

### Visualization Table

| Table | Purpose | Key Fields |
|---|---|---|
| `visualizations` | Persisted JSXGraph viz code | question_id, viz_ref, title, caption, learning_goal, helpers_used_json, jsx_code, params_json, animation_json |

### Exam Tables

| Table | Purpose | Key Fields |
|---|---|---|
| `exams` | Generated practice exams | name, config_json |
| `exam_items` | Individual exam items | exam_id, position, source_question_id, synthesized_payload_json, answer_outline, rubric |

### Dialog Tables

| Table | Purpose | Key Fields |
|---|---|---|
| `conversation_sessions` | Multi-turn dialog sessions | question_id (nullable anchor), title, latest_summary, key_facts_json, open_questions_json |
| `conversation_messages` | Individual messages | conversation_id, role (user/assistant/system), sequence_no, content, metadata_json |
| `conversation_memory_snapshots` | Rolling memory snapshots | conversation_id, sequence_no, summary, key_facts_json, open_questions_json |

### Cost Tracking

| Table | Purpose | Key Fields |
|---|---|---|
| `llm_calls` | Cost ledger | task, prompt_version, model, prompt_tokens, completion_tokens, cost_usd, latency_ms, status, error |

---

## 6. API Surface

### Ingest (`/api/ingest/`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/image` | Upload + Gemini Parser → ParsedQuestion |
| PATCH | `/{id}` | Edit parsed fields |
| GET | `/{id}/image` | Serve original image |
| POST | `/{id}/rescan` | Re-parse same image |
| POST | `/{id}/replace-image` | Replace image + re-parse |

### Answer (`/api/answer/`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/{id}/start` | Start background answer job |
| POST | `/{id}/stages/{stage}/confirm` | Confirm a stage |
| POST | `/{id}/stages/{stage}/rerun` | Reject + rerun a stage |
| POST | `/{id}` | SSE stream of AnswerPackage sections |
| GET | `/{id}/resume` | Stored status + sections (poll after refresh) |

### Questions (`/api/questions/`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Library list with filters + facets |
| GET | `/{id}` | Full persisted question + answer |
| POST | `/{id}/solutions` | Create new solution variant |

### Retrieve (`/api/retrieve/`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/similar` | Hybrid search (auto/text/kp/pattern modes) |

### Practice (`/api/practice/`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/exam` | Build exam (bank + LLM variants) |
| GET | `/exam/{id}` | Hydrated exam detail |

### Knowledge (`/api/knowledge/`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/tree` | Taxonomy tree |
| GET | `/pending` | Pending KPs + patterns |
| GET | `/patterns` | List method patterns |
| GET | `/kp/{id}/detail` | Related questions + co-occurring patterns |
| GET | `/pattern/{id}/detail` | Related questions + pitfalls |
| POST | `/promote` | Promote pending node to live |
| POST | `/reject` | Delete pending node |
| POST | `/merge` | Merge from_id into into_id |

### Dialog (`/api/dialog/`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/sessions` | List all sessions |
| POST | `/sessions` | Create new session |
| GET | `/sessions/{id}` | Session detail + messages |
| POST | `/sessions/{id}/messages` | Send message + get Gemini reply |
| GET | `/stats` | Dialog analytics counts |

### Admin (`/api/admin/`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/config` | Active config (key masked) |
| GET | `/llm-cost` | Aggregate cost/latency over N days |
| GET | `/prompts` | Prompt registry summary |
| GET | `/prompts/{name}/explain` | Design decisions + schema |
| POST | `/prompts/{name}/preview` | Render prompt (no LLM call) |

---

## 7. LLM Integration

### Gemini Models Used
| Task | Model | Input → Output |
|---|---|---|
| Parser | gemini-3.1-pro-preview | Image + text → ParsedQuestion (structured JSON) |
| Solver | gemini-3.1-pro-preview | ParsedQuestion → AnswerPackage (teaching-oriented) |
| VizCoder | gemini-3.1-pro-preview | AnswerPackage → Visualization[] (JSXGraph code) |
| VariantSynth | gemini-3.1-pro-preview | Source question → VariantQuestion[] |
| Dialog | gemini-3.1-pro-preview | Context + memory + message → ConversationTurnResult |
| Embedding | gemini-embedding-2-preview (recommended), gemini-embedding-001, or legacy text-embedding-004 | Text → dense vectors (768-dim default) |

### LLM Client Architecture
```
GeminiClient (llm_client.py)
  │
  ├── Retry with exponential backoff (tenacity)
  ├── Structured output validation (pydantic)
  ├── Repair loop (re-prompt with validation error, max 2 attempts)
  ├── Cost tracking (PgCostLedger → llm_calls table)
  ├── Streaming support (two paths):
  │   ├── call_structured — bulk: wait for full response, validate, repair
  │   └── call_structured_streaming — incremental: yield (key, value) tuples
  │       as each top-level JSON field completes via TopLevelStreamParser,
  │       then yield validated model. Falls back to bulk+repair on failure.
  │
  └── GoogleGeminiTransport (gemini_transport.py)
       ├── google-genai SDK: generate_content + generate_content_stream
       ├── generate_json_stream_iter: true chunk-by-chunk iterator
       │   yielding StreamChunk(text, prompt_tokens, completion_tokens)
       ├── google-generativeai SDK: legacy text-embedding-004 fallback
       └── Embedding v2 model (gemini-embedding-2-preview):
           uses text prefixes ("task: search result | query: ...") not task_type param
```

### Prompt Template Framework

Every LLM call goes through a versioned `PromptTemplate` subclass (defined in `app/prompts/base.py`). Each template has:
- **Class-level metadata**: version, name, purpose, input/output descriptions, design decisions
- **Required methods**: `system_message(**kwargs)`, `user_message(**kwargs)`, `schema` (JSON Schema)
- **Optional**: `fewshot_examples(**kwargs)` for topic-aware few-shot injection
- **Assembly**: `build(**kwargs)` → `[system, *fewshot, user]` message list

Registered prompts (in `PromptRegistry` singleton):

| Name | Version | File | Purpose |
|---|---|---|---|
| parser | v1.0 | parser_prompt.py | Image → ParsedQuestion |
| solver | v1.0 | solver_prompt.py | ParsedQuestion → AnswerPackage |
| vizcoder | v1.0 | vizcoder_prompt.py | AnswerPackage → Visualization[] |
| variant_synth | v1.0 | variant_synth_prompt.py | Source question → variants |
| dialog | v1.0 | dialog_prompt.py | Multi-turn tutoring with rolling memory |

Few-shot examples are auto-selected by `ParsedQuestion.topic_path` from `prompts/fewshot/<subject>/<grade_band>/*.json`.

---

## 8. Retrieval Strategy

### Multi-Route Hybrid Retrieval

```
Query / anchor
    │
    ├── Dense route (ANN on 4 embedding surfaces):
    │   ├── q_emb (legacy question text)
    │   ├── question_full_emb (full parsed question)
    │   ├── answer_full_emb (full rendered answer)
    │   └── retrieval_unit_emb (pedagogical units)
    │
    ├── Sparse route (BM25/bge-m3 lexical on same 4 surfaces):
    │   └── *_sparse companion collections
    │
    └── Structural route (PostgreSQL):
        ├── shared method_pattern overlap
        └── shared knowledge_point overlap
                │
                ▼
        RRF fuse (k=60, configurable weights)
                │
                ▼
        PG hydrate + difficulty filters → top-K
```

### 4 Embedding Surfaces
1. **Whole question text** — full parsed question rendering
2. **Whole answer text** — full rendered answer
3. **Pedagogical retrieval units** — method, step, focus, keyword profile units
4. **Legacy question text** — original q_emb collection

### Milvus Collections (12 total)
- Dense (HNSW, IP metric): `q_emb`, `question_full_emb`, `answer_full_emb`, `retrieval_unit_emb`, `pattern_emb`, `kp_emb`
- Sparse (SPARSE_INVERTED_INDEX, IP metric): `q_emb_sparse`, `question_full_emb_sparse`, `answer_full_emb_sparse`, `retrieval_unit_emb_sparse`, `pattern_emb_sparse`, `kp_emb_sparse`

### Embedding Configuration
- **Fast remote mode**: Gemini `gemini-embedding-2-preview` (768-dim) + BM25 sparse. No local model load. V2 model uses text prefixes instead of task_type API param.
- **Strong local mode**: bge-m3 (1024-dim) dense + bge-m3 lexical sparse. Requires `pip install -e '.[retrieval]'`.

Switching embedder requires Milvus dense collection rebuild: `python -m scripts.rebuild_retrieval_index --recreate-dense --recreate-sparse`

---

## 9. Visualization Safety

### Three-Layer Safety
1. **AST Validation**: LLM-generated JavaScript is validated by Node.js + acorn subprocess (`backend/viz_validator/validate.mjs`). Only allow-listed AST node types are permitted.
2. **CSP-Locked Sandbox**: Code executes inside `<iframe sandbox="allow-scripts">` with strict CSP that blocks `eval()`, `Function()`, and network access.
3. **Runtime Budget**: Initial render budget check + animation-frame overrun shutdown in `sandbox.html`.

### Visualization Pipeline
```
AnswerPackage → Gemini VizCoder → Visualization[]
                                         │
                                    AST validate (acorn)
                                         │
                                    Persist to PG
                                         │
                                    postMessage protocol:
                                    init → render → update-params → dispose
                                    (VizSandbox.tsx ↔ sandbox.html)
```

### H Helper Library (`public/viz/h-library.js`)
Modules: `H.shapes`, `H.plot`, `H.phys`, `H.anim`, `H.geom` — curated functions for common math/physics visualizations (triangle, circle, vector field, spring-mass, etc.).

---

## 10. Frontend Pages

| Page | Path | Key Features |
|---|---|---|
| Ask | `/` | Drag-drop upload, camera capture button (`capture="environment"`), localStorage recent uploads strip, Gemini parse with MathJax preview, inline editing, subject hint, confidence badge, start answer |
| Answer | `/q/[id]` | 3-column layout (left TOC rail, center sections, right viz panel). Polls `/resume` for progress (sections appear incrementally during streaming). Stage confirm/rerun. Solution variants. Error cards with retry. |
| Library | `/library` | Filter by subject/grade/difficulty + date range pickers. Semantic search via RRF. Per-route rank traces. Facet-driven navigation. |
| Knowledge | `/knowledge` | Tree view (live/pending colors). KP detail panel with clickable pattern links. Pattern detail panel showing when_to_use, procedure, pitfalls, linked pitfalls, related questions. Promote/merge/reject pending nodes. |
| Practice | `/practice` | localStorage basket, search-add from question library, count + difficulty config, exam runner with answer outline reveal, self-check (✓/✗/?). |
| Dialog | `/dialog` | Multi-turn chat. Create sessions (free-form or question-anchored). Send messages, display replies with follow-up suggestions. |
| Settings | `/settings` | Config dashboard (masked key, models, retrieval/dialog knobs). Cost ledger by prompt/day. Dialog analytics. Prompt registry. |

---

## 11. Configuration Reference

All backend config in `backend/config.toml` (git-ignored). Sections:

| Section | Key Fields |
|---|---|
| `[gemini]` | model_parser, model_solver, model_vizcoder, model_embed, embed_dim. API key from `$GEMINI_API_KEY` env only. |
| `[postgres]` | dsn (asyncpg driver required) |
| `[milvus]` | host, port, database, auto_bootstrap, recreate_dense_on_dim_mismatch |
| `[server]` | host, port, cors_origins |
| `[storage]` | image_dir |
| `[llm]` | max_retries, max_repair_attempts, request_timeout_s, parser_timeout_s, solver_timeout_s, vizcoder_timeout_s, dialog_timeout_s, embed_timeout_s, stream_solver_json, stream_vizcoder_json |
| `[retrieval]` | embedder, sparse_encoder, multi_route, rrf_k, route_weights_*, bge_m3_model, bge_m3_device, bge_m3_dense_dim, wide_k_multiplier |
| `[dialog]` | model_chat, recent_messages, max_question_context_chars, max_summary_chars, max_key_facts, max_open_questions |

---

## 12. Pydantic Data Contracts (schemas/llm.py)

| Model | Purpose |
|---|---|
| `ParsedQuestion` | Gemini Parser output: subject, grade_band, topic_path, question_text, given, find, difficulty, confidence |
| `AnswerPackage` | Gemini Solver output: question_understanding, key_points, solution_steps, method_pattern, similar_questions, knowledge_points, self_check |
| `Visualization` | Gemini VizCoder output: jsx_code, params (slider/toggle), animation config |
| `VisualizationList` | Wrapper for VizCoder structured output |
| `VariantQuestion` | Gemini VariantSynth output: statement, answer_outline, rubric, difficulty |
| `ConversationMemory` | Dialog rolling memory: summary, key_facts, open_questions |
| `ConversationTurnResult` | Dialog turn output: assistant_reply, follow_up_suggestions, memory |
| `PedagogicalIndexProfile` | Retrieval profile with query_texts, novelty flags, method labels, etc. |
| `RetrievalUnit` | Semantic unit: unit_kind, title, text, keywords, weight, source_section |

---

## 13. Key Design Decisions

1. **Teacher-first, solver-second**: AnswerPackage puts reusable `method_pattern` (方法模式) ahead of the numeric answer.
2. **Prompt Template framework**: No ad-hoc prompt strings in application code. Every LLM call goes through versioned templates with documented design decisions.
3. **Safety by construction**: LLM visualization code is AST-validated, then executed in a CSP-locked sandbox iframe with postMessage communication.
4. **Knowledge sediment**: Every solved question resolves/creates KPs + patterns, builds retrieval profiles, embeds into Milvus, promotes pending nodes only on explicit user review.
5. **Conversation memory, not transcript replay**: Dialog sends compressed context (rolling summary + key facts + open questions + recent turns) instead of full transcript.
6. **Local-first**: Everything runs locally. The only outbound network call is to Gemini API.
7. **Stage review workflow**: Each pipeline stage (parsed → solving → visualizing → indexing) requires explicit user confirmation before proceeding to the next stage.

---

## 14. Known Gaps (from Unfinished.md and P2S.md)

**Completed in recent update:**
- ~~True incremental section streaming~~ — DONE via `TopLevelStreamParser` + `call_structured_streaming`
- ~~Ask page camera capture + recent uploads~~ — DONE
- ~~Library date range filters~~ — DONE
- ~~Pattern detail browsing + pitfalls~~ — DONE
- ~~Practice search-add~~ — DONE
- ~~Settings model editing~~ — Downgraded: intentionally file-only

**Remaining (from P2S.md):**
- B2: In-memory job state lost on process restart — questions can get stuck in intermediate status
- B5: `list_questions` fetches max 500 rows then filters in Python — no pagination
- B7: SSE stream holds a single uncommitted transaction for the entire solve duration
- B8/B9: Dialog sequence number race; message append split across multiple sessions
- B10: BM25 corpus statistics reset on restart
- F5/F6: No loading/error boundaries in routes; Library has no pagination

See `P2S.md` for the full problem tracker (51 items, many already fixed).
