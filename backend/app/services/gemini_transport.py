"""Concrete Gemini transport using google-genai SDK.

Kept small and isolated so tests can substitute FakeTransport without
depending on the network.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.services.llm_client import GeminiTransport, StreamChunk, TransientLLMError

log = logging.getLogger(__name__)
_EMBED_FALLBACK_MODEL = "gemini-embedding-2-preview"
_LEGACY_TEXT_EMBED_MODELS = {"text-embedding-004", "models/text-embedding-004"}
# gemini-embedding-2-preview uses task prefixes in prompt text, NOT task_type param
_EMBED_V2_MODELS = {"gemini-embedding-2-preview"}


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
        try:
            import google.generativeai as legacy_genai  # type: ignore
        except ImportError:
            legacy_genai = None
        self._client = genai.Client(api_key=settings.gemini.api_key)
        self._legacy_genai = legacy_genai
        self._text_embedding_004_available: bool | None = None

    @staticmethod
    def _usage_counts(resp) -> tuple[int, int]:
        usage = getattr(resp, "usage_metadata", None)
        ptok = int(getattr(usage, "prompt_token_count", 0) or 0)
        ctok = int(getattr(usage, "candidates_token_count", 0) or 0)
        return ptok, ctok

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
            # `response_schema` expects the SDK's native Schema shape.
            # Our prompt layer stores JSON Schema (§7.1.5), so send it via
            # `response_json_schema` instead; passing raw JSON Schema to
            # `response_schema` makes the Gemini API reject keys such as
            # `additionalProperties`.
            response_json_schema=response_schema,
        )
        try:
            resp = await asyncio.wait_for(
                self._client.aio.models.generate_content(  # type: ignore[attr-defined]
                    model=model,
                    contents=contents,
                    config=cfg,
                ),
                timeout=timeout_s,
            )
        except Exception as e:  # noqa: BLE001
            # Classify a handful of transient errors; everything else is fatal.
            msg = str(e).lower()
            if any(k in msg for k in ("timeout", "unavailable", "429", "503", "deadline")):
                raise TransientLLMError(str(e)) from e
            raise

        raw = resp.text or ""
        ptok, ctok = self._usage_counts(resp)
        return raw, ptok, ctok

    async def generate_json_stream(
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
            response_json_schema=response_schema,
        )
        try:
            stream = await asyncio.wait_for(
                self._client.aio.models.generate_content_stream(  # type: ignore[attr-defined]
                    model=model,
                    contents=contents,
                    config=cfg,
                ),
                timeout=timeout_s,
            )
            parts: list[str] = []
            ptok = ctok = 0
            while True:
                try:
                    chunk = await asyncio.wait_for(stream.__anext__(), timeout=timeout_s)
                except StopAsyncIteration:
                    break
                chunk_text = getattr(chunk, "text", None)
                if chunk_text:
                    parts.append(str(chunk_text))
                chunk_ptok, chunk_ctok = self._usage_counts(chunk)
                if chunk_ptok:
                    ptok = chunk_ptok
                if chunk_ctok:
                    ctok = chunk_ctok
            return "".join(parts), ptok, ctok
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if isinstance(e, TimeoutError) or any(
                k in msg for k in ("timeout", "unavailable", "429", "503", "deadline")
            ):
                raise TransientLLMError(str(e)) from e
            raise

    async def generate_json_stream_iter(
        self,
        *,
        model: str,
        messages: list[dict],
        response_schema: dict,
        timeout_s: int,
    ):
        """Yield ``StreamChunk`` deltas as they arrive from Gemini.

        This is the true incremental-streaming path. Callers downstream
        (for example ``GeminiClient.call_structured_streaming``) feed
        the deltas into a streaming JSON parser to emit SSE events
        without waiting for the full response.
        """
        from google.genai import types  # type: ignore

        system_instruction, contents = _flatten_messages(messages)
        cfg = types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            response_mime_type="application/json",
            response_json_schema=response_schema,
        )
        try:
            stream = await asyncio.wait_for(
                self._client.aio.models.generate_content_stream(  # type: ignore[attr-defined]
                    model=model,
                    contents=contents,
                    config=cfg,
                ),
                timeout=timeout_s,
            )
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        stream.__anext__(), timeout=timeout_s
                    )
                except StopAsyncIteration:
                    return
                text = getattr(chunk, "text", None) or ""
                ptok, ctok = self._usage_counts(chunk)
                yield StreamChunk(
                    text=str(text), prompt_tokens=ptok, completion_tokens=ctok,
                )
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if isinstance(e, TimeoutError) or any(
                k in msg for k in ("timeout", "unavailable", "429", "503", "deadline")
            ):
                raise TransientLLMError(str(e)) from e
            raise

    async def _legacy_embed(
        self,
        *,
        model: str,
        texts: list[str],
        task_type: str | None,
    ) -> list[list[float]]:
        if self._legacy_genai is None:
            raise RuntimeError(
                "google-generativeai is not installed, but it is required for "
                "`text-embedding-004`. Install `google-generativeai` or switch "
                "to `gemini-embedding-001`."
            )

        def _run() -> list[list[float]]:
            self._legacy_genai.configure(api_key=settings.gemini.api_key)
            result = self._legacy_genai.embed_content(
                model=model if model.startswith("models/") else f"models/{model}",
                content=texts,
                task_type=task_type or "RETRIEVAL_DOCUMENT",
            )
            raw = result.get("embedding", result)
            if raw and isinstance(raw[0], (float, int)):
                return [list(map(float, raw))]
            return [list(map(float, row)) for row in raw]

        return await asyncio.to_thread(_run)

    async def embed(
        self,
        *,
        model: str,
        texts: list[str],
        task_type: str | None = None,
    ) -> list[list[float]]:
        from google.genai import types  # type: ignore

        # google-genai SDK note: `embed_content(contents=...)` is overloaded.
        # Passing `list[str]` is interpreted as the *Parts of a single Content*
        # and returns ONE embedding. Passing `list[Content]` performs a real
        # batch call and returns N embeddings — which is what we want.
        # We chunk into MAX_BATCH-sized requests to stay under per-call limits.
        MAX_BATCH = 100  # Gemini embedContent batch ceiling

        if not texts:
            return []

        def _to_contents(chunk: list[str]) -> list:
            return [
                types.Content(parts=[types.Part(text=t)]) for t in chunk
            ]

        async def _embed_chunk(model_name: str, chunk: list[str], use_task_type: bool) -> list[list[float]]:
            config_kwargs: dict = {
                "output_dimensionality": settings.gemini.embed_dim,
            }
            if use_task_type and task_type:
                config_kwargs["task_type"] = task_type
            resp = await asyncio.wait_for(
                self._client.aio.models.embed_content(  # type: ignore[attr-defined]
                    model=model_name,
                    contents=_to_contents(chunk),
                    config=types.EmbedContentConfig(**config_kwargs),
                ),
                timeout=settings.llm.embed_timeout_s,
            )
            return [list(emb.values) for emb in resp.embeddings]

        async def _embed_all(model_name: str, use_task_type: bool = True) -> list[list[float]]:
            chunks = [texts[i : i + MAX_BATCH] for i in range(0, len(texts), MAX_BATCH)]
            results = await asyncio.gather(
                *[_embed_chunk(model_name, ch, use_task_type) for ch in chunks]
            )
            out: list[list[float]] = []
            for part in results:
                out.extend(part)
            return out

        # Legacy path for text-embedding-004
        if model in _LEGACY_TEXT_EMBED_MODELS:
            if self._text_embedding_004_available is False:
                log.warning(
                    "embedding model %s previously failed in this environment; "
                    "using %s instead",
                    model,
                    _EMBED_FALLBACK_MODEL,
                )
                is_v2 = _EMBED_FALLBACK_MODEL in _EMBED_V2_MODELS
                return await _embed_all(_EMBED_FALLBACK_MODEL, use_task_type=not is_v2)
            try:
                out = await self._legacy_embed(model=model, texts=texts, task_type=task_type)
                self._text_embedding_004_available = True
                return out
            except Exception as e:  # noqa: BLE001
                msg = str(e).lower()
                should_fallback = (
                    "not found" in msg
                    or "not supported for embedcontent" in msg
                    or "embedcontent" in msg
                )
                if not should_fallback:
                    raise
                self._text_embedding_004_available = False
                log.warning(
                    "embedding model %s unavailable in this Gemini environment; "
                    "falling back to %s",
                    model,
                    _EMBED_FALLBACK_MODEL,
                )
                is_v2 = _EMBED_FALLBACK_MODEL in _EMBED_V2_MODELS
                return await _embed_all(_EMBED_FALLBACK_MODEL, use_task_type=not is_v2)

        # genai path — skip task_type for v2 models (they use text prefixes)
        is_v2 = model in _EMBED_V2_MODELS
        try:
            return await _embed_all(model, use_task_type=not is_v2)
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            should_fallback = (
                model != _EMBED_FALLBACK_MODEL
                and (
                    "embedcontent" in msg
                    or "not supported for embedcontent" in msg
                    or "model" in msg and "not found" in msg
                )
            )
            if not should_fallback:
                raise
            log.warning(
                "embedding model %s unavailable; falling back to %s",
                model,
                _EMBED_FALLBACK_MODEL,
            )
            fb_v2 = _EMBED_FALLBACK_MODEL in _EMBED_V2_MODELS
            return await _embed_all(_EMBED_FALLBACK_MODEL, use_task_type=not fb_v2)
