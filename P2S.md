# P2S — Project Problems to Solutions

## Backend (PostgreSQL + SQLAlchemy ORM + Milvus)

| # | Sev | Problem | File | Status |
|---|-----|---------|------|--------|
| B1 | High | Test fixture bypassed by services (`dialog_service`, `answer_job_service`, `cost_ledger`) that create their own `session_scope()` — writes leak past SAVEPOINT sandbox | `tests/conftest.py` | ⚠️ Documented |
| B2 | Medium | In-memory job state (`_tasks`, `_states` dicts) lost on process restart — running jobs disappear, questions stuck in intermediate status with no recovery | `services/answer_job_service.py:77-78` | Open |
| B3 | Medium | Cost estimation returned $0 for all used models — `_COST_PER_1K` only had `gemini-2.0-flash` (unused), missing `gemini-3.1-pro-preview` | `services/llm_client.py:123-127` | ✅ Fixed |
| B4 | Medium | `get_session()` FastAPI dependency yielded session without commit/rollback — any use as `Depends` silently lost writes | `db/session.py:38-41` | ✅ Fixed |
| B5 | Medium | `list_questions` fetches max 500 rows then applies 6 filters in Python — matching rows beyond the window are invisible, no pagination | `routers/retrieve.py:119-237` | Open |
| B6 | Medium | `_structural_route` full table scan with no WHERE clause — loaded entire dataset into Python, filtered in a loop | `services/retrieval_service.py` | ✅ Fixed |
| B7 | Medium | SSE stream holds a single uncommitted transaction for the entire solve duration (potentially minutes) | `routers/answer.py` | Open |
| B8 | Low | Dialog sequence number race — `MAX(sequence_no)+1` across separate session scopes; concurrent messages could get duplicate numbers | `services/dialog_service.py:253-260` | Open |
| B9 | Low | `append_message` splits across 3 session scopes — crash between user msg commit and assistant msg commit orphans a user message | `services/dialog_service.py:240-420` | Open |
| B10 | Low | BM25 corpus statistics reset to zero on restart, degenerating to pure TF scoring | `services/sparse_encoder.py:84-92` | Open |
| B11 | Low | Redundant `AnswerPackageSection` import (imported twice, once aliased) | `services/answer_job_service.py:20` | ✅ Fixed |
| B12 | Low | `_session()` generators annotated as `-> AsyncSession` instead of `-> AsyncIterator[AsyncSession]` | Multiple routers | Open |
| B13 | Low | Mixed session patterns — some services own their sessions, others receive them — transaction boundaries unclear | Cross-service | Open |
| B14 | Low | ~160 lines business logic (filtering, faceting, sorting) inline in router | `routers/retrieve.py` | Open |

## Frontend (Next.js + React)

| # | Sev | Problem | File | Status |
|---|-----|---------|------|--------|
| F1 | High | React 18 + Next.js 16 — Next.js 16 requires React 19; build/runtime breaks | `package.json:13-15` | ✅ Fixed |
| F2 | High | ESLint flat config (`eslint.config.mjs`) used with ESLint 8 which doesn't support it — lint broken | `eslint.config.mjs`, `package.json:21` | ✅ Fixed |
| F3 | Medium | Race condition: auto-start `useEffect` fired duplicate `POST /start` due to dependency cascade | `app/q/[id]/page.tsx:129-149` | ✅ Fixed |
| F4 | Medium | Polling `setInterval(1500ms)` fired regardless of previous `loadResume()` completion — overlapping requests | `app/q/[id]/page.tsx:151-155` | ✅ Fixed |
| F5 | Medium | No `loading.tsx` or `error.tsx` in any route — unhandled render error crashes entire app | `app/` (all routes) | Open |
| F6 | Medium | Library listing fetches and renders all items with no pagination or virtualization | `app/library/page.tsx:86-93` | Open |
| F7 | Low | Double `loadResume()` on mount — two useEffects both fire GET /resume before auto-start | `app/q/[id]/page.tsx:120-149` | Open |
| F8 | Low | Missing `useEffect` dependencies — `createSession`/`loadSession` not in dep array or memoized | `app/dialog/page.tsx:160-192` | Open |
| F9 | Low | `localStorage` parsed with `JSON.parse` without `Array.isArray()` check — corrupted storage silently wrong | `app/q/[id]/page.tsx:158`, `app/practice/page.tsx:40` | Open |
| F10 | Low | No `AbortController` on `fetch()` calls — orphaned requests on rapid navigation | Multiple pages | Open |
| F11 | Low | VizSandbox cleanup accesses potentially-null `iframeRef.current` — dispose message silently lost | `components/VizSandbox.tsx:51-53` | Open |
| F12 | Low | MathJax (~300KB) loaded via root layout on every page including non-math pages | `app/layout.tsx:33-55` | Open |
| F13 | Low | Unused dependencies: `@tanstack/react-query`, `katex`, `zustand` — never imported | `package.json` | ✅ Fixed |
| F14 | Low | `initial` state typed as `any` — defeats TypeScript safety throughout the component | `app/q/[id]/page.tsx:74` | Open |

## Config / Infrastructure

| # | Sev | Problem | File | Status |
|---|-----|---------|------|--------|
| C1 | Medium | Duplicate pytest config in both `pytest.ini` and `pyproject.toml` — `pytest.ini` silently wins | `pytest.ini`, `pyproject.toml:43-46` | ✅ Fixed |
| C2 | Medium | `compare_type=True` missing in Alembic `env.py` — column type changes not detected by autogenerate | `migrations/env.py` | ✅ Fixed |
| C3 | Medium | `data/images/` has no `.gitkeep` (missing on fresh clone), `data/samples/` missing `q1.jpg` | `data/` | ✅ Partial (.gitkeep added) |
| C4 | Low | `config.toml` missing `[dialog]` section — silently falls back to code defaults | `config.toml` | Open |
| C5 | Low | README documents `max_retries = 3` but code/config defaults to `2` | `README.md`, `config.py:64` | ✅ Fixed |
| C6 | Low | `tomli` dependency has dead environment marker (`python_version<'3.11'`) that never activates | `pyproject.toml:21` | ✅ Fixed |
