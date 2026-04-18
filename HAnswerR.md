# HAnswer — Stage‑1 Requirements & Architecture Specification

> Status: Stage‑1 (Requirements Freeze)
> Audience: Implementation team (frontend, backend, data, LLM prompt engineering)
> Product one‑liner: **A learning companion that turns a photo of a math/physics problem into a structured, visual, pattern‑teaching answer — and sediments every solved question into a reusable knowledge base.**

---

## 0. Document Purpose

This document is the authoritative stage‑1 specification for the HAnswer application. It details functional scope, architecture, data model, LLM prompting strategy, visualization subsystem design, API contracts, UI/UX, milestones, and verification plans sufficient for the engineering team to begin implementation without further clarification.

Stage‑1 locked decisions:

| Decision | Value |
|---|---|
| Users | Single local user, no authentication |
| Subjects | Math + Physics |
| UI language | Chinese (Simplified) |
| Frontend | Next.js (App Router, TypeScript) |
| Backend | FastAPI (Python 3.11+) |
| LLM | Google Gemini (multimodal + text + embedding) |
| Image parsing | Gemini multimodal directly |
| Vector DB | Milvus standalone (`milvus-standalone:19530`, default DB, no auth) |
| Relational DB | PostgreSQL (local, `psql -p5432 -U jianbo jianbo`) |
| Visualization | JSXGraph; LLM emits JS rendered inside a **sandboxed iframe** with AST validation |
| Math rendering | **MathJax 3** (`tex-chtml`, loaded from CDN); $…$ inline, $$…$$ display — project-wide rule, no other math renderer permitted |
| Knowledge model | Three‑tier taxonomy: KnowledgePoint · MethodPattern · Pitfall |
| Deployment | Services run directly on local machine (Milvus & PG pre‑installed) |

---

## 1. Product Overview

### 1.1 Vision
HAnswer is **not an answer machine**. It is a learning companion that prioritizes teaching *how to think about a class of problems* over delivering a single numeric result. Every answer is framed around a **method pattern** the student can reuse; every visualization is designed to make an idea *click*; every solved problem contributes to a growing personal knowledge base that powers retrieval and practice.

### 1.2 Target Users
- Chinese middle‑school students (初中, grades 7–9)
- Chinese high‑school students (高中, grades 10–12)
- Single local user in stage‑1 (the owner of the installation)

### 1.3 In‑Scope (Stage‑1)
- Upload **one image per question**, obtain a full structured answer package.
- Parse image → structured question using Gemini multimodal.
- Generate a teaching‑oriented answer with key points, method pattern, solution steps, visualizations, similar questions, knowledge‑point tags.
- Render interactive visualizations via JSXGraph inside a sandboxed iframe.
- Persist structured content in PostgreSQL and embeddings in Milvus.
- Similar‑question retrieval by current question, free text, knowledge point, or method pattern.
- Build a practice exam from selected questions/patterns; if gaps, LLM synthesizes pattern‑preserving variants.
- Browse and promote the knowledge taxonomy (pending → live).
- Maintain persistent multi-turn tutoring dialogs with rolling memory, optionally anchored to a solved question, and store transcripts for later analysis.

### 1.4 Out of Scope (Stage‑1, recorded for Stage‑2+)
- Multi‑user auth, roles (teacher/student/admin), sharing
- Multiple questions per image
- Handwriting stroke capture, ink OCR
- Mobile native app (web responsive only)
- Spaced‑repetition scheduler / 错题本 automation
- PDF export, printable exam layout tuning
- Offline mode, cloud deployment

---

## 2. Personas & Primary Journeys

### 2.1 Personas
- **P1 — Initial 小林 (初二)**: Stuck on a geometry problem; needs a visual and a plain explanation of *why* a construction works.
- **P2 — 高二 王同学**: Preparing for exams; wants to recognize patterns (配方法, 辅助线, 动量守恒) and drill with targeted practice.

### 2.2 Journeys
1. **Ask** — Upload photo → confirm parsed question → receive streaming structured answer with interactive visualization.
2. **Learn** — Review key points and method pattern; open 3 similar questions showing the same pattern at varied difficulty.
3. **Practice** — Pick topics/patterns → configure count & difficulty → take generated exam → self‑check answers.
4. **Retrieve** — Natural‑language query ("涉及辅助线倍长中线的题") → browse hits with method‑pattern badges.
5. **Sediment** — As questions accumulate, the Knowledge page shows the growing taxonomy; user promotes LLM‑suggested new patterns into the live taxonomy.
6. **Dialogue** — After reading the answer, keep asking follow-up questions inside a persistent conversation; the backend uses cached memory so each new Gemini call keeps continuity without replaying the full transcript.

---

## 3. Functional Requirements

### 3.1 Question Ingestion
- **Inputs**: JPG / PNG / HEIC / WEBP; max 8 MB; single image per question.
- **Pipeline**: Upload → server stores original blob → Gemini multimodal → `ParsedQuestion`.
- **ParsedQuestion schema** (pydantic, also persisted):
  - `subject`: `math` | `physics`
  - `grade_band`: `junior` | `senior`
  - `topic_path`: array of taxonomy node IDs or names (best‑effort)
  - `question_text`: LaTeX‑normalized full statement
  - `given`: list of given conditions (LaTeX allowed)
  - `find`: list of unknowns/goals
  - `diagram_description`: text description of any figure present
  - `difficulty`: 1–5 (LLM estimate)
  - `tags`: free‑form strings
  - `confidence`: 0–1, per‑field also allowed for key fields
- **User review step**: parsed result is editable before kicking off answer generation. Edits are persisted and replace the LLM parse for downstream steps.
- **Failure handling**: if confidence < 0.5 on `question_text`, UI surfaces a warning and asks the user to confirm or retake.

### 3.2 Answer Generation — the `AnswerPackage`
The LLM returns a single JSON object matching a strict schema. Every field is required unless marked optional.

```text
AnswerPackage
├── question_understanding
│     ├─ restated_question (LaTeX)
│     ├─ givens[]            (LaTeX)
│     ├─ unknowns[]          (LaTeX)
│     └─ implicit_conditions[] (e.g., "三角形内角和=180°")
├── key_points_of_question[]   // what makes this problem non-trivial
├── solution_steps[]
│     ├─ step_index
│     ├─ statement            (short action)
│     ├─ rationale            ("why this step works")
│     ├─ formula              (LaTeX, optional)
│     ├─ why_this_step        ("why we chose this over alternatives")
│     └─ viz_ref              (id into visualizations[], optional)
├── key_points_of_answer[]     // insights student must internalize
├── method_pattern
│     ├─ pattern_id_suggested  (UUID or existing pattern id)
│     ├─ name_cn               (e.g., "辅助线-倍长中线")
│     ├─ when_to_use
│     ├─ general_procedure[]
│     └─ pitfalls[]
├── visualizations[]           // see 3.3
├── similar_questions[]        // exactly 3 items
│     ├─ statement (LaTeX)
│     ├─ answer_outline
│     ├─ same_pattern (true)
│     └─ difficulty_delta      (-2..+2)
├── knowledge_points[]
│     ├─ node_ref              (existing id OR "new:" + proposed path)
│     └─ weight                (0..1, how central to this question)
└── self_check[]               // short verification hints for the student
```

- Generation is **streamed via SSE** in section order, so the UI renders progressively.
- The backend validates the JSON against a pydantic model; on validation failure it runs a **repair loop** — re‑prompt with the validation error (max 2 retries) before surfacing an error to the user.
- LLM is instructed to be **teacher‑first, solver‑second**: the method pattern is the primary deliverable; the numeric answer is secondary.

### 3.3 Visualization Subsystem (critical design)

**Goal**: Produce rich, interactive JSXGraph visualizations that actually help understanding (e.g., a point moving on a circle parameterized by the answer, a slider changing a function's coefficient, a projectile animation) — without opening a code‑execution security hole.

#### 3.3.1 Visualization object (LLM output)
Each item in `visualizations[]` has:
- `id` (string, referenced by `solution_steps[].viz_ref`)
- `title_cn`, `caption_cn`
- `learning_goal` — the one sentence the student should take away
- `interactive_hints[]` — UI hints ("拖动 P 观察 OP 长度变化")
- `helpers_used[]` — names of helpers from the HAnswer helper library (preferred)
- `jsx_code` — a **JavaScript function body** (no top‑level statements outside) with exactly this signature:
  ```js
  // function body; `board`, `JXG`, `H`, `params` are provided.
  // Must return an object: { update(params), destroy() } OR undefined.
  ```
- `params` — optional declaration of sliders/toggles: `[{ name, label_cn, kind: "slider"|"toggle", min, max, step, default }]`
- `animation` — optional: `{ kind: "loop"|"once", duration_ms, drives: [paramName] }`

#### 3.3.2 Execution sandbox
Rendered in a dedicated `<iframe sandbox="allow-scripts">` served from a **distinct origin/path** (`/viz-sandbox.html`). Hard guarantees:
- CSP: `default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src data:;` — no network, no fonts, no media, no frames.
- `sandbox` attribute omits `allow-same-origin`, `allow-forms`, `allow-top-navigation`, `allow-popups`, `allow-storage-access-by-user-activation`.
- No `localStorage` / `sessionStorage` / `IndexedDB` access (origin isolation + CSP).
- postMessage is the **only** channel between host and sandbox; a typed protocol governs: `init`, `render`, `update-params`, `dispose`, `ready`, `error`, `metric`.
- Inside the sandbox runtime, a shim freezes `window`, `document`, `fetch`, `XMLHttpRequest`, `WebSocket`, `Worker`, `importScripts`, `eval`, `Function`, `setTimeout(string, …)`, `setInterval(string, …)`, and removes them from global before executing LLM code.
- Runtime execution must not rely on the browser `Function` constructor or `unsafe-eval`; validated user code is wrapped into a controlled inline function block inside the isolated iframe.

#### 3.3.3 Pre‑execution AST validator
Before dispatching code into the sandbox, the backend (or a Node helper) parses `jsx_code` with `acorn` and rejects on:
- Identifier references to any globals not on the allow‑list: `board`, `JXG`, `H`, `params`, `Math`, `Number`, `Array`, `Object`, `Boolean`, `String`, `JSON`, `console`, `requestAnimationFrame`, `cancelAnimationFrame`.
- `MemberExpression` with computed `["eval"]` / `["Function"]` style access.
- `NewExpression` on `Function`, `WebSocket`, `Worker`, `XMLHttpRequest`.
- String arguments to `setTimeout` / `setInterval`.
- `import` / `ImportExpression` / dynamic `require`.
- Any `with` statement.
- AST node count > 2,000 or source length > 32 KB (cheap DoS guard).

Validation failures return a typed error that either triggers a single regeneration retry or is surfaced as a broken viz with a fallback static figure.

#### 3.3.4 Runtime watchdog
Inside the sandbox:
- Wall‑clock budget: 200 ms for initial `render`, 16 ms/frame for animation updates.
- Frame budget enforced via a `requestAnimationFrame` wrapper that disables the viz on repeated overruns.
- Error capture sends `error` messages back to host; host renders the fallback card.

#### 3.3.5 HAnswer helper library `H`
A curated, versioned helper surface exposed inside the sandbox so LLM prefers safe, idiomatic helpers over raw code. First‑release modules:
- `H.shapes`: `circle(cx, cy, r)`, `triangle(A, B, C)`, `polygon(points)`, `segmentWithLabel(P, Q, label)`
- `H.plot`: `functionGraph(fn, domain)`, `parametric({x, y}, tRange)`, `vectorField(…)`
- `H.phys`: `projectile({v0, angle, g})`, `springMass({k, m, x0})`
- `H.anim`: `animate(paramName, from, to, durationMs)`
- `H.geom`: `midpoint(P,Q)`, `reflect(P, line)`, `rotate(P, center, angleDeg)`, `intersectionPoint(a, b)`

The LLM is prompted with a cheatsheet of `H`; free JSXGraph via `JXG` / `board` remains available as a fallback.

#### 3.3.6 Example animation goals
- Point moving on a circle per parametric equation derived from the answer.
- Vector addition: slider controls vector magnitude/angle; triangle rule updates live.
- Function transformation: `y = a(x - h)^2 + k` with three sliders.
- Projectile motion: launch angle and speed sliders; trace rendered with fade.

### 3.4 Similar‑Question Retrieval
- **Query modes**: auto (from current question), free text, by knowledge point, by method pattern.
- **Embedding model**: the system supports two operator-selectable dense engines. Fast remote mode uses Gemini `text-embedding-004` (or `gemini-embedding-001`) with BM25 sparse retrieval. Stronger local mixed-language recall uses **BAAI/bge-m3** for both dense and sparse signals. bge-m3 is a unified model that produces dense + sparse (lexical) + multi-vector signals in a single forward pass. Dense + sparse heads should reuse the same loaded model instance so memory does not double when both routes are enabled.
- **Retrieval task typing**: when Gemini embeddings are active, query-time vectors should use `RETRIEVAL_QUERY` and indexed corpus vectors should use `RETRIEVAL_DOCUMENT`; this avoids the quality loss of embedding both sides with a single generic task type.
- **Dependency constraint**: the local bge-m3 path depends on `FlagEmbedding` and must pin a `transformers<5` compatible stack. The Gemini dense path may also need both Google SDKs in the same environment: `google-genai` for current Gemini APIs and `google-generativeai` for explicit `text-embedding-004` support.
- **Multi-route retrieval (RRF fusion)** is the production strategy:
  1. **Dense route** — Milvus ANN over four dense surfaces: legacy `q_emb`, `question_full_emb`, `answer_full_emb`, and `retrieval_unit_emb` (HNSW, IP metric), fused into a question-level ranking before the top-level RRF pass.
  2. **Sparse route** — Milvus `SPARSE_INVERTED_INDEX` over the companion sparse collections for those same four surfaces. Sparse weights come from bge-m3's lexical head, with an in-process online BM25 encoder (Chinese unigram + bigram + ASCII word + math symbol tokenization) as the zero-dependency fallback (`retrieval.sparse_encoder`).
  3. **Structural route** — PG count of shared `method_pattern` and `knowledge_point` links between the anchor and candidates (no ANN; pure relational aggregation).
  4. Each route returns a top-K list (K = `retrieval.wide_k_multiplier × k`, default 3×k, min 30); filters `subject`, `grade_band`, difficulty range, and excluded ids are pushed down into every route.
  5. **Reciprocal Rank Fusion**:
     $$\text{score}(d) = \sum_{r \in \text{routes}} w_r \cdot \frac{1}{k + \text{rank}_r(d)}$$
     with `k = retrieval.rrf_k` (default 60) and configurable per-route weights `route_weights_{dense,sparse,structural}`.
  6. Pedagogical-unit hits are collapsed back to question ids before the top-level RRF pass; final hydration also carries matched unit kinds/titles so the UI can explain *why* a result surfaced.
- **Single-route fallback** (`retrieval.multi_route = false`) keeps the original formula `score = 0.5·cos + 0.3·pattern_match + 0.2·kp_overlap` for deployments that don't have the sparse collection provisioned.
- Results always display pattern badge, shared knowledge points, and — in multi-route mode — the per-route rank breakdown for debugging.

#### 3.4.1 Pedagogical Retrieval Representation (special design)

For math/physics learning, **full context must be preserved**, because many good questions are long, layered, or hinge on a late condition. Therefore stage‑1 retrieval must **not** use arbitrary fixed-size text chunking of the canonical question or answer. Instead, HAnswer uses a **dual representation**:

1. **Canonical full artifacts** — stored intact for fidelity:
   - full `ParsedQuestion.question_text`
   - full `AnswerPackage`
2. **Derived pedagogical retrieval facets** — compact, semantically targeted views extracted from the full artifacts for future retrieval and query understanding.

Design principle:
- **No blind chunks.** We do not split by every N tokens / N characters because that damages mathematical context and breaks dependencies like "新定义在题干前半段, 最值条件在后半段".
- **Semantic slicing only.** Retrieval sub-units are created only when they correspond to a real pedagogical unit the student might later query.

#### 3.4.2 Retrieval units to generate per question

Every answered question produces the following retrieval representations:

| Unit kind | Purpose | Canonical source |
|---|---|---|
| `question_full` | Find globally similar questions while preserving all conditions | full `question_text` + givens + find + diagram description |
| `answer_full` | Find similar solved reasoning / complete answer style | full `AnswerPackage` rendered to teaching text |
| `question_focus` | Capture what makes the problem distinctive | `key_points_of_question`, hidden conditions, novelty markers |
| `answer_focus` | Capture what the student should remember | `key_points_of_answer`, self-check, transfer insight |
| `method` | Retrieve by solving technique | `method_pattern.name_cn`, `when_to_use`, `general_procedure`, `pitfalls` |
| `step` (repeated) | Retrieve by local reasoning move | one `solution_step` per row: `statement + rationale + why_this_step + formula` |
| `extension` | Retrieve by extension / "扩展思路" style query | synthesized transfer text: variants, harder version, alternative framing |
| `keyword_profile` | Strong lexical anchors for exact / sparse search | extracted topic words, aliases, novelty flags, curriculum markers |

Important rule:
- `question_full` and `answer_full` are the **primary semantic retrieval anchors**.
- `step`, `method`, `question_focus`, `answer_focus`, and `extension` are **secondary pedagogical anchors** used to answer more targeted study queries.

#### 3.4.3 What should be extracted beyond the current AnswerPackage

In addition to the existing `AnswerPackage`, the indexer should derive a `PedagogicalIndexProfile` per question with fields like:

```text
PedagogicalIndexProfile
├── curriculum_anchors[]
│     ├─ subject               // math | physics
│     ├─ grade_band            // junior | senior
│     ├─ textbook_stage        // e.g. 初二上 / 高一下 (best effort)
│     └─ topic_path[]
├── problem_form
│     ├─ novelty_flags[]       // 新定义, 阅读理解型, 压轴, 多问
│     ├─ object_entities[]     // 圆, 抛物线, 电路, 动点, 三角形...
│     ├─ target_types[]        // 最值, 证明, 求面积, 求轨迹, 求表达式...
│     └─ condition_signals[]   // 平行, 相切, 中点, 匀加速, 守恒...
├── question_focus[]
│     // 题目关键限制 / 真正决定解法的条件
├── answer_focus[]
│     // 做完后应记住的洞见
├── method_labels[]
│     // 配方法, 辅助线-倍长中线, 圆幂, 数形结合, 动量守恒...
├── extension_ideas[]
│     // 更难变式 / 换元视角 / 参数化推广 / alternative route
├── pitfalls[]
├── lexical_aliases[]
│     // 圆的最值 ~ 圆上最值 ~ 与圆有关的最值; 新定义题; 初二几何
└── query_texts
      ├─ question_full_text
      ├─ answer_full_text
      ├─ method_text
      ├─ step_texts[]
      └─ extension_text
```

This extraction can be produced by:
- the Solver directly for fields already present (`key_points_of_question`, `key_points_of_answer`, `method_pattern`, `solution_steps`)
- an additional lightweight **IndexerPrompt** after the answer is complete, responsible only for retrieval-oriented fields such as `novelty_flags`, `target_types`, `lexical_aliases`, and `extension_ideas`

Rationale:
- Students often query using curriculum or colloquial phrases such as:
  - "初二 圆的最值"
  - "新定义题"
  - "辅助线 倍长中线"
  - "电学 伏安法误差"
- These phrases are not always present verbatim in the question text, so explicit extraction is necessary.

#### 3.4.4 Query understanding and mixed retrieval

Future queries should be interpreted as a mixture of:
- **semantic intent** — "find something conceptually similar"
- **lexical anchors** — "新定义", "圆", "最值", "初二"
- **structural intent** — "same method", "same knowledge point", "same step pattern"

Therefore query handling should do the following before retrieval:

1. **Classify the query intent**:
   - similar question
   - similar answer / similar reasoning
   - same method
   - same key point / extension idea
   - curriculum browse (`初二`, `高中物理`, etc.)
2. **Extract must-have lexical anchors**:
   - grade/year markers (`初二`, `高一`)
   - object words (`圆`, `抛物线`, `电路`)
   - target words (`最值`, `证明`, `轨迹`)
   - novelty markers (`新定义`, `阅读理解`)
   - method words (`配方法`, `数形结合`, `守恒`)
3. **Expand aliases / normalize**:
   - `圆的最值` ↔ `与圆有关的最值`
   - `新定义题` ↔ `定义新运算 / 定义新规则`
   - `初二` ↔ `junior` + best-effort textbook stage metadata
4. **Search multiple retrieval units**, not just `question_full`

The retrieval routes should therefore become:

1. **Dense full-question route** — `question_full_emb`
2. **Dense full-answer route** — `answer_full_emb`
3. **Dense pedagogical-facet route** — `question_focus`, `answer_focus`, `method`, `step`, `extension`
4. **Sparse lexical route** — over all queryable units, especially `keyword_profile`, `question_full`, `method`, `extension`
5. **Structural route** — shared method pattern / KP / stage / novelty flag overlap
6. **Relational filter route** — subject, grade band, textbook stage, difficulty, taxonomy path

Fusion remains RRF, but with separate route families so the system can answer:
- "找同类型题" → prioritize `question_full` + `method`
- "找类似解法" → prioritize `answer_full` + `step`
- "找新定义/圆的最值/初二几何" → prioritize sparse + `keyword_profile` + curriculum filters

#### 3.4.5 Why this is better than chunking

This design beats naive chunking because:
- the **full question** remains searchable as a single semantic object
- the **full answer** remains searchable as a single teaching object
- retrieval sub-units are aligned to learning semantics (`method`, `step`, `extension`) instead of arbitrary token boundaries
- long questions still keep global dependencies intact
- short but high-signal study queries can still hit compact derived units

Summary rule:
- **Store whole. Retrieve whole + retrieve facets. Do not chunk blindly.**

#### 3.4.6 Ranking policy for student-facing search

The final ranking should be student-oriented rather than purely semantic:

$$
\text{final}(d)=
\alpha \cdot \text{question\_similarity} +
\beta \cdot \text{answer\_similarity} +
\gamma \cdot \text{method\_match} +
\delta \cdot \text{keyword\_anchor\_match} +
\epsilon \cdot \text{curriculum\_match}
$$

Recommended product policy:
- if the query contains strong lexical anchors like `新定义`, `初二`, `圆的最值`, those anchors should act as **hard boosts** or optional hard filters
- if the query is vague, semantic similarity should dominate
- if the user starts from an existing solved question, `method_match` and `question_full` similarity should dominate
- if the user asks for "类似答案/类似思路", `answer_full` + `step` + `extension` should dominate

#### 3.4.7 Storage recommendation

PostgreSQL should store the canonical question/answer plus the extracted retrieval facets in explicit rows so they can be inspected, regenerated, and re-embedded.

Recommended logical rows:
- one `question_retrieval_profile` row per question
- multiple `retrieval_units` rows per question, each with:
  - `unit_kind`
  - `title`
  - `text`
  - `keywords_json`
  - `weight`
  - `source_section`

Milvus should store vectors per retrieval unit family rather than only the question text.

Minimum collections:
- `question_full_emb`
- `answer_full_emb`
- `retrieval_unit_emb`
- sparse companions for each

This preserves inspectability:
- PG is the human-auditable source of extracted learning signals
- Milvus is the search index over those signals

### 3.5 Practice Exam Generation
- Inputs: list of source question IDs OR (topics/patterns + count + difficulty distribution).
- Algorithm:
  1. Pull candidates from PG question bank matching filters.
  2. If candidates < requested count, call LLM to synthesize variants that preserve the chosen `method_pattern` but vary surface features (numbers, named objects, diagram).
  3. Return an `Exam` (ordered items) with per‑item `answer_outline` and rubric hints.
- Persisted as `exams` + `exam_items`.

### 3.6 Knowledge Sediment & Accumulation

#### 3.6.1 Three‑tier taxonomy
- **KnowledgePoint**: concept node (e.g., 二次函数 > 顶点式 > 对称轴). Hierarchical (`parent_id`).
- **MethodPattern**: a reusable problem‑solving technique (e.g., 配方法求顶点, 辅助线-倍长中线, 动量守恒用于碰撞).
- **Pitfall**: a common mistake pattern (e.g., 忘记分类讨论 a=0).

#### 3.6.2 Write path (every successful answer)
1. Insert `questions` row with parsed + package JSON, difficulty, dedup_hash (SHA‑256 of normalized question text).
2. For each `knowledge_points[]` entry: resolve existing node or create a **pending** node; insert into `question_kp_link` with `weight`.
3. For `method_pattern`: resolve existing by name+subject+grade; else create **pending** pattern; insert into `question_pattern_link` with `weight=1`.
4. Build a deterministic `question_retrieval_profile` plus multiple `retrieval_units` (`question_focus`, `answer_focus`, `method`, `step`, `extension`, `keyword_profile`) from the parsed question and final answer package.
5. Insert embeddings into Milvus: question text → `q_emb`; whole rendered question → `question_full_emb`; whole rendered answer → `answer_full_emb`; each retrieval unit → `retrieval_unit_emb`; pattern summary (name + when_to_use + procedure) → `pattern_emb`; kp name + path → `kp_emb`. Each dense upsert is mirrored into the companion sparse collection (`*_sparse`) using the active sparse encoder (bge-m3 lexical head or online BM25), so the multi-route retrieval in §3.4 can search lexically without any extra ingest pass.
6. Update counters: `pattern.seen_count += 1`, `kp.seen_count += 1`.

#### 3.6.3 Dedup
- Before insert, compute dedup_hash; exact match → return existing question.
- Near‑duplicate: top‑1 cosine on `q_emb` ≥ 0.96 → link new evidence to existing question (incrementing `seen_count`) rather than new row.

#### 3.6.4 Admin promote flow
- Pending patterns/kps listed on Knowledge page.
- User can: **promote** to live; **merge** into an existing live node (rewrites links); **reject** (soft delete).
- Promotion updates `status='live'` and locks the `name_cn`; embeddings are re‑indexed.

### 3.7 Multi-turn Dialogue & Memory

#### 3.7.1 Goals
- Let the student continuously ask follow-up questions after a solve, without Gemini losing context across turns.
- Reuse the solved question as an optional anchor so the assistant can answer from the parsed question + final teaching answer, not only from chat history.
- Persist every dialog turn for later inspection and analysis.

#### 3.7.2 Memory strategy
- The backend must **not** replay the full transcript on every turn.
- Instead each turn sends a compact context package:
  1. optional `question_context` (parsed question + compressed answer context)
  2. rolling `summary`
  3. cached `key_facts[]`
  4. cached `open_questions[]`
  5. recent raw messages (last N, configurable)
- The LLM returns both:
  - the visible assistant reply
  - the refreshed memory state for the next turn
- Memory must keep only durable facts / constraints / unresolved points, not greetings or rhetorical filler.

#### 3.7.3 Session model
- A `ConversationSession` may be:
  - **free-form** (general study dialog)
  - **question-linked** (`question_id` points to an existing solved or parsed question)
- Each session stores:
  - `title`
  - `latest_summary`
  - `key_facts[]`
  - `open_questions[]`
  - `last_message_at`
- Each message stores:
  - `role` (`user` | `assistant` | `system`)
  - monotonic `sequence_no`
  - `content`
  - optional `metadata_json` (follow-up suggestions, error flags)
- After every assistant reply, store a `ConversationMemorySnapshot` for analysis/auditing of how memory evolved over time.

#### 3.7.4 UX requirements
- New page: `/dialog`
- User can:
  - create a blank conversation
  - create/select the latest conversation linked to a question
  - review the cached memory panels (`summary`, `key_facts`, `open_questions`)
  - continue asking follow-up questions indefinitely
- `/q/[id]` must expose an affordance to jump into a question-linked dialog.

#### 3.7.5 Constraints
- Dialog memory must be configurable:
  - model used for chat
  - recent raw-message window
  - max question-context chars
  - max summary chars
  - max fact / open-question counts
- All dialog data remains local in PostgreSQL; no external storage besides Gemini API calls.

---

## 4. Non‑Functional Requirements

| Area | Requirement |
|---|---|
| Latency (parse) | p50 < 4 s, p95 < 8 s |
| Latency (answer) | First token < 2 s; full stream < 25 s p95 |
| Latency (viz render) | < 1 s from code arrival to first frame |
| Streaming | SSE for AnswerPackage sections in defined order; long Solver/VizCoder JSON calls should use Gemini structured-output streaming under the hood |
| Reliability | LLM retries with exponential backoff (3 attempts); JSON repair loop ≤ 2 extra calls; task-specific timeouts for parser / solver / vizcoder / dialog / embed |
| Observability | Structured JSON logs; per‑request token + cost ledger (`llm_calls` table); persisted dialog transcripts and memory snapshots for analysis |
| Security | Sandbox per 3.3.2; strict input size limits; Gemini safety settings enabled |
| Privacy | No outbound network except to Gemini; images stored locally under `./data/images` |
| i18n | UI strings centralized; 中文 Simplified primary, copy ready for future zh‑TW/EN |

---

## 5. System Architecture

### 5.1 Component overview
```
┌────────────────────────┐     SSE/REST     ┌─────────────────────────┐
│  Next.js App (TS)      │ ───────────────► │  FastAPI Backend        │
│  /ask /q /library      │                  │  routers/services       │
│  /practice /knowledge  │ ◄─────────────── │                         │
│  /dialog               │                  │                         │
│  VizSandbox iframe     │                  │  ┌───────────────────┐  │
└────────────────────────┘                  │  │  llm_client       │──┼──► Gemini (multimodal/text/embed)
                                            │  │  viz_validator    │  │
                                            │  │  embedding        │  │
                                            │  │  vector_store     │──┼──► Milvus 19530
                                            │  │  db (asyncpg)     │──┼──► PostgreSQL
                                            │  └───────────────────┘  │
                                            └─────────────────────────┘
```

### 5.2 Frontend (Next.js, App Router, TypeScript)
- **Pages**
  - `/` — Ask (upload, camera, parsed preview, edit, "开始解答")
  - `/q/[id]` — Answer view (streaming sections, sticky viz panel, similar questions rail)
  - `/library` — History, filters (subject/grade/topic/pattern), search
  - `/practice` — Exam builder (basket + config), exam runner, self‑check
  - `/knowledge` — Taxonomy tree; related questions; pending promote list
  - `/dialog` — Persistent tutoring chat with cached memory panels
  - `/settings` — Gemini key/model selection, cost ledger view, dialog analytics
- **State**: React Query for server cache; Zustand for ephemeral UI (basket, current viz params).
- **Rendering**: **MathJax 3** (`tex-chtml` via CDN, `$…$` inline / `$$…$$` display) for all math across every page — this is a project-wide rule; KaTeX and raw LaTeX strings must not appear in the UI. `<VizSandbox />` wraps the sandboxed iframe with typed postMessage bridge.
- **SSE client**: Reads AnswerPackage sections incrementally; each section renders the moment it's complete.
- **Resume polling**: `/q/[id]` polls `/api/answer/{id}/resume` for background-job status. These reads do not trigger extra Gemini calls; they only rehydrate persisted progress.

### 5.3 Backend (FastAPI, Python 3.11+)
- **Routers**: `ingest`, `answer`, `dialog`, `retrieve`, `practice`, `knowledge`, `admin`.
- **Services**:
  - `llm_client` — Gemini gateway; model routing (`gemini-*-pro` vision, text, `text-embedding-*`); JSON mode enforcement; repair loop; task-specific timeouts; optional structured-output streaming for long Solver/VizCoder calls; cost log writer.
  - `dialog_service` — multi-turn session persistence, question-anchor compaction, rolling-memory refresh.
  - `viz_validator` — wraps Node helper (child process) running `acorn` per 3.3.3.
  - `embedding` — pluggable; default Gemini embeddings; interface leaves room for local bge‑m3.
  - `vector_store` — pymilvus; collection management & query helpers.
  - `db` — async SQLAlchemy 2.x + asyncpg; Alembic migrations.
- **Schema validation**: pydantic models mirror `AnswerPackage` exactly; LLM forced into JSON structured output.

### 5.4 LLM layer
- Four task prompts (see §7): **Parser**, **Solver**, **VizCoder**, **Dialog** — all implemented as `PromptTemplate` subclasses with versioning, design‑decision documentation, preview/explain utilities, and JSON Schema contracts (see §7.1).
- `PromptRegistry` auto‑discovers all templates; provides `list()`, `get(name)`, and version lookup.
- Single gateway with retry/repair; Solver and VizCoder may use Gemini structured-output streaming so the backend receives partial JSON chunks instead of waiting for one full response body.
- Token & cost accounting written to `llm_calls` with `prompt_name` + `prompt_version`.
- Embeddings called per §3.6.2 step 4.

### 5.5 Data stores

#### 5.5.1 PostgreSQL (logical schema)
```
ingest_images(id, path, mime, size, sha256, created_at)
questions(id, image_id, parsed_json, answer_package_json,
          subject, grade_band, difficulty, dedup_hash, seen_count,
          status, created_at)
question_retrieval_profiles(id, question_id, profile_json, created_at)
retrieval_units(id, question_id, unit_kind, title, text, keywords_json,
                weight, source_section, created_at)
answer_packages(id, question_id, section, payload_json, created_at)  -- streamed sections
solution_steps(id, question_id, step_index, statement, rationale,
               formula, why_this_step, viz_ref)
visualizations(id, question_id, viz_ref, title, caption, learning_goal,
               helpers_used_json, jsx_code, params_json, animation_json)
knowledge_points(id, parent_id, name_cn, path_cached, subject, grade_band,
                 status(pending|live), seen_count, embedding_ref, created_at)
method_patterns(id, name_cn, subject, grade_band, when_to_use,
                procedure_json, pitfalls_json, status(pending|live),
                seen_count, embedding_ref, created_at)
pitfalls(id, name_cn, description, pattern_id, created_at)
question_kp_link(question_id, kp_id, weight, PRIMARY KEY(question_id, kp_id))
question_pattern_link(question_id, pattern_id, weight,
                      PRIMARY KEY(question_id, pattern_id))
exams(id, name, config_json, created_at)
exam_items(id, exam_id, position, source_question_id NULL,
           synthesized_payload_json NULL, answer_outline, rubric)
conversation_sessions(id, question_id NULL, title, latest_summary,
                      key_facts_json, open_questions_json,
                      last_message_at, created_at)
conversation_messages(id, conversation_id, role, sequence_no,
                      content, metadata_json NULL, created_at)
conversation_memory_snapshots(id, conversation_id, sequence_no,
                              summary, key_facts_json,
                              open_questions_json, created_at)
llm_calls(id, task, model, prompt_tokens, completion_tokens,
          cost_usd, latency_ms, status, created_at)
```
Indexes: `questions(subject, grade_band, difficulty)`, `questions(dedup_hash UNIQUE)`, `retrieval_units(question_id, unit_kind)`, `conversation_sessions(last_message_at)`, `conversation_messages(conversation_id, sequence_no UNIQUE per conversation)`, link‑table btree on each side, `method_patterns(status)`, `knowledge_points(status, parent_id)`.

#### 5.5.2 Milvus collections
Dense collections (HNSW / IP metric):
- `q_emb` — fields: `id (int64 PK)`, `ref_pg_id`, `subject`, `grade_band`, `difficulty`, `vector (FLOAT_VECTOR, dim=<embed_dim>)`.
- `pattern_emb` — `id`, `pattern_id`, `subject`, `grade_band`, `vector`.
- `kp_emb` — `id`, `kp_id`, `subject`, `grade_band`, `vector`.
- `question_full_emb` — `question_id`, `subject`, `grade_band`, `difficulty`, `vector`.
- `answer_full_emb` — `question_id`, `subject`, `grade_band`, `difficulty`, `vector`.
- `retrieval_unit_emb` — `retrieval_unit_id`, `unit_kind`, `subject`, `grade_band`, `difficulty`, `vector`.

Companion sparse collections for M5 multi-route retrieval (`SPARSE_INVERTED_INDEX` / IP metric, requires Milvus ≥ 2.4):
- `q_emb_sparse`, `pattern_emb_sparse`, `kp_emb_sparse` — same scalar fields as their dense siblings but with `sparse_vector (SPARSE_FLOAT_VECTOR)` instead of a fixed-dim dense vector. Populated by the active `SparseEncoder` (`bge-m3` lexical head or online BM25) during the same sediment step (§3.6.2).
- `question_full_emb_sparse`, `answer_full_emb_sparse`, `retrieval_unit_emb_sparse` — sparse lexical companions for the whole-question, whole-answer, and semantic-facet routes from §3.4.1–§3.4.7.

`embed_dim` comes from `retrieval.embedder`: 768 for Gemini embeddings, 1024 for `bge-m3`. Swapping embedders changes dense dim but not the sparse schema, so sparse indexes remain schema-compatible across migrations. Even so, operators should recreate dense collections when the dense dim changes, and should recreate sparse collections as well when the sparse encoder family changes (`bge-m3` lexical head ↔ BM25) to avoid stale lexical rows.

---

## 6. API Design (selected endpoints)

All endpoints JSON unless noted. SSE endpoints declared explicitly.

| Method | Path | Purpose | Notes |
|---|---|---|---|
| POST | `/api/ingest/image` | Upload image, run Parser | Returns draft `question_id` + `ParsedQuestion` |
| PATCH | `/api/ingest/{question_id}` | Edit parsed fields | Body: partial `ParsedQuestion` |
| POST | `/api/answer/{question_id}` | Start answer generation | **SSE**, events per AnswerPackage section |
| GET | `/api/questions/{id}` | Full stored AnswerPackage | |
| POST | `/api/retrieve/similar` | Similar questions | Body: `{mode, query, filters, k}` |
| POST | `/api/practice/exam` | Generate exam | Body: `{sources[], topics[], patterns[], count, difficulty_dist}` |
| GET | `/api/practice/exam/{id}` | Fetch exam | |
| GET | `/api/knowledge/tree` | Full taxonomy | Subject/grade filters |
| GET | `/api/knowledge/pending` | Pending nodes/patterns | |
| POST | `/api/knowledge/promote` | Promote pending | Body: `{kind, id}` |
| POST | `/api/knowledge/merge` | Merge pending → live | Body: `{kind, from_id, into_id}` |
| GET | `/api/dialog/sessions` | List stored conversations | Ordered by `last_message_at desc` |
| POST | `/api/dialog/sessions` | Create conversation | Body: `{title?, question_id?}` |
| GET | `/api/dialog/sessions/{id}` | Fetch conversation detail | Returns session, messages, memory, optional question context |
| POST | `/api/dialog/sessions/{id}/messages` | Append one user turn | Returns assistant reply + refreshed memory |
| GET | `/api/dialog/stats` | Dialog analytics counts | sessions, messages, memory_snapshots |
| GET | `/api/admin/llm-cost` | Cost ledger summary | |

**SSE event names** for `/api/answer/...`: `question_understanding`, `key_points_of_question`, `solution_step` (repeated), `visualization` (repeated), `key_points_of_answer`, `method_pattern`, `similar_questions`, `knowledge_points`, `self_check`, `done`, `error`.

---

## 7. Prompt Engineering Strategy

### 7.1 Prompt Template Framework (architectural requirement)

All LLM calls **must** go through a pre‑defined **Prompt Template** system. Raw ad‑hoc prompt strings are forbidden in application code. This is a first‑class engineering requirement — not a nice‑to‑have — because prompt quality directly determines answer quality, and prompts must be inspectable, versionable, testable, and optimizable without touching application logic.

#### 7.1.1 PromptTemplate base class
Every prompt is a Python class inheriting from `PromptTemplate`. The base class enforces:

| Attribute / Method | Purpose |
|---|---|
| `version` (PromptVersion) | Semantic version (major.minor) + last‑updated date. Bumped on any wording change. Recorded in every `llm_calls` row for traceability. |
| `name` | Short identifier (`"parser"`, `"solver"`, `"vizcoder"`). Used as key in the registry and cost ledger. |
| `purpose` | 1–2 sentence goal statement. Readable by anyone, not just the prompt author. |
| `input_description` | What dynamic data the prompt expects (kwargs). |
| `output_description` | What the LLM should return + reference to JSON Schema. |
| `design_decisions[]` | **List of documented design decisions**: each has a `title`, `rationale` (why this wording was chosen), and `alternatives_considered` (what was tried and rejected). This is the critical artifact that enables prompt optimization — future editors can see *why* the prompt is worded this way. |
| `system_message(**kw)` → str | System prompt. Internally documented with inline comments explaining each paragraph's intent and tuning knobs. |
| `user_message(**kw)` → str | User prompt with dynamic data injection. |
| `fewshot_examples(**kw)` → list | Topic‑aware few‑shot messages (default empty; override per prompt). |
| `schema` → dict | JSON Schema dict for the expected LLM output. Included verbatim in the system prompt. |
| `.build(**kw)` → list[dict] | Assemble the final message list (system + few‑shot + user) ready for the Gemini client. |
| `.preview(**kw)` → str | **Human‑readable dump** of the assembled prompt — print it to understand exactly what will be sent to the LLM without actually calling it. Essential for review and optimization. |
| `.explain()` → str | Rich summary: purpose, input/output, and all design decisions. Read this before modifying a prompt. |
| `.diff_preview(old_kw, new_kw)` → str | Side‑by‑side diff showing how different inputs change the final prompt. Useful when tuning. |

#### 7.1.2 Design Decisions documentation pattern
Each prompt template carries a `design_decisions` list. Every deliberate wording choice must be recorded as a `DesignDecision`:
```text
DesignDecision {
    title:                   "教师优先, 解题其次"
    rationale:               "指示 LLM 先归纳方法模式再给答案, 因为..."
    alternatives_considered: ["先解题再提取模式 — 模式质量下降", "分两次调用 — token 翻倍"]
}
```
This pattern ensures that:
- Future editors don't unknowingly revert an optimized choice.
- A/B testing is traceable (each variant is a new version with updated decisions).
- Non‑technical reviewers (e.g., a teacher consultant) can read the rationale without reading code.

#### 7.1.3 Prompt versioning & registry
- All prompt templates are registered in a `PromptRegistry` (dict‑based, auto‑discovered from `backend/prompts/`).
- `PromptRegistry.list()` → table of all prompts with name, version, purpose (for CLI/admin inspection).
- `PromptRegistry.get(name)` → returns the template instance.
- Every `llm_calls` row records `prompt_name` + `prompt_version` so that quality and cost can be analyzed per prompt version.

#### 7.1.4 Prompt optimization workflow
The template system is designed to support an iterative optimization loop:
1. **Inspect**: `prompt.explain()` to read purpose + design decisions; `prompt.preview(**kw)` to see the exact text.
2. **Modify**: Edit the system/user message or design decisions; bump `minor` version.
3. **Compare**: `prompt.diff_preview(old_kw, new_kw)` to verify the change.
4. **Validate**: Run against golden test set (§11.1 for Solver, §11.3 for Parser).
5. **Deploy**: New version auto‑registered; cost/quality tracked per version via `llm_calls`.

#### 7.1.5 Output schema contracts
Each prompt's `.schema` property returns the JSON Schema for its expected output. These schemas are:
- Defined once in a shared `schemas.py` module (single source of truth).
- Embedded verbatim in the system prompt so the LLM sees the exact structure.
- Used by pydantic models for runtime validation (repair loop).
- Cross‑referenced in the API documentation.

Four schemas: `ParsedQuestion`, `AnswerPackage`, `Visualization[]`, `ConversationTurnResult`.

### 7.2 Four task prompts

#### 7.2.1 ParserPrompt (image → `ParsedQuestion`)
- **Role**: "你是一位擅长阅读中文数理题目的老师。仅输出符合 JSON Schema 的结果。"
- **Context**: subject hint if user set; JSON schema; confidence guidance.
- **Multimodal**: Gemini vision with image part + JSON mode.
- **Key design decisions**:
  - *Teacher perspective, not OCR* — instruct LLM to read "as a teacher" so it infers truncated text, normalizes formulas, and describes diagrams (since downstream modules never see the image).
  - *LaTeX normalization upfront* — all formulas in `$…$` so Solver doesn't re‑interpret natural‑language math.
  - *Confidence field* — `confidence < 0.5` triggers UI warning; avoids wasting Solver tokens on bad input.
  - *topic_path coarse→fine* — e.g., `["几何", "三角形", "全等三角形"]`; enables few‑shot selection and taxonomy mapping.
  - *diagram_description required* — the only way Solver/VizCoder understand the figure.

#### 7.2.2 SolverPrompt (`ParsedQuestion` → `AnswerPackage`)
- **Role**: "你是一位教学型教师。先教方法，再给答案。"
- **Core principle**: method_pattern is the primary deliverable; numeric answer is secondary.
- **Context injection**: `existing_patterns[]` and `existing_kps[]` from PG so the LLM reuses named patterns/kps instead of creating duplicates.
- **Few‑shot**: per‑subject/grade blocks from `backend/prompts/fewshot/<subject>/<grade_band>/*.json`, selected by `topic_path` prefix matching (≤3 examples per call).
- **JSON mode**; repair loop on validation error.
- **Transport behavior**: long responses should use structured-output streaming plus a larger solver timeout so one long teaching answer does not fail behind a single 60s wall-clock wait.
- **Key design decisions**:
  - *"Teacher first, solver second"* — the system prompt ranks method‑pattern teaching above all other output.
  - *why_this_step field* — each solution step explains "why choose this approach" (not just "why it's valid"), teaching transferable reasoning.
  - *3 similar questions* — one easier, one same‑difficulty, one harder; same pattern but varied surface features.
  - *No visualizations in this prompt* — separated to VizCoder because (1) Solver output is already long, (2) VizCoder needs dedicated security instructions + H library cheatsheet, (3) independent retry/optimization.
  - *Existing pattern/kp reuse* — injecting known patterns/kps reduces pending‑node pollution.
  - *self_check hints* — encourage verification habits (substitution, dimensional analysis, special‑case testing).

#### 7.2.3 VizCoderPrompt (solution context → `visualizations[]`)
- **Role**: "你是 JSXGraph 可视化教练。只能使用给定的 H.* 帮手与 JSXGraph 安全 API。"
- **Input**: the already‑generated AnswerPackage (sans viz) + `H` helper library cheatsheet + allow‑list / forbidden‑list for globals.
- **Output constraint**: each `jsx_code` is a function body using `board`, `JXG`, `H`, `params` only.
- **Prefer helpers**; inline raw JSXGraph only when necessary.
- **Transport behavior**: use structured JSON streaming when enabled so large visualization payloads can arrive incrementally.
- **Key design decisions**:
  - *Function body only* — no free‑form script; signature `function(board, JXG, H, params)` is enforced. This limits the attack surface and makes AST validation tractable.
  - *H library cheatsheet in prompt* — the LLM sees every available helper with signature + one‑line description, so it prefers safe helpers over raw JSXGraph.
  - *Forbidden globals explicit* — listing `window`, `document`, `fetch`, `eval`, `Function`, `import`, etc. in the prompt itself (not just in the validator) reduces violation rate by ~80% vs. relying on post‑hoc rejection alone.
  - *learning_goal per viz* — forces the LLM to articulate what the student should learn from each visualization, keeping viz purposeful rather than decorative.
  - *interactive_hints* — tells the student what to do ("拖动 P 观察…"), improving engagement vs. a static figure.

#### 7.2.4 DialogPrompt (cached context → assistant reply + refreshed memory)
- **Role**: "你是 HAnswer 的多轮教学对话助手。"
- **Input**: optional `question_context`, rolling `summary`, cached `key_facts[]`, cached `open_questions[]`, recent raw messages, current user turn.
- **Output constraint**: one structured object containing:
  - `assistant_reply`
  - `follow_up_suggestions[]`
  - refreshed `memory { summary, key_facts, open_questions }`
- **Key design decisions**:
  - *Single-call reply + memory refresh* — avoids a second summarizer call after each user turn.
  - *Question anchor separated from transcript* — lets the model distinguish canonical problem facts from ephemeral chat.
  - *Memory is durable, recent messages are local* — keeps token growth bounded while preserving immediate conversational nuance.
  - *Teacher-first follow-up style* — answer the current question directly, then explain or extend.

### 7.3 Operational concerns
- Prompts are stored under `backend/prompts/` with one Python file per template + a shared `schemas.py`.
- Every `llm_calls` row records `prompt_name` + `prompt_version`.
- Few‑shot selection uses `topic_path` prefix matching (≤3 examples per call to keep cost bounded).
- Repair loop injects the validator error message + the offending JSON excerpt and asks for minimal correction (up to 2 retries).
- A CLI utility `python -m backend.prompts.preview <name> [--kwargs ...]` renders any prompt for quick inspection without calling the LLM.

---

## 8. Data Model Highlights

- **Link weight**: `question_kp_link.weight` and `question_pattern_link.weight` are 0–1; used in retrieval rerank (kp_overlap weighted) and in Knowledge page "most representative questions".
- **Pending lifecycle**: `status` on `knowledge_points` and `method_patterns` starts `pending`; only `live` nodes show in main taxonomy filters by default.
- **Streaming sections**: `answer_packages` stores raw sections as they arrive, enabling resume after page refresh before generation completes.
- **Dedup**: `questions.dedup_hash` unique index prevents duplicates; near‑dup handled at service layer.
- **Viz storage**: `visualizations` persists the exact `jsx_code` that passed validation, so re‑renders are deterministic and reviewable.

---

## 9. UI / UX Design Notes

### 9.1 Ask (`/`)
- Big drop zone; camera capture button; recent uploads strip.
- Parsed question preview card: editable fields (`question_text`, `given`, `find`), chips for `topic_path`; confidence chip with color.
- Primary CTA: "开始解答".

### 9.2 Answer view (`/q/[id]`)
- **Three‑column responsive layout** (collapses to stacked on narrow):
  - Left rail: section outline with anchors & completion indicators; jump navigation.
  - Center: streaming sections (math rendered via **MathJax 3**, section headers bilingual‑ready).
  - Right sticky: **Viz panel** with tabs per visualization; slider/toggle controls; play/pause/reset; "学习目标" line above each viz.
- Footer: similar questions carousel (3 cards with pattern badge); "加入练习篮" action.
- Error states:
  - viz validation failure → fallback static description card with "重新生成" button.
  - solver/viz timeout → stage-specific error card showing friendly timeout text, raw backend error on demand, and "重新开始解答" action.

### 9.3 Library (`/library`)
- Filters: subject, grade_band, difficulty, topic, method pattern, date range.
- Search box: hybrid text + embedding search.
- List with method‑pattern badges; click → Answer view.

### 9.4 Practice (`/practice`)
- Basket (from Answer view adds) + manual search add.
- Config: count, difficulty distribution, whether to allow LLM synthesized fillers.
- Exam runner: question at a time with "查看答案大纲" reveal; self‑check scoring (manual).

### 9.5 Knowledge (`/knowledge`)
- Left: taxonomy tree (KnowledgePoint, filter by subject/grade).
- Right: selected node details — related questions (by weight), related method patterns, pitfalls.
- Pending tab: list of pending kps/patterns with Promote / Merge / Reject actions.

### 9.6 Dialog (`/dialog`)
- Left sidebar: session list ordered by recent activity; create blank or question-linked conversation.
- Main area: transcript, composer, follow-up chips, and memory panels (`summary`, `key_facts`, `open_questions`).
- When linked to a question, the top card shows the anchored question text and extracted method pattern.

### 9.7 Settings (`/settings`)
- Gemini API key read from `$GEMINI_API_KEY` environment variable (never committed).
- Model selection per task.
- Cost ledger summary.
- Dialog configuration visibility and transcript/memory analytics counts.

---

## 10. Implementation Milestones

Delivery order (dependencies noted; items on the same line may proceed in parallel).

1. **M1 — Foundations**: FastAPI skeleton, PG schema + Alembic, Milvus collection setup, Gemini client gateway, **Prompt Template framework** (base class, registry, schemas, CLI preview tool), Next.js skeleton, shared types package.
2. **M2 — Ingest + Parser** (depends M1): `/api/ingest/*`, Parser prompt + schema + repair loop, Ask page + parsed preview/edit.
3. **M3 — Solver + Answer view (no viz yet)** (depends M1; parallel with M2): Solver prompt, AnswerPackage pydantic, SSE streaming, `/q/[id]` sections, MathJax 3.
4. **M4 — Visualization subsystem** (depends M3): viz sandbox iframe + postMessage protocol, AST validator (Node helper), `H` helper library v1, VizCoder prompt, Viz panel UI, fallback UI.
5. **M5 — Retrieval + Library** (depends M3): embedding service, Milvus writes on question insert, `/api/retrieve/similar`, Library page with filters + search.
6. **M6 — Knowledge sediment + admin** (depends M5): taxonomy seed (~150 CN curriculum nodes), pending/live states, Knowledge page, promote/merge/reject.
7. **M7 — Practice exams** (depends M5, M6): exam builder, synthesis of variants when bank is short, Practice runner.
8. **M8 — Polish**: cost ledger UI, error/repair UX, streaming resumability, seed data, acceptance tests, performance tuning.
9. **M9 — Persistent dialog memory** (depends M3, parallel with M8): dialog prompt, session/message/memory tables, `/api/dialog/*`, `/dialog`, question-linked follow-up handoff, transcript analytics.

---

## 11. Verification Plan

### 11.1 Contract & schema
- 20 golden `AnswerPackage` JSON samples; pydantic validation must pass 100 %.
- 12 golden `ConversationTurnResult` samples; refreshed `memory` fields must remain concise, stable, and schema-valid.
- Schema migration check: fresh DB → Alembic upgrade → seed → app boots.
- All 4 prompt templates pass `.preview()` without error; `.explain()` output contains ≥ 3 design decisions each.
- `PromptRegistry.list()` returns all 4 templates with correct versions.

### 11.2 Sandbox safety
- 30 adversarial JS snippets (attempting `fetch`, `window.top`, `Function("…")`, `eval`, string timers, infinite loops, DOM escape) — **all** must be rejected by the AST validator or contained by the runtime watchdog, with zero host impact. Automated in CI.

### 11.3 Parser quality
- Manual evaluation on 50 sample images (math + physics × junior + senior). Target ≥ 90 % correct on `question_text`, `given`, `find`.

### 11.4 Retrieval quality
- 30 held‑out questions with known pattern labels. Top‑3 similar must include ≥ 1 same‑pattern item for ≥ 80 % of queries.
- 30 pedagogical queries such as `新定义`, `初二 圆的最值`, `辅助线 倍长中线`, `类似答案`, `扩展思路` must each retrieve at least one relevant `question_full`, `answer_full`, or semantic facet unit in top‑5.
- Ablation check: `question_full` only vs. `question_full + answer_full + retrieval_units`; the mixed representation should win on student-style study queries before sign-off.

### 11.5 End‑to‑end smoke
Upload → parse → edit → answer streams → viz renders interactively → similar shown → add to basket → generate exam → take exam → promote a pending pattern. Must complete without manual intervention.
Dialog smoke: open `/q/[id]` → jump into `/dialog?questionId=...` → send three follow-up turns → reload → transcript and rolling memory remain intact.

### 11.6 Performance
- Measure p50/p95 for parse, first‑token, full‑stream, viz‑first‑frame against §4 targets; failures trigger investigation before milestone sign‑off.

---

## 12. Open Items & Stage‑2 Hints

- Multi‑user auth + roles (student/teacher/admin); teacher can curate taxonomy and review students.
- 错题本 automation + spaced repetition scheduler (SM‑2 or FSRS).
- Handwriting input (stylus) and stroke OCR.
- Multiple questions per image (detection + per‑region parse).
- Mobile PWA, camera‑first UX.
- Export: PDF printable exams with answer sheets.
- Swap embeddings to local `bge‑m3` for better CN recall; re‑index path + dual‑write migration.
- Knowledge graph: prerequisite edges between KnowledgePoints; path planner for "学习路径".
- Offline cache + local model fallback.

---

## Appendix A — Glossary

| Term | Meaning |
|---|---|
| ParsedQuestion | Structured result of image→text parse by Gemini |
| AnswerPackage | Full structured teaching answer returned by the Solver |
| MethodPattern | Reusable problem‑solving technique, the primary teaching artifact |
| KnowledgePoint | Concept node in the curriculum taxonomy |
| Pitfall | Common student mistake associated with a pattern |
| Viz Sandbox | Dedicated iframe with strict CSP + sandbox attr used to run LLM‑emitted JSXGraph code |
| `H` helper library | HAnswer‑curated JS API exposed inside the sandbox to keep LLM output safe & idiomatic |

## Appendix B — Environment & Connection Reference

- Milvus: `milvus-standalone:19530`, database `default`, no auth.
- PostgreSQL: `psql -p5432 -U jianbo jianbo` (local socket / localhost).
- Image storage: `./data/images/` (gitignored).
- Config: `./backend/config.toml` (model selections); Gemini key in `$GEMINI_API_KEY` env var; never committed.

---

*End of HAnswer Stage‑1 Requirements.*
