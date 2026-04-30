from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_visual_actions_endpoint_writes_jsonl(tmp_path):
    original_path = settings.storage.visual_action_log_file
    log_path = tmp_path / "visualActions.jsonl"
    settings.storage.visual_action_log_file = str(log_path)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/answer/visual-actions",
                json={
                    "records": [
                        {
                            "source": "frontend",
                            "phase": "runtime",
                            "action": "sandbox.render.start",
                            "status": "info",
                            "question_id": "q-1",
                            "visualization_id": "viz-1",
                            "engine": "jsxgraph",
                            "component": "sandbox",
                            "details": {"element_type": "circle"},
                        },
                        {
                            "source": "sandbox",
                            "phase": "runtime",
                            "action": "board.create.error",
                            "status": "error",
                            "question_id": "q-1",
                            "visualization_id": "viz-1",
                            "engine": "jsxgraph",
                            "component": "board",
                            "details": {"element_type": "intersection", "name": "P1_prime"},
                            "error": "intersection failed",
                        },
                    ]
                },
            )

        assert response.status_code == 200, response.text
        assert response.json() == {"logged": 2}
        rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 2
        assert rows[0]["action"] == "sandbox.render.start"
        assert rows[0]["details"]["element_type"] == "circle"
        assert rows[1]["status"] == "error"
        assert rows[1]["error"] == "intersection failed"
    finally:
        settings.storage.visual_action_log_file = original_path