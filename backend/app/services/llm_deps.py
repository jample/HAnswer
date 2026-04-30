"""LLM client singleton wiring (§5.3).

Lazy-constructed so importing `app` doesn't require a network dependency
and so tests can inject a FakeTransport via `set_llm_client`.
"""

from __future__ import annotations

from app.config import settings
from app.services.cost_ledger import PgCostLedger
from app.services.gemini_transport import GoogleGeminiTransport
from app.services.llm_client import JsonlPromptLogger, LLMClient
from app.services.openai_transport import OpenAIResponsesTransport

_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        transport = (
            OpenAIResponsesTransport()
            if settings.llm.provider == "openai"
            else GoogleGeminiTransport()
        )
        _client = LLMClient(
            transport,
            ledger=PgCostLedger(),
            prompt_logger=JsonlPromptLogger(),
        )
    return _client


def set_llm_client(client: LLMClient | None) -> None:
    """Test hook: inject a custom client (e.g. with FakeTransport)."""
    global _client
    _client = client
