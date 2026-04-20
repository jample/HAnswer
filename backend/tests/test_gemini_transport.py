import asyncio

import pytest

from app.config import settings
from app.services import gemini_transport


@pytest.mark.asyncio
async def test_gemini_call_limiter_serializes_when_limit_is_one():
    old_limit = settings.llm.max_parallel_gemini_calls
    old_limiter = gemini_transport._gemini_call_limiter
    old_limiter_limit = gemini_transport._gemini_call_limiter_limit
    settings.llm.max_parallel_gemini_calls = 1
    gemini_transport._gemini_call_limiter = None
    gemini_transport._gemini_call_limiter_limit = None

    current = 0
    peak = 0

    async def _worker() -> None:
        nonlocal current, peak
        async with gemini_transport._acquire_gemini_call_slot():
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.01)
            current -= 1

    try:
        await asyncio.gather(_worker(), _worker(), _worker())
    finally:
        settings.llm.max_parallel_gemini_calls = old_limit
        gemini_transport._gemini_call_limiter = old_limiter
        gemini_transport._gemini_call_limiter_limit = old_limiter_limit

    assert peak == 1


@pytest.mark.asyncio
async def test_gemini_call_limiter_allows_configured_parallelism():
    old_limit = settings.llm.max_parallel_gemini_calls
    old_limiter = gemini_transport._gemini_call_limiter
    old_limiter_limit = gemini_transport._gemini_call_limiter_limit
    settings.llm.max_parallel_gemini_calls = 2
    gemini_transport._gemini_call_limiter = None
    gemini_transport._gemini_call_limiter_limit = None

    current = 0
    peak = 0

    async def _worker() -> None:
        nonlocal current, peak
        async with gemini_transport._acquire_gemini_call_slot():
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.01)
            current -= 1

    try:
        await asyncio.gather(_worker(), _worker(), _worker())
    finally:
        settings.llm.max_parallel_gemini_calls = old_limit
        gemini_transport._gemini_call_limiter = old_limiter
        gemini_transport._gemini_call_limiter_limit = old_limiter_limit

    assert peak == 2