# CLAUDE.md

This file is the primary instruction file for Claude Code when working in this repository.

Use it as the fast operational guide. For full product and system detail, follow the linked project documents instead of duplicating them here.

## 1. What this repository is

HAnswer is a local-first learning companion for Chinese middle/high-school math and physics students.

Core flow:

1. Parse a photographed problem into `ParsedQuestion`
2. Generate a teaching-first `AnswerPackage`
3. Generate visualization specs and JSXGraph code
4. Sediment the solved question into the retrieval / knowledge system

Primary stack:

- Backend: FastAPI + async SQLAlchemy + asyncpg + Alembic + Pydantic v2
- Frontend: Next.js App Router + React + TypeScript
- Database: PostgreSQL
- Vector store: Milvus dense + sparse collections
- LLM provider: Google Gemini
- Visualization runtime: sandboxed JSXGraph, AST-validated by Node/acorn

Start with these docs when you need more context:

- `README.md`: current product overview, commands, API surface, architecture snapshot, troubleshooting
- `HAnswerR.md`: detailed spec / system requirements
- `P2S.md`: known issues and bug backlog
- `Unfinished.md`: audit log and remaining gaps
- `AGENTS.md`: repository guidelines for structure, testing, and security

## 2. How Claude Code should behave here

These rules are merged from `AClaude.md` and adapted for this repository.

### Think before coding

- Do not assume unclear intent.
- State assumptions explicitly when they matter to implementation.
- If multiple interpretations are possible, surface them instead of silently choosing one.
- Prefer one clear local hypothesis and validate it quickly.

### Simplicity first

- Write the minimum code that solves the request.
- Do not add speculative abstractions, configuration, or future-proofing.
- Do not widen scope unless the current approach is blocked.
- Match the existing style and architecture of the touched module.

### Surgical edits only

- Touch only the files and lines required for the task.
- Do not refactor unrelated nearby code.
- Remove only the imports / variables / helpers made unnecessary by your own change.
- If you notice unrelated issues, mention them separately instead of silently changing them.

### Goal-driven execution

- Convert requests into verifiable outcomes.
- Prefer tests or narrow validation commands over reasoning-only confidence.
- After the first substantive edit, run the narrowest relevant validation immediately.
- Keep iterating in the same slice until the validation is green or the hypothesis is disproven.

## 3. Working style for this repo

### Change strategy

- Start from the most concrete anchor available: file, symbol, failing test, failing endpoint, or visible behavior.
- For backend bugs, prefer the owning service or router, not broad repo exploration.
- For frontend bugs, prefer the route component or the specific sandbox/runtime component involved.
- For prompt / schema / stage-flow issues, inspect the prompt template, the schema, and the orchestrating service together.

### Validation expectations

- Backend changes: run focused `pytest` targets first; use full `pytest` only if needed.
- Frontend changes: run `npm run lint` or `npm run typecheck` for the touched slice when applicable.
- Prompt / visualization changes: prefer targeted tests under `backend/tests/` for prompts, validators, and stage integration.
- Documentation-only changes usually do not require tests.

### Editing constraints

- Preserve public API shape unless the task explicitly requires changing it.
- Do not commit secrets, generated local data, or `backend/config.toml`.
- Settings are file-driven; the Settings page is read-only and does not edit runtime config.

## 4. High-value repository facts

### Important backend areas

- `backend/app/routers/`: HTTP surface for ingest, answer, dialog, retrieve, practice, knowledge, admin
- `backend/app/services/answer_job_service.py`: background 4-stage pipeline with review / confirm / rerun
- `backend/app/services/solver_service.py`: solver generation and incremental section persistence
- `backend/app/services/visualization_spec_service.py`: HAVizNew Stage 1 visualization spec generation
- `backend/app/services/jsxgraph_codegen_service.py`: HAVizNew Stage 2 JSXGraph code generation
- `backend/app/services/sediment_service.py`: taxonomy resolution, dedup, indexing, vector upsert
- `backend/app/services/retrieval_service.py`: multi-route retrieval with RRF fusion
- `backend/app/services/dialog_service.py`: multi-turn tutoring sessions with rolling memory
- `backend/app/prompts/`: all LLM prompts go through versioned `PromptTemplate` subclasses
- `backend/viz_validator/validate.mjs`: AST validator for generated JSXGraph code

### Important frontend areas

- `frontend/app/page.tsx`: Ask flow
- `frontend/app/q/[id]/page.tsx`: answer page, review flow, visualization panel, polling `/resume`
- `frontend/components/VizSandbox.tsx`: visualization dispatcher
- `frontend/components/JsxgraphSandbox.tsx`: JSXGraph iframe host
- `frontend/public/viz/sandbox.html`: CSP-locked visualization runtime

### Data model landmarks

- Questions / answers: `questions`, `question_solutions`, `answer_packages`, `question_stage_reviews`
- Retrieval: `question_retrieval_profiles`, `retrieval_units`, `solution_steps`
- Visualization: `visualizations`
- Knowledge: `knowledge_points`, `method_patterns`, `pitfalls`, link tables
- Dialog: `conversation_sessions`, `conversation_messages`, `conversation_memory_snapshots`
- Tracking: `llm_calls`, `ingest_images`

## 5. Current visualization architecture

The active visualization path is the HAVizNew two-stage flow:

1. Stage 1: generate `VisualizationSpecBundle`
2. Validate with Pydantic / JSON Schema
3. Select one recommended visualization
4. Stage 2: generate `function renderVisualization(containerId, spec)` JSXGraph code
5. Validate generated code with the Node AST validator
6. Execute inside the sandboxed frontend runtime

Important implementation detail:

- Stage 2 generated code must follow the `renderVisualization(containerId, spec)` contract.
- Do not assume direct DOM access inside generated code; runtime and validator constraints are stricter than a normal browser page.

## 6. Commands Claude Code should prefer

### Infrastructure

```bash
docker compose up -d
docker compose ps
```

### Backend setup / run

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

### Frontend

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

## 7. Conventions Claude Code must preserve

- Python: snake_case, 4-space indentation, Ruff-driven style, target 3.11+
- Frontend: PascalCase components, lowercase route folders
- Keep code comments sparse and only where they clarify non-obvious behavior
- No ad-hoc prompt strings in app code; use the prompt framework
- Tests use real local PostgreSQL with SAVEPOINT rollback, not mocks as the default system behavior

## 8. Known operational constraints

- `backend/config.toml` is git-ignored and must not be committed
- Gemini API key must come from `$GEMINI_API_KEY`
- Node is required for visualization validator execution
- Frontend proxies `/api/*` to backend on port `8787`
- Long-running answer generation is background-job driven; the UI mainly uses `/api/answer/{id}/start` and `/resume`

## 9. Current known issue areas

Use `P2S.md` as the source of truth. Historically important open areas include:

- in-memory job state loss across process restart
- pagination / list scaling gaps in some routes
- frontend loading / virtualization gaps
- long-running pipeline edge cases around retries and review flow

Do not assume a surface is broken just because it appears in `P2S.md`; confirm locally before changing it.

## 10. Preferred way to use the docs

- Read `CLAUDE.md` first for working rules and repository map
- Use `README.md` for commands, architecture snapshot, API routes, and troubleshooting
- Use `HAnswerR.md` for feature / product requirements
- Use `HAViz.md` and related visualization docs when touching the visualization pipeline
- Use `Unfinished.md` and `P2S.md` to understand known gaps before proposing large changes

## 11. Definition of a good Claude Code change here

A good change in this repository is:

- localized
- testable
- consistent with the current stage-based architecture
- respectful of the prompt-template and review-flow design
- explicit about assumptions
- validated with the narrowest relevant command or test

If a task is ambiguous, clarify it before making broad changes. If it is concrete, implement directly and verify.
