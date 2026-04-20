"""GeminiClient singleton wiring (§5.3).

Lazy-constructed so importing `app` doesn't require a network dependency
and so tests can inject a FakeTransport via `set_llm_client`.
"""

from __future__ import annotations

from app.services.cost_ledger import PgCostLedger
from app.services.gemini_transport import GoogleGeminiTransport
from app.services.llm_client import GeminiClient, JsonlPromptLogger

_client: GeminiClient | None = None


def get_llm_client() -> GeminiClient:
    global _client
    if _client is None:
        _client = GeminiClient(
            GoogleGeminiTransport(),
            ledger=PgCostLedger(),
            prompt_logger=JsonlPromptLogger(),
        )
    return _client


def set_llm_client(client: GeminiClient | None) -> None:
    """Test hook: inject a custom client (e.g. with FakeTransport)."""
    global _client
    _client = client
