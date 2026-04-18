"""Answer job error formatting tests."""

from __future__ import annotations

from app.services.answer_job_service import _friendly_llm_failure


def test_friendly_llm_failure_formats_solver_timeout():
    payload = _friendly_llm_failure(
        "timeout after 180s:",
        failed_stage="solving",
    )
    assert payload["kind"] == "timeout"
    assert payload["failed_stage"] == "solving"
    assert payload["timeout_s"] == 180
    assert "超时" in payload["message"]
    assert "solver_timeout_s" in payload["hint"]


def test_friendly_llm_failure_preserves_non_timeout_errors():
    payload = _friendly_llm_failure(
        "schema validation failed",
        failed_stage="solving",
    )
    assert payload["kind"] == "llm_error"
    assert payload["message"] == "schema validation failed"
