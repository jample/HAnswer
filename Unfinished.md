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

- Task: true incremental section streaming instead of bulk-then-stream replay.
  Related requirements: `§3.2`, `§4`, `§10 M8`.
  Current status: `backend/app/services/solver_service.py` still performs one full LLM call and only then emits section SSE events. This preserves event order but does not satisfy the intended progressive first-section delivery behavior.

- Task: complete Ask-page UX (`camera` capture button and recent uploads strip).
  Related requirements: `§5.2`, `§9.1`.
  Current status: `frontend/app/page.tsx` supports file upload and parsed editing, but does not implement camera capture or recent-upload history.

- Task: finish Library filters for topic, method pattern, and date range.
  Related requirements: `§9.3`.
  Current status: `frontend/app/library/page.tsx` and `GET /api/questions` only expose subject / grade / difficulty list filters plus free-text retrieval.

- Task: expose pattern-detail browsing with pitfalls in the frontend.
  Related requirements: `§9.5`, `§6`.
  Current status: `GET /api/knowledge/pattern/{id}/detail` exists, but `frontend/app/knowledge/page.tsx` only drives a knowledge-point detail panel and does not surface pattern-centric detail/pitfall browsing.

- Task: allow manual search-add into the practice basket.
  Related requirements: `§9.4`.
  Current status: `frontend/app/practice/page.tsx` only supports localStorage basket items added elsewhere; it does not provide manual search/add inside the practice page.

- Task: support model-selection editing from Settings UI, or explicitly downgrade that requirement.
  Related requirements: `§5.2`, `§9.6`.
  Current status: `/settings` is read-only and intentionally file-backed. This is safe, but it does not match the specification wording that mentions model selection on that page.

- Task: align frontend state architecture with the spec (`React Query` / `Zustand`) or relax the requirement.
  Related requirements: `§5.2`.
  Current status: dependencies are installed, but the implemented pages still use direct `fetch` plus local React state instead of a shared cache/store architecture.

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
