"""Concrete Gemini transport using google-genai SDK.

Kept small and isolated so tests can substitute FakeTransport without
depending on the network.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings
from app.services.llm_client import GeminiTransport, TransientLLMError

log = logging.getLogger(__name__)


def _flatten_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Gemini-genai expects (system_instruction, contents).

    `messages` is [{role, content|parts}, ...].
    System messages are joined into system_instruction.
    User/assistant messages become contents with role 'user'|'model'.
    Multimodal user messages keep `parts` verbatim.
    """
    sys_parts: list[str] = []
    contents: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            sys_parts.append(m["content"])
            continue
        gen_role = "model" if role == "assistant" else "user"
        if "parts" in m:
            contents.append({"role": gen_role, "parts": m["parts"]})
        else:
            contents.append({"role": gen_role, "parts": [{"text": m["content"]}]})
    return "\n\n".join(sys_parts), contents


class GoogleGeminiTransport(GeminiTransport):
    """Adapter over google-genai. Real network IO happens here."""

    def __init__(self) -> None:
        try:
            from google import genai  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "google-genai not installed. `pip install google-genai`"
            ) from e
        self._client = genai.Client(api_key=settings.gemini.api_key)

    async def generate_json(
        self,
        *,
        model: str,
        messages: list[dict],
        response_schema: dict,
        timeout_s: int,
    ) -> tuple[str, int, int]:
        from google.genai import types  # type: ignore

        system_instruction, contents = _flatten_messages(messages)
        cfg = types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            response_mime_type="application/json",
            response_schema=response_schema,
        )
        try:
            resp = await self._client.aio.models.generate_content(  # type: ignore[attr-defined]
                model=model,
                contents=contents,
                config=cfg,
            )
        except Exception as e:  # noqa: BLE001
            # Classify a handful of transient errors; everything else is fatal.
            msg = str(e).lower()
            if any(k in msg for k in ("timeout", "unavailable", "429", "503", "deadline")):
                raise TransientLLMError(str(e)) from e
            raise

        raw = resp.text or ""
        usage = getattr(resp, "usage_metadata", None)
        ptok = int(getattr(usage, "prompt_token_count", 0) or 0)
        ctok = int(getattr(usage, "candidates_token_count", 0) or 0)
        return raw, ptok, ctok

    async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]:
        resp = await self._client.aio.models.embed_content(  # type: ignore[attr-defined]
            model=model,
            contents=texts,
        )
        return [list(e.values) for e in resp.embeddings]
