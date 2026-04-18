"""Admin router — LLM cost ledger + prompt inspection (§6, §7.1.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import LLMCall
from app.db.session import session_scope
from app.prompts import PromptRegistry

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def _session() -> AsyncSession:  # type: ignore[override]
    async with session_scope() as s:
        yield s


SESSION_DEP = Depends(_session)


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "•" * len(secret)
    return secret[:4] + "•" * (len(secret) - 8) + secret[-4:]


@router.get("/config")
async def read_config() -> dict:
    """Read-only view of the active config (§9.6 Settings page).

    The API key is returned masked — never in cleartext. Editing is
    explicitly NOT supported via the API; the user must edit
    `backend/config.toml` and restart `uvicorn` (documented in README).
    """
    return {
        "gemini": {
            "api_key_masked": _mask(settings.gemini.api_key),
            "api_key_configured": bool(settings.gemini.api_key),
            "model_parser": settings.gemini.model_parser,
            "model_solver": settings.gemini.model_solver,
            "model_vizcoder": settings.gemini.model_vizcoder,
            "model_embed": settings.gemini.model_embed,
            "embed_dim": settings.gemini.embed_dim,
        },
        "postgres": {
            # Strip the password component from the DSN if present.
            "dsn_masked": _strip_dsn_password(settings.postgres.dsn),
        },
        "milvus": {
            "host": settings.milvus.host,
            "port": settings.milvus.port,
            "database": settings.milvus.database,
            "auto_bootstrap": settings.milvus.auto_bootstrap,
            "recreate_dense_on_dim_mismatch": settings.milvus.recreate_dense_on_dim_mismatch,
        },
        "retrieval": {
            **settings.retrieval.model_dump(),
            "active_dense_dim": settings.retrieval_dense_dim,
        },
        "llm": settings.llm.model_dump(),
        "dialog": settings.dialog.model_dump(),
        "server": {
            "host": settings.server.host,
            "port": settings.server.port,
            "cors_origins": settings.server.cors_origins,
        },
        "note": (
            "编辑 backend/config.toml 并重启后端以修改配置。"
            "为安全起见, API 不支持通过 HTTP 修改密钥。"
        ),
    }


def _strip_dsn_password(dsn: str) -> str:
    """Replace the password portion of a DSN with ••• if present."""
    # Pattern: scheme://user:password@host/...
    try:
        if "://" not in dsn:
            return dsn
        scheme, rest = dsn.split("://", 1)
        if "@" not in rest:
            return dsn
        cred, tail = rest.split("@", 1)
        if ":" in cred:
            user, _pw = cred.split(":", 1)
            return f"{scheme}://{user}:•••@{tail}"
        return dsn
    except Exception:  # noqa: BLE001
        return dsn


@router.get("/llm-cost")
async def llm_cost(
    days: int = Query(7, ge=1, le=90),
    session: AsyncSession = SESSION_DEP,
) -> dict:
    """Aggregate LLM cost + latency from `llm_calls` over the last N days.

    Returns:
        - `window_days`
        - `totals`: cost_usd, prompt_tokens, completion_tokens, calls,
                    ok, repaired, error
        - `by_prompt`: [{task, prompt_version, calls, cost_usd,
                         prompt_tokens, completion_tokens, avg_latency_ms}]
        - `by_day`:    [{date, cost_usd, calls}]
    """
    since = datetime.now(tz=UTC) - timedelta(days=days)

    # Totals
    totals_q = select(
        func.coalesce(func.sum(LLMCall.cost_usd), 0),
        func.coalesce(func.sum(LLMCall.prompt_tokens), 0),
        func.coalesce(func.sum(LLMCall.completion_tokens), 0),
        func.count(LLMCall.id),
        func.count(LLMCall.id).filter(LLMCall.status == "ok"),
        func.count(LLMCall.id).filter(LLMCall.status == "repaired"),
        func.count(LLMCall.id).filter(LLMCall.status == "error"),
    ).where(LLMCall.created_at >= since)
    t = (await session.execute(totals_q)).one()
    totals = {
        "cost_usd": float(t[0] or 0),
        "prompt_tokens": int(t[1] or 0),
        "completion_tokens": int(t[2] or 0),
        "calls": int(t[3] or 0),
        "ok": int(t[4] or 0),
        "repaired": int(t[5] or 0),
        "error": int(t[6] or 0),
    }

    # By (task, prompt_version)
    by_prompt_q = select(
        LLMCall.task,
        LLMCall.prompt_version,
        func.count(LLMCall.id),
        func.coalesce(func.sum(LLMCall.cost_usd), 0),
        func.coalesce(func.sum(LLMCall.prompt_tokens), 0),
        func.coalesce(func.sum(LLMCall.completion_tokens), 0),
        func.coalesce(func.avg(LLMCall.latency_ms), 0),
    ).where(LLMCall.created_at >= since).group_by(
        LLMCall.task, LLMCall.prompt_version,
    ).order_by(func.sum(LLMCall.cost_usd).desc())
    by_prompt = [
        {
            "task": r[0],
            "prompt_version": r[1],
            "calls": int(r[2]),
            "cost_usd": float(r[3] or 0),
            "prompt_tokens": int(r[4] or 0),
            "completion_tokens": int(r[5] or 0),
            "avg_latency_ms": int(r[6] or 0),
        }
        for r in (await session.execute(by_prompt_q)).all()
    ]

    # By day
    day_col = func.date_trunc("day", LLMCall.created_at)
    by_day_q = select(
        day_col,
        func.coalesce(func.sum(LLMCall.cost_usd), 0),
        func.count(LLMCall.id),
    ).where(LLMCall.created_at >= since).group_by(day_col).order_by(day_col)
    by_day = [
        {
            "date": r[0].date().isoformat() if r[0] else None,
            "cost_usd": float(r[1] or 0),
            "calls": int(r[2]),
        }
        for r in (await session.execute(by_day_q)).all()
    ]

    return {
        "window_days": days,
        "totals": totals,
        "by_prompt": by_prompt,
        "by_day": by_day,
    }


@router.get("/prompts")
async def list_prompts() -> dict:
    """Registry overview — lets the UI show available prompts and versions."""
    return {"prompts": PromptRegistry.list()}


@router.get("/prompts/{name}/explain")
async def explain_prompt(name: str) -> dict:
    try:
        t = PromptRegistry.get(name)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    return {
        "name": t.name,
        "version": str(t.version),
        "purpose": t.purpose,
        "input_description": t.input_description,
        "output_description": t.output_description,
        "design_decisions": [
            {
                "title": d.title,
                "rationale": d.rationale,
                "alternatives_considered": d.alternatives_considered,
            }
            for d in t.design_decisions
        ],
        "schema": t.schema,
    }


@router.post("/prompts/{name}/preview")
async def preview_prompt(name: str, kwargs: dict) -> dict:
    """Render the prompt with the given kwargs WITHOUT calling the LLM."""
    try:
        t = PromptRegistry.get(name)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    try:
        return {"preview": t.preview(**kwargs), "version": str(t.version)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"preview failed: {e}") from e
