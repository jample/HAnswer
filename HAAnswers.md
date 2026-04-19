# HAnswer Storage Schema Reference

Complete schema for question, answer, and visualization data — covering both PostgreSQL (structured data + JSONB payloads) and Milvus (vector index).

---

## PostgreSQL — 20 tables

### A. Question & Answer

| Table | Key Columns | Purpose |
|---|---|---|
| `ingest_images` | `id`, `path`, `mime`, `sha256` | Source image file reference |
| `questions` | `id`, `parsed_json` (JSONB), `answer_package_json` (JSONB), `subject`, `grade_band`, `difficulty`, `dedup_hash`, `status` | Core question record; `parsed_json` is `ParsedQuestion`; `answer_package_json` is the current `AnswerPackage` |
| `question_solutions` | `id`, `question_id`, `ordinal`, `is_current`, `answer_package_json`, `visualizations_json`, `sediment_json`, `stage_reviews_json` | Versioned solutions (each rerun appends a new row) |
| `answer_packages` | `id`, `question_id`, `section`, `payload_json` | Incremental streaming sections so the resume endpoint can show partial results mid-generation |
| `question_stage_reviews` | `id`, `question_id`, `stage` (`parsed`/`solving`/`visualizing`/`indexing`), `review_status`, `artifact_version` | Per-stage review/confirm/rerun workflow |

#### `parsed_json` shape — `ParsedQuestion`

```json
{
  "subject": "math | physics",
  "grade_band": "junior | senior",
  "topic_path": ["..."],
  "question_text": "...",
  "given": ["..."],
  "find": ["..."],
  "diagram_description": "...",
  "difficulty": 3,
  "tags": ["..."],
  "confidence": 0.95
}
```

#### `answer_package_json` shape — `AnswerPackage`

```json
{
  "question_understanding": {
    "restated_question": "...",
    "givens": ["..."],
    "unknowns": ["..."],
    "implicit_conditions": ["..."]
  },
  "key_points_of_question": ["..."],
  "solution_steps": [
    {
      "step_index": 1,
      "statement": "...",
      "rationale": "...",
      "formula": "...",
      "why_this_step": "...",
      "viz_ref": "viz_id_or_empty"
    }
  ],
  "key_points_of_answer": ["..."],
  "method_pattern": {
    "pattern_id_suggested": "...",
    "name_cn": "...",
    "when_to_use": "...",
    "general_procedure": ["..."],
    "pitfalls": ["..."]
  },
  "similar_questions": [
    { "statement": "...", "answer_outline": "...", "same_pattern": true, "difficulty_delta": 0 }
  ],
  "knowledge_points": [
    { "node_ref": "existing_id or new:path>to>node", "weight": 0.9 }
  ],
  "self_check": ["..."]
}
```

---

### B. Visualizations

| Table | Key Columns | Purpose |
|---|---|---|
| `visualizations` | `id`, `question_id`, `viz_ref`, `title`, `caption`, `learning_goal`, `engine`, `jsx_code`, `ggb_commands_json`, `ggb_settings_json`, `params_json`, `animation_json` | One row per viz tab per question |
| `solution_steps` | `id`, `question_id`, `step_index`, `statement`, `rationale`, `formula`, `why_this_step`, `viz_ref` | Denormalized step rows enabling per-step retrieval |

#### `ggb_commands_json` — list of GeoGebra command strings (engine=geogebra)

```json
[
  "K=(1,1)",
  "t=Slider(-2,1.5,0.1)",
  "T=(t,0)",
  "cK=Circle(K,2)",
  "P=(x(K)+2*cos(tAng), y(K)+2*sin(tAng))",
  "SetColor(cK, 0, 128, 255)",
  "SetLineThickness(cK, 2)"
]
```

#### `ggb_settings_json` shape

```json
{
  "app_name": "classic",
  "perspective": "G",
  "coord_system": [-4, 7, -3, 7],
  "axes_visible": true,
  "grid_visible": true,
  "show_algebra_input": false,
  "show_tool_bar": false,
  "show_menu_bar": false
}
```

#### `params_json` — slider/toggle definitions

```json
[
  { "name": "t", "label_cn": "参数 t", "kind": "slider", "min": -2, "max": 1.5, "step": 0.1, "default": 0 },
  { "name": "tAng", "label_cn": "动点 P 位置", "kind": "slider", "min": 0, "max": 6.28, "step": 0.05, "default": 1 }
]
```

#### `animation_json`

```json
{ "kind": "loop", "duration_ms": 4000, "drives": ["tAng"] }
```

---

### C. Retrieval Index (what Milvus points back to)

| Table | Key Columns | Purpose |
|---|---|---|
| `question_retrieval_profiles` | `id`, `question_id`, `solution_id`, `profile_json` | Aggregated query-text surfaces used to build embeddings |
| `retrieval_units` | `id`, `question_id`, `solution_id`, `unit_kind`, `title`, `text`, `keywords_json`, `weight`, `source_section` | Fine-grained pedagogical chunks (one per step / formula / pitfall / KP); each gets its own vector |

`unit_kind` values: `question_full`, `answer_full`, `solution_step`, `formula`, `pitfall`, `key_insight`, `kp_summary`.

---

### D. Taxonomy

| Table | Purpose |
|---|---|
| `knowledge_points` | Tree of curriculum nodes; `path_cached` = `"A>B>C"` |
| `method_patterns` | Reusable solve patterns with `procedure_json`, `pitfalls_json` |
| `pitfalls` | Named common mistakes, linked to a pattern |
| `question_kp_link` | M:N question↔KP with `weight` |
| `question_pattern_link` | M:N question↔pattern with `weight` |

---

### E. Dialog

| Table | Purpose |
|---|---|
| `conversation_sessions` | Session linked to a question; rolling `latest_summary`, `key_facts_json`, `open_questions_json` |
| `conversation_messages` | Turn-by-turn `role` / `content` |
| `conversation_memory_snapshots` | Periodic rolling summaries |

---

### F. Operational

| Table | Purpose |
|---|---|
| `exams` / `exam_items` | Practice exam bundles; items point to source questions or synthesized variants |
| `llm_calls` | Every API call: model, tokens, `cost_usd`, `latency_ms`, `status` — cost ledger |

---

## Milvus — 12 collections (6 dense + 6 sparse)

Each dense collection has a companion `*_sparse` with a `SPARSE_INVERTED_INDEX` for BM25/bge-m3 lexical search. RRF fusion combines both at query time.

| Dense collection | Scalar payload | What is embedded |
|---|---|---|
| `q_emb` | `ref_pg_id` (solution ref), `difficulty` | Raw `question_text` only — fast lookup, near-dup check |
| `question_full_emb` | `question_id`, `difficulty` | Full question profile text (question + givens + conditions) |
| `answer_full_emb` | `question_id`, `difficulty` | Full answer text (steps + insights + formulas) |
| `retrieval_unit_emb` | `retrieval_unit_id`, `unit_kind`, `difficulty` | Individual pedagogical chunks |
| `pattern_emb` | `pattern_id` | Method pattern summary text |
| `kp_emb` | `kp_id` | Knowledge point name + path |

Sparse companions (`*_sparse`): same scalar fields, `SPARSE_FLOAT_VECTOR` instead of `FLOAT_VECTOR`.

### What gets embedded during the indexing stage

```
sediment_service.sediment()
  └─ embed_many([
       q_text,                → q_emb  +  q_emb_sparse
       question_full_text,    → question_full_emb  +  question_full_emb_sparse
       answer_full_text,      → answer_full_emb  +  answer_full_emb_sparse
       pattern_summary,       → pattern_emb  +  pattern_emb_sparse
       *kp_texts,             → kp_emb  +  kp_emb_sparse
       *retrieval_unit_texts, → retrieval_unit_emb  +  retrieval_unit_emb_sparse
     ])
```

---

## Data-flow summary (full solve cycle)

```
Image upload
  → ingest_images
  → questions (status=parsed, parsed_json set)

Solver (SSE stream)
  → answer_packages (section rows, incremental)
  → questions.answer_package_json (on confirm)
  → question_solutions (versioned row)
  → question_stage_reviews (stage=solving)

VizCoder
  → visualizations (one row per viz)
  → solution_steps (denormalized)
  → question_stage_reviews (stage=visualizing)

Sediment / Indexing
  → method_patterns + question_pattern_link
  → knowledge_points + question_kp_link
  → question_retrieval_profiles
  → retrieval_units
  → Milvus upserts (12 collections)
  → question_stage_reviews (stage=indexing)
```

---

## Gaps — not yet indexed

| Data | Stored in PG | In Milvus |
|---|---|---|
| Visualization content (`ggb_commands`, `caption`, `learning_goal`) | ✅ `visualizations` | ✗ not embedded |
| `similar_questions[]` from AnswerPackage | ✅ inside `answer_package_json` | ✗ not separately indexed |
| Dialog messages | ✅ `conversation_messages` | ✗ not embedded |

To make visualization content retrievable by semantic search, add a `viz_emb` Milvus collection and extend `sediment_service` to embed `caption + learning_goal` per `VisualizationRow`.
