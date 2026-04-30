"""Dedicated embedding-client singleton wiring.

Embedding traffic is configured independently from the main LLM provider.
This lets retrieval/indexing use its own provider, gateway endpoint, API key,
model, and vector dimension while solver/parser/vizcoder calls keep using
`llm_deps.get_llm_client()`. Remote embedding endpoint/key come from
`EMB_URL` and `EMB_API_KEY`.
"""

from __future__ import annotations

from app.config import settings
from app.services.gemini_transport import GoogleGeminiTransport
from app.services.llm_client import JsonlPromptLogger, LLMClient
from app.services.openai_transport import OpenAIResponsesTransport

_client: LLMClient | None = None


def get_embedding_client() -> LLMClient:
    global _client
    if _client is None:
        if settings.embedding.provider == "openai":
            transport = OpenAIResponsesTransport(
                api_key=settings.embedding.api_key,
                base_url=settings.embedding.endpoint,
                embed_dimensions=settings.embedding.dimensions,
            )
        elif settings.embedding.provider == "gemini":
            transport = GoogleGeminiTransport(
                api_key=settings.embedding.api_key,
                embed_dim=settings.embedding.dimensions,
            )
        else:
            raise RuntimeError(
                "bge-m3 embeddings are local and do not use a remote embedding client."
            )
        _client = LLMClient(
            transport,
            prompt_logger=JsonlPromptLogger(),
        )
    return _client


def set_embedding_client(client: LLMClient | None) -> None:
    """Test hook: inject or clear the dedicated embedding client."""
    global _client
    _client = client
