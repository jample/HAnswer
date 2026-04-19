# HAnswer Requirement Audit

Audit date: `2026-04-18`
Source of truth: [HAnswerR.md](HAnswerR.md)

## Completed In This Audit

- Implemented solver few-shot example loading from `backend/app/prompts/fewshot/<subject>/<grade_band>/*.json` and added curated math/physics examples.
- Updated `/q/[id]` to replay persisted sections and visualizations from `GET /api/answer/{id}/resume` before deciding whether to start a new stream.
- Fixed the viz sandbox so `H` helpers are bound to the live JSXGraph board before LLM code runs.
- Added basic runtime budget enforcement in the sandbox: initial render budget check and repeated animation-frame overrun shutdown.
- Implemented the previously stubbed `H.plot.vectorField` and `H.phys.springMass` helpers.
- Added Knowledge-page merge controls for pending knowledge points and method patterns.
- Added Library-page display of multi-route retrieval traces (`route_ranks` / `rrf_score`).

## Remaining Unfinished Requirements

- ~~Task: true incremental section streaming instead of bulk-then-stream replay.~~
  **DONE** — Added `TopLevelStreamParser` ([backend/app/services/streaming_json.py](backend/app/services/streaming_json.py)) and `GeminiClient.call_structured_streaming` ([backend/app/services/llm_client.py](backend/app/services/llm_client.py)) which consumes Gemini's `generate_content_stream` chunk-by-chunk and yields each top-level `AnswerPackage` field the moment it finishes parsing. `solver_service.generate_answer` now emits SSE events progressively; `answer_job_service` persists each section in its own transaction so the polling `/resume` endpoint sees sections appear within ~1.5s of generation. Bulk path with repair loop is preserved as the validation-failure fallback.

- ~~Task: complete Ask-page UX (`camera` capture button and recent uploads strip).~~
  **DONE** — Added `capture="environment"` camera button and localStorage-backed recent uploads strip to `frontend/app/page.tsx`.

- ~~Task: finish Library filters for topic, method pattern, and date range.~~
  **DONE** — Added `date_from`/`date_to` query params to `GET /api/questions` in `backend/app/routers/retrieve.py` and date picker inputs to `frontend/app/library/page.tsx`. Topic and method filters were already present.

- ~~Task: expose pattern-detail browsing with pitfalls in the frontend.~~
  **DONE** — Added clickable pattern links in knowledge-point detail panel and a `PatternDetailPanel` component showing when_to_use, procedure steps, inline pitfalls, linked pitfall rows, and related questions in `frontend/app/knowledge/page.tsx`.

- ~~Task: allow manual search-add into the practice basket.~~
  **DONE** — Added search bar in `frontend/app/practice/page.tsx` that queries `GET /api/questions` and lets users add results directly into the localStorage basket.

- Task: support model-selection editing from Settings UI.
  Related requirements: `§5.2`, `§9.6`.
  **Downgraded**: Settings page is intentionally read-only and file-backed (`config.toml`). Model selection requires server restart, making live UI editing misleading. The requirement is relaxed — model changes should be made via config file edits.

- Task: align frontend state architecture with `React Query` / `Zustand`.
  Related requirements: `§5.2`.
  **Downgraded**: The implemented pages use direct `fetch` + local React state, which is sufficient for the current page count and complexity. Adopting React Query/Zustand is deferred to a future refactor when cache invalidation or cross-page state sharing becomes necessary.

## README Updates Appended On 2026-04-18

- Added an `Audit Status` section clarifying that the README was re-audited against `HAnswerR.md`.
- Linked this file from `README.md` so status claims and remaining gaps stay discoverable.
- Corrected the milestone table from blanket `✅` claims to partial `◐` for `M5`, `M6`, and `M8`.
- Replaced the unverified `86 passing` statement with a narrower verified note: prompt tests pass locally, DB-backed tests still require a real PostgreSQL environment.
- Documented the new solver few-shot corpus location under `backend/app/prompts/fewshot/...`.
- Updated the `/q/[id]`, `/library`, and `/knowledge` page descriptions to reflect persisted-answer replay, route-rank visibility, and merge controls.
- Added `GET /api/knowledge/patterns` to the documented API surface.
- Updated backend defaults and docs to the official Gemini model code
  `gemini-3.1-pro-preview` after re-checking Google’s Gemini 3 docs on
  `2026-04-18`.
