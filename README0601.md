# HAnswer Local Setup Guide 0601

This guide focuses on the operational setup for this repo: Milvus and related
services, backend and frontend startup, OpenAI-compatible configuration, and
the visualization engine.

## Repository Layout

- `backend/`: FastAPI app, Alembic migrations, services, prompts, tests, and
  the Node visualization validator.
- `frontend/`: Next.js app on port `3333`, with visualization sandbox assets
  under `frontend/public/viz/`.
- `docker-compose.yml`: local Milvus stack: etcd, MinIO, Milvus standalone,
  and Attu.
- `backend/config.example.toml`: copy this to `backend/config.toml` and edit
  local settings.

## Prerequisites

- Python 3.11+
- Node.js 18+
- Docker Desktop or another Docker Compose-compatible runtime
- PostgreSQL 13+ reachable from the backend
- API access for whichever LLM/embedding provider you configure

## Configuration File Loading

Backend config is loaded by `backend/app/config.py` in this order:

1. `HANSWER_CONFIG`, if the environment variable points to a file
2. `backend/config.toml`
3. `backend/config.example.toml`

Create your local config:

```bash
cd backend
cp config.example.toml config.toml
```

Do not put API keys in `config.toml`. The app intentionally reads secrets and
remote embedding endpoints from environment variables only.

## OpenAI-Compatible LLM Settings

The main parser, solver, visualization, and dialog calls are controlled by
`[llm]` and `[openai]`.

```toml
[llm]
provider = "openai"  # "openai" | "gemini"
max_retries = 0
max_repair_attempts = 0
request_timeout_s = 60
parser_timeout_s = 90
solver_timeout_s = 300
vizcoder_timeout_s = 240
dialog_timeout_s = 90
stream_solver_json = true
stream_vizcoder_json = true

[openai]
model_default  = "gpt-5.4-pro"
model_parser   = "gpt-5.4-pro"
model_solver   = "gpt-5.4-pro"
model_vizcoder = "gpt-5.4-pro"
model_chat     = "gpt-5.4-pro"
```

Environment variables for the main OpenAI-compatible LLM client:

```bash
export OAI_API_KEY="your_openai_or_gateway_key"
export OAI_BASE_URL="https://api.openai.com/v1"  # optional for standard OpenAI
```

If you use an OpenAI-compatible gateway or Azure OpenAI-compatible endpoint,
set `OAI_BASE_URL` to the SDK-compatible base URL. For Azure-style v1 endpoints,
use:

```bash
export OAI_BASE_URL="https://<resource>.openai.azure.com/openai/v1/"
```

Keep the deployment/model name in `model_parser`, `model_solver`,
`model_vizcoder`, and `model_chat`.

## Embedding Settings

Embeddings are configured separately from the main LLM provider. Retrieval and
indexing use `[embedding]`, `EMB_API_KEY`, and `EMB_URL`.

```toml
[embedding]
provider   = "openai"                  # "openai" | "gemini" | "bge-m3"
model      = "text-embedding-3-large"
dimensions = 1536
```

Environment variables for OpenAI-compatible embeddings:

```bash
export EMB_API_KEY="your_embedding_key"
export EMB_URL="https://api.openai.com/v1"  # optional for standard OpenAI
```

If you want to use the same OpenAI account for both LLM and embeddings, export
both variables:

```bash
export OAI_API_KEY="same_key"
export EMB_API_KEY="same_key"
```

For Azure OpenAI-compatible embeddings:

```bash
export EMB_URL="https://<resource>.openai.azure.com/openai/v1/"
```

Use the embedding deployment name in `[embedding].model`.

Changing `[embedding].dimensions` or switching to `bge-m3` changes the dense
vector dimension. With the default Milvus settings, the backend can recreate
stale dense collections and rebuild them from PostgreSQL on startup.

## Optional Gemini Settings

If `[llm].provider = "gemini"` or `[embedding].provider = "gemini"`, export:

```bash
export GEMINI_API_KEY="your_gemini_key"
```

Gemini model names are configured under `[gemini]`. Dialog uses
`[dialog].model_chat` when the active LLM provider is Gemini.

## PostgreSQL Settings

Set the async SQLAlchemy DSN in `backend/config.toml`:

```toml
[postgres]
dsn = "postgresql+asyncpg://jianbo@localhost:5432/jianbo"
```

Create the database and run migrations:

```bash
createdb jianbo
cd backend
alembic upgrade head
```

If your database name, user, password, host, or port differs, update
`[postgres].dsn`.

## Milvus And Related Services

The bundled Compose stack starts:

- `etcd`: metadata store for Milvus
- `minio`: object storage used by Milvus
- `standalone`: Milvus standalone on `localhost:19530`
- `attu`: Milvus web UI on `http://localhost:1212`

Start the stack from the repo root:

```bash
docker compose up -d
docker compose ps
```

Useful endpoints:

- Milvus gRPC: `localhost:19530`
- Milvus health: `http://localhost:9091/healthz`
- MinIO console: `http://localhost:9001`
- Attu: `http://localhost:1212`

Milvus config in `backend/config.toml`:

```toml
[milvus]
host = "localhost"
port = 19530
database = "default"
auto_bootstrap = true
recreate_dense_on_dim_mismatch = true
auto_reindex_on_bootstrap_change = true
flush_on_write = false
```

On FastAPI startup, `backend/app/main.py` calls Milvus bootstrap when
`auto_bootstrap = true`. It creates missing dense and sparse collections. If a
dense collection dimension no longer matches your embedding config and
`recreate_dense_on_dim_mismatch = true`, it can recreate the dense collection
and reindex from PostgreSQL.

Manual Milvus checks:

```bash
cd backend
python -m app.services.milvus_setup --doctor
```

Manual retrieval rebuild:

```bash
cd backend
python -m scripts.rebuild_retrieval_index --recreate-dense
```

For local `bge-m3` retrieval, install the optional backend extra:

```bash
cd backend
pip install -e ".[retrieval]"
```

Then use:

```toml
[embedding]
provider = "bge-m3"

[retrieval]
sparse_encoder = "bge-m3"
bge_m3_device = "cpu"  # or "mps" / "cuda"
bge_m3_dense_dim = 1024
```

## Visualization Engine Configuration

The visualization pipeline has two backend stages:

1. Stage 1: `visualization_spec_service.py` asks the LLM for a structured
   visualization plan.
2. Stage 2: codegen turns the selected plan into an executable payload.

Configure the preferred generation engine in `backend/config.toml`:

```toml
[viz]
default_engine = "geogebra"  # "geogebra" | "jsxgraph"
```

This setting biases newly generated visualizations. Stored visualizations keep
their own engine value in the database.

### GeoGebra Runtime

GeoGebra is the current frontend visualization host. `frontend/components/VizSandbox.tsx`
dispatches to `GeoGebraSandbox`, which loads:

```text
frontend/public/viz/geogebra-sandbox.html
```

The GeoGebra sandbox uses GeoGebra CDN assets. The required CSP allow-list is
defined in `frontend/next.config.js` for `/viz/geogebra-sandbox.html`.

Backend GeoGebra generation and validation lives in:

- `backend/app/services/geogebra_codegen_service.py`
- `backend/app/services/geogebra_validator.py`
- `backend/app/prompts/geogebra_codegen_prompt.py`

GeoGebra payloads are statically sanitized and validated locally before they
are persisted. Runtime rendering still happens in the frontend sandbox.

### JSXGraph Runtime And Validator

JSXGraph support still exists in backend services and the sandbox assets:

- `backend/app/services/jsxgraph_codegen_service.py`
- `backend/app/services/viz_validator.py`
- `backend/viz_validator/validate.mjs`
- `frontend/public/viz/sandbox.html`
- `frontend/components/JsxgraphSandbox.tsx`

Install the Node validator dependency:

```bash
cd backend/viz_validator
npm install
```

The Python wrapper runs:

```text
node backend/viz_validator/validate.mjs
```

for JSXGraph code validation. The validator uses `acorn` and rejects code
outside the allowed sandbox subset.

If you use JSXGraph rendering, make sure these files exist:

```text
frontend/public/viz/jsxgraphcore.js
frontend/public/viz/jsxgraph.css
```

They are same-origin sandbox assets required by the strict CSP for
`/viz/sandbox.html`.

## Backend Setup And Startup

Install backend dependencies:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run migrations and optional seed data:

```bash
alembic upgrade head
python -m scripts.seed_knowledge
```

Start FastAPI:

```bash
uvicorn app.main:app --reload --port 8787
```

Health check:

```bash
curl http://127.0.0.1:8787/health
```

The backend also writes local logs under `backend/data/logs/` by default:

- `llm_prompts.jsonl`
- `llm_responses.jsonl`
- `visualActions.jsonl`

## Frontend Setup And Startup

Install dependencies:

```bash
cd frontend
npm install
```

Start Next.js:

```bash
npm run dev
```

Open:

```text
http://localhost:3333
```

`frontend/next.config.js` rewrites `/api/*` to:

```text
http://127.0.0.1:8787/api/*
```

If you change the backend port, update the rewrite destination and
`[server].cors_origins` as needed.

## Daily Local Startup

Use three terminals.

Terminal 1, infrastructure:

```bash
cd /Users/jianbo/code/cccode/HAnswer
docker compose up -d
```

Terminal 2, backend:

```bash
cd /Users/jianbo/code/cccode/HAnswer/backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8787
```

Terminal 3, frontend:

```bash
cd /Users/jianbo/code/cccode/HAnswer/frontend
npm run dev
```

Then open:

```text
http://localhost:3333
```

## Validation Commands

Backend:

```bash
cd backend
pytest
ruff check .
python -m app.services.milvus_setup --doctor
```

Frontend:

```bash
cd frontend
npm run build
npm run lint
npm run typecheck
```

Visualization validator:

```bash
cd backend/viz_validator
npm install
```

## Troubleshooting

- `Milvus auto-bootstrap skipped`: confirm `docker compose ps`, wait for
  Milvus health on `http://localhost:9091/healthz`, then run
  `python -m app.services.milvus_setup --doctor`.
- Embedding dimension mismatch: keep
  `recreate_dense_on_dim_mismatch = true` for local development, or manually
  rebuild with `python -m scripts.rebuild_retrieval_index --recreate-dense`.
- OpenAI auth or model errors: verify `OAI_API_KEY`, `OAI_BASE_URL`, and the
  model names under `[openai]`.
- Embedding auth or endpoint errors: verify `EMB_API_KEY`, `EMB_URL`,
  `[embedding].model`, and `[embedding].dimensions`.
- GeoGebra sandbox is blank: check the browser console and confirm the CSP in
  `frontend/next.config.js` still allows `https://www.geogebra.org` and
  `https://cdn.geogebra.org`.
- JSXGraph validation fails before persistence: run `cd backend/viz_validator
  && npm install`, then rerun the backend.
