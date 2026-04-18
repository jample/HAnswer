"""Typed application configuration.

Loads `backend/config.toml` (or an override path) and exposes a singleton
`settings` object. Missing file falls back to environment variables so the
module still imports during CI/tests.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class GeminiSettings(BaseModel):
    # `model_*` field names collide with pydantic's protected namespace.
    model_config = ConfigDict(protected_namespaces=())

    api_key: str = ""
    model_parser: str = "gemini-3.1-pro"
    model_solver: str = "gemini-3.1-pro"
    model_vizcoder: str = "gemini-3.1-pro"
    model_embed: str = "text-embedding-004"
    embed_dim: int = 768


class PostgresSettings(BaseModel):
    dsn: str = "postgresql+asyncpg://jianbo@localhost:5432/jianbo"


class MilvusSettings(BaseModel):
    host: str = "localhost"
    port: int = 19530
    database: str = "default"
    # Create dense + sparse collections automatically on FastAPI
    # startup if they are missing. Safe to leave on — no-op when the
    # collections already exist; logs a warning and continues when
    # Milvus itself is unreachable.
    auto_bootstrap: bool = True


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3333"])


class StorageSettings(BaseModel):
    image_dir: str = "./data/images"


class LLMSettings(BaseModel):
    max_retries: int = 3
    max_repair_attempts: int = 2
    request_timeout_s: int = 60


class RetrievalSettings(BaseModel):
    """M5 hybrid retrieval knobs (§3.4).

    `embedder`  — dense-vector provider. "gemini" (default) uses the
                  Gemini embedding model already configured. "bge-m3"
                  loads a local BAAI/bge-m3 model via FlagEmbedding
                  (optional dependency, lazy-imported).
    `multi_route` — when True, similar-question retrieval runs three
                  routes in parallel (dense, sparse, structural) and
                  fuses their ranks with Reciprocal-Rank Fusion.
                  When False, falls back to single-route §3.4 formula.
    `sparse_encoder` — sparse-signal provider. "bm25" (default) uses
                  the in-process Chinese-friendly BM25 encoder; "bge-m3"
                  reuses the loaded bge-m3 model's lexical head.
    """

    embedder: str = "gemini"               # "gemini" | "bge-m3"
    sparse_encoder: str = "bm25"           # "bm25" | "bge-m3"
    multi_route: bool = True
    rrf_k: int = 60                        # RRF damping constant
    route_weights_dense: float = 1.0
    route_weights_sparse: float = 1.0
    route_weights_structural: float = 1.0
    bge_m3_model: str = "BAAI/bge-m3"      # HF repo id
    bge_m3_device: str = "cpu"             # "cpu" | "cuda" | "mps"
    wide_k_multiplier: int = 3             # per-route top-K = k * multiplier


class Settings(BaseModel):
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    milvus: MilvusSettings = Field(default_factory=MilvusSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)


def _candidate_paths() -> list[Path]:
    env = os.environ.get("HANSWER_CONFIG")
    here = Path(__file__).resolve().parent.parent  # backend/
    paths = []
    if env:
        paths.append(Path(env))
    paths.extend([here / "config.toml", here / "config.example.toml"])
    return paths


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    raw: dict = {}
    for path in _candidate_paths():
        if path.is_file():
            with path.open("rb") as f:
                raw = tomllib.load(f)
            break
    # API key is sourced exclusively from $GEMINI_API_KEY env var.
    # Any api_key value in config.toml is intentionally ignored.
    env_key = os.environ.get("GEMINI_API_KEY", "")
    raw.setdefault("gemini", {})["api_key"] = env_key
    return Settings(**raw)


settings = get_settings()
