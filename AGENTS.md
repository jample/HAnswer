# Repository Guidelines

## Project Structure & Module Organization
`backend/` contains the FastAPI app, Alembic migrations, prompt templates, and pytest suites. Core code lives under `backend/app/` with `routers/`, `services/`, `db/`, `schemas/`, and `prompts/`. `frontend/` is a Next.js 14 App Router app; routes live in `frontend/app/`, shared UI in `frontend/components/`, and visualization assets in `frontend/public/viz/`. `backend/viz_validator/` holds the Node-based AST validator. Reference docs and sample data live in `README.md`, `HAnswerR.md`, and `data/`.

## Build, Test, and Development Commands
Use the repo as a local-first stack:

- `docker compose up -d`: start Milvus, MinIO, etcd, and Attu.
- `cd backend && cp config.example.toml config.toml && alembic upgrade head`: initialize backend config and schema.
- `cd backend && pip install -e '.[dev]' && uvicorn app.main:app --reload --port 8787`: run the API locally.
- `cd backend && python -m scripts.seed_knowledge`: seed baseline knowledge points.
- `cd backend && pytest`: run backend tests.
- `cd backend && ruff check .`: run Python lint checks.
- `cd frontend && npm install && npm run dev`: run the Next.js app on `:3333`.
- `cd frontend && npm run build && npm run lint && npm run typecheck`: validate production build, linting, and TS types.

## Coding Style & Naming Conventions
Python targets 3.11+, uses 4-space indentation, and follows Ruff settings in `backend/pyproject.toml` with a 100-character line length. Keep backend modules snake_case and grouped by domain (`services/retrieval_service.py`, `routers/knowledge.py`). Frontend code is TypeScript/React; keep component files in PascalCase (`VizSandbox.tsx`) and route folders lowercase per Next.js conventions.

## Testing Guidelines
Backend tests use `pytest` and `pytest-asyncio`. Place tests in `backend/tests/` and name them `test_<feature>.py`. The suite runs against a real local PostgreSQL database with SAVEPOINT rollback, so run `alembic upgrade head` first and keep tests isolated. Add or update tests for router, service, and prompt-path changes.

## Commit & Pull Request Guidelines
Git history is not available in this workspace, so no repository-specific commit pattern could be verified. Use short, imperative commit subjects with an optional scope, such as `backend: tighten ingest validation`. PRs should describe the user-visible change, list config or migration impacts, link related issues, and include screenshots for UI changes.

## Security & Configuration Tips
Do not commit `backend/config.toml`, API keys, or generated local data. Keep Gemini credentials local, and use `frontend/next.config.js` rewrites/CSP settings as the source of truth for API proxying and sandbox policy.
