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


def test_friendly_llm_failure_formats_service_unavailable():
    payload = _friendly_llm_failure(
        (
            "503 Service Unavailable. {'message': '{\"error\": {\"code\": 503, "
            "\"message\": \"This model is currently experiencing high demand.\", "
            "\"status\": \"UNAVAILABLE\"}}'}"
        ),
        failed_stage="solving",
    )
    assert payload["kind"] == "service_overloaded"
    assert payload["failed_stage"] == "solving"
    assert payload["retryable"] is True
    assert "暂时繁忙" in payload["message"]
    assert "等待 30 到 90 秒后重试" in payload["hint"]
