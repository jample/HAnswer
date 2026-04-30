"""OpenAI Responses API transport for the provider-neutral LLM gateway."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.config import settings
from app.services.llm_client import LLMError, LLMTransport, StreamChunk, TransientLLMError

log = logging.getLogger(__name__)

_TRANSIENT_MARKERS = (
    "timeout",
    "temporarily",
    "rate limit",
    "429",
    "500",
    "502",
    "503",
    "504",
    "connection reset",
    "connection closed",
    "connection aborted",
    "connection refused",
    "incomplete chunked read",
    "network error",
    "peer closed",
    "protocol error",
    "remote protocol",
)
_TRANSIENT_EXCEPTION_CLASS_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "NetworkError",
    "PoolTimeout",
    "ProtocolError",
    "ReadError",
    "ReadTimeout",
    "RemoteProtocolError",
    "TimeoutException",
    "TransportError",
    "WriteError",
    "WriteTimeout",
}
_PROVIDER_CONFIG_EXCEPTION_CLASS_NAMES = {
    "AuthenticationError",
    "BadRequestError",
    "NotFoundError",
    "PermissionDeniedError",
}


def _looks_transient_error(err: Exception) -> bool:
    status = getattr(err, "status_code", None)
    if status in {408, 409, 429, 500, 502, 503, 504}:
        return True
    class_names = {cls.__name__ for cls in err.__class__.mro()}
    if class_names & _TRANSIENT_EXCEPTION_CLASS_NAMES:
        return True
    msg = str(err).lower()
    return isinstance(err, TimeoutError) or any(marker in msg for marker in _TRANSIENT_MARKERS)


def _looks_provider_config_error(err: Exception) -> bool:
    status = getattr(err, "status_code", None)
    if status in {400, 401, 403, 404}:
        return True
    class_names = {cls.__name__ for cls in err.__class__.mro()}
    return bool(class_names & _PROVIDER_CONFIG_EXCEPTION_CLASS_NAMES)


def _l2_renormalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return vec
    return [v / norm for v in vec]


def _response_usage(resp: Any) -> tuple[int, int]:
    usage = getattr(resp, "usage", None)
    ptok = int(getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0)
    ctok = int(
        getattr(usage, "output_tokens", 0)
        or getattr(usage, "completion_tokens", 0)
        or 0
    )
    return ptok, ctok


def _extract_output_text(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return str(text)

    parts: list[str] = []
    for item in list(getattr(resp, "output", None) or []):
        for content in list(getattr(item, "content", None) or []):
            content_text = getattr(content, "text", None)
            if content_text:
                parts.append(str(content_text))
    return "".join(parts)


def _schema_format(response_schema: dict) -> dict:
    return {
        "format": {
            "type": "json_schema",
            "name": "structured_response",
            "schema": response_schema,
            "strict": False,
        }
    }


def _convert_messages(messages: list[dict]) -> list[dict]:
    converted: list[dict] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if "parts" not in message:
            converted.append({"role": role, "content": str(message.get("content") or "")})
            continue

        content: list[dict] = []
        for part in list(message.get("parts") or []):
            if "text" in part:
                content.append({"type": "input_text", "text": str(part.get("text") or "")})
                continue
            inline = part.get("inline_data")
            if isinstance(inline, dict):
                mime_type = str(inline.get("mime_type") or "image/png")
                data = str(inline.get("data") or "")
                content.append({
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{data}",
                })
        converted.append({"role": role, "content": content})
    return converted


def _normalize_openai_base_url(base_url: str) -> str:
    """Return an OpenAI-compatible SDK base URL.

    Azure users often copy a deployment-specific REST endpoint such as
    `/openai/deployments/<deployment>/embeddings?api-version=...`. The OpenAI
    SDK appends `/embeddings` itself, so that shape becomes an invalid doubled
    URL. The SDK-compatible Azure shape is `/openai/v1/`, with the deployment
    name passed as `model`.
    """
    raw = base_url.strip()
    if not raw:
        return raw
    parts = urlsplit(raw)
    marker = "/openai/deployments/"
    if marker not in parts.path:
        return raw
    openai_prefix = parts.path.split(marker, 1)[0] + "/openai/v1/"
    return urlunsplit((parts.scheme, parts.netloc, openai_prefix, "", ""))


class OpenAIResponsesTransport(LLMTransport):
    """Adapter over the OpenAI Responses and Embeddings APIs."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        embed_dimensions: int | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI  # type: ignore
        except ImportError as exc:
            raise RuntimeError("openai SDK not installed. `pip install openai`") from exc

        configured_api_key = settings.openai.api_key if api_key is None else api_key
        configured_base_url = settings.openai.base_url if base_url is None else base_url
        configured_base_url = _normalize_openai_base_url(configured_base_url or "")
        kwargs: dict[str, Any] = {"api_key": configured_api_key}
        if configured_base_url:
            kwargs["base_url"] = configured_base_url
        self._client = AsyncOpenAI(**kwargs)
        self._embed_dimensions = (
            settings.openai.embed_dim if embed_dimensions is None else embed_dimensions
        )

    async def generate_json(
        self,
        *,
        model: str,
        messages: list[dict],
        response_schema: dict,
        timeout_s: int,
    ) -> tuple[str, int, int]:
        try:
            resp = await asyncio.wait_for(
                self._client.responses.create(
                    model=model,
                    input=_convert_messages(messages),
                    text=_schema_format(response_schema),
                    timeout=timeout_s,
                ),
                timeout=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            if _looks_transient_error(exc):
                raise TransientLLMError(str(exc)) from exc
            raise
        ptok, ctok = _response_usage(resp)
        return _extract_output_text(resp), ptok, ctok

    async def generate_text(
        self,
        *,
        model: str,
        messages: list[dict],
        timeout_s: int,
    ) -> tuple[str, int, int]:
        try:
            resp = await asyncio.wait_for(
                self._client.responses.create(
                    model=model,
                    input=_convert_messages(messages),
                    timeout=timeout_s,
                ),
                timeout=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            if _looks_transient_error(exc):
                raise TransientLLMError(str(exc)) from exc
            raise
        ptok, ctok = _response_usage(resp)
        return _extract_output_text(resp), ptok, ctok

    async def generate_json_stream(
        self,
        *,
        model: str,
        messages: list[dict],
        response_schema: dict,
        timeout_s: int,
    ) -> tuple[str, int, int]:
        parts: list[str] = []
        ptok = ctok = 0
        async for chunk in self.generate_json_stream_iter(
            model=model,
            messages=messages,
            response_schema=response_schema,
            timeout_s=timeout_s,
        ):
            if chunk.text:
                parts.append(chunk.text)
            if chunk.prompt_tokens:
                ptok = chunk.prompt_tokens
            if chunk.completion_tokens:
                ctok = chunk.completion_tokens
        return "".join(parts), ptok, ctok

    async def generate_json_stream_iter(
        self,
        *,
        model: str,
        messages: list[dict],
        response_schema: dict,
        timeout_s: int,
    ):
        try:
            stream = await asyncio.wait_for(
                self._client.responses.create(
                    model=model,
                    input=_convert_messages(messages),
                    text=_schema_format(response_schema),
                    stream=True,
                    timeout=timeout_s,
                ),
                timeout=timeout_s,
            )
            async for event in stream:
                event_type = str(getattr(event, "type", "") or "")
                if event_type == "response.output_text.delta":
                    yield StreamChunk(text=str(getattr(event, "delta", "") or ""))
                    continue
                if event_type == "response.completed":
                    resp = getattr(event, "response", None)
                    ptok, ctok = _response_usage(resp)
                    yield StreamChunk(text="", prompt_tokens=ptok, completion_tokens=ctok)
                    continue
                if event_type in {"response.error", "error"}:
                    raise TransientLLMError(str(getattr(event, "error", event)))
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, TransientLLMError):
                raise
            if _looks_transient_error(exc):
                raise TransientLLMError(str(exc)) from exc
            raise

    async def embed(
        self,
        *,
        model: str,
        texts: list[str],
        task_type: str | None = None,
    ) -> list[list[float]]:
        del task_type
        if not texts:
            return []
        max_batch = 2048
        out: list[list[float]] = []
        for i in range(0, len(texts), max_batch):
            batch = texts[i : i + max_batch]
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "input": batch,
                    "timeout": settings.llm.embed_timeout_s,
                }
                if self._embed_dimensions > 0:
                    kwargs["dimensions"] = self._embed_dimensions
                resp = await asyncio.wait_for(
                    self._client.embeddings.create(**kwargs),
                    timeout=settings.llm.embed_timeout_s,
                )
            except Exception as exc:  # noqa: BLE001
                if _looks_transient_error(exc):
                    raise TransientLLMError(str(exc)) from exc
                if _looks_provider_config_error(exc):
                    raise LLMError(
                        f"OpenAI embedding request failed for model '{model}'. "
                        "Check EMB_URL, EMB_API_KEY, backend/config.toml "
                        "[embedding].provider, [embedding].model, and "
                        f"[embedding].dimensions. Provider error: {exc}"
                    ) from exc
                raise
            data_rows = getattr(resp, "data", None)
            if data_rows is None:
                response_object = getattr(resp, "object", None)
                response_model = getattr(resp, "model", None)
                raise LLMError(
                    f"OpenAI embedding request for model '{model}' did not return embedding "
                    "data. The gateway may have routed this model to a chat/responses "
                    f"endpoint instead of /embeddings. object={response_object!r}, "
                    f"response_model={response_model!r}."
                )
            rows = sorted(list(data_rows), key=lambda row: int(getattr(row, "index", 0) or 0))
            out.extend(_l2_renormalize([float(v) for v in row.embedding]) for row in rows)
        return out
