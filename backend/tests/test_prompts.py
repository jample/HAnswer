"""Prompt Template framework tests (§11.1 verification).

Verifies for every registered prompt:
  - explain() contains ≥3 design decisions with title + rationale;
  - preview() renders without raising and includes system / user sections;
  - build() returns at least [system, user];
  - trace_tag() exposes name + version;
  - diff_preview() produces a diff when kwargs change;
  - call_structured() round-trips a valid JSON via FakeTransport;
  - call_structured() triggers the repair loop on first invalid JSON
    and succeeds on the second attempt.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.prompts import PromptRegistry
from app.schemas import ParsedQuestion
from app.services.llm_client import FakeTransport, GeminiClient, LLMError


# ---- registry-wide invariants -----------------------------------------

def test_registry_has_three_core_prompts():
    names = PromptRegistry.names()
    assert {"parser", "solver", "vizcoder"}.issubset(set(names))


@pytest.mark.parametrize("name", ["parser", "solver", "vizcoder"])
def test_prompt_has_design_decisions(name: str):
    t = PromptRegistry.get(name)
    assert len(t.design_decisions) >= 3, "must document ≥3 design decisions"
    for d in t.design_decisions:
        assert d.title and d.rationale, "each decision needs title + rationale"


@pytest.mark.parametrize("name", ["parser", "solver", "vizcoder"])
def test_explain_is_stable(name: str):
    t = PromptRegistry.get(name)
    text = t.explain()
    assert t.name in text
    assert "DESIGN DECISIONS" in text
    # each design decision must appear in the rendered explanation
    for d in t.design_decisions:
        assert d.title in text


# ---- preview / build --------------------------------------------------

SAMPLE_KWARGS: dict[str, dict] = {
    "parser": {"raw_ocr": "已知 a=3, b=4, 求斜边长。"},
    "solver": {
        "parsed_question": {
            "subject": "math",
            "grade_band": "high",
            "stem_text": "已知 a=3, b=4, 求斜边长。",
            "givens": [{"symbol": "a", "value": "3"}, {"symbol": "b", "value": "4"}],
            "unknowns": ["c"],
            "figures": [],
            "candidate_kps": [],
        },
        "existing_patterns": [],
        "existing_kps": [],
    },
    "vizcoder": {
        "parsed_question": {"stem_text": "直角三角形斜边长。"},
        "answer_package": {
            "question_understanding": {"restated_goal": "求 c"},
            "solution_steps": [],
            "method_pattern": None,
        },
    },
}


@pytest.mark.parametrize("name", ["parser", "solver", "vizcoder"])
def test_preview_renders(name: str):
    t = PromptRegistry.get(name)
    out = t.preview(**SAMPLE_KWARGS[name])
    assert "[SYSTEM]" in out
    assert "[USER]" in out
    assert "OUTPUT SCHEMA" in out


@pytest.mark.parametrize("name", ["parser", "solver", "vizcoder"])
def test_build_has_system_and_user(name: str):
    t = PromptRegistry.get(name)
    msgs = t.build(**SAMPLE_KWARGS[name])
    roles = [m["role"] for m in msgs]
    assert roles[0] == "system"
    assert roles[-1] == "user"


@pytest.mark.parametrize("name", ["parser", "solver", "vizcoder"])
def test_trace_tag(name: str):
    t = PromptRegistry.get(name)
    tag = t.trace_tag()
    assert tag["prompt_name"] == name
    assert tag["prompt_version"].startswith("v")


def test_diff_preview_shows_changes():
    t = PromptRegistry.get("parser")
    diff = t.diff_preview(
        old_kwargs={"subject_hint": "math"},
        new_kwargs={"subject_hint": "physics"},
    )
    assert "-" in diff and "+" in diff


# ---- GeminiClient round-trip via FakeTransport -----------------------


_VALID_PARSED = {
    "subject": "math",
    "grade_band": "senior",
    "topic_path": ["几何", "三角形"],
    "question_text": "已知 a=3, b=4, 求斜边长。",
    "given": ["a=3", "b=4"],
    "find": ["c"],
    "diagram_description": "",
    "difficulty": 2,
    "tags": [],
    "confidence": 0.9,
}


def test_call_structured_happy_path():
    transport = FakeTransport(json_by_model={"gemini-2.0-flash": json.dumps(_VALID_PARSED)})
    client = GeminiClient(transport)
    parser = PromptRegistry.get("parser")
    result = asyncio.run(
        client.call_structured(
            template=parser,
            model="gemini-2.0-flash",
            model_cls=ParsedQuestion,
            template_kwargs={"raw_ocr": "已知 a=3, b=4, 求斜边长。"},
        )
    )
    assert isinstance(result, ParsedQuestion)
    assert result.find == ["c"]
    assert len(transport.calls) == 1  # no repair needed


class _RepairTransport(FakeTransport):
    """First call returns bad JSON, second returns valid."""

    def __init__(self, bad: str, good: str) -> None:
        super().__init__()
        self._responses = [bad, good]

    async def generate_json(self, *, model, messages, response_schema, timeout_s):
        self.calls.append({"model": model, "messages": messages})
        raw = self._responses.pop(0) if self._responses else "{}"
        return raw, 0, 0


def test_call_structured_repair_loop_recovers():
    transport = _RepairTransport(bad="{}", good=json.dumps(_VALID_PARSED))
    client = GeminiClient(transport)
    parser = PromptRegistry.get("parser")
    result = asyncio.run(
        client.call_structured(
            template=parser,
            model="gemini-2.0-flash",
            model_cls=ParsedQuestion,
            template_kwargs={"raw_ocr": "q"},
        )
    )
    assert isinstance(result, ParsedQuestion)
    assert len(transport.calls) == 2  # one repair round-trip


def test_call_structured_gives_up_after_max_attempts():
    transport = FakeTransport(json_by_model={"gemini-2.0-flash": "{}"})
    client = GeminiClient(transport)
    parser = PromptRegistry.get("parser")
    with pytest.raises(LLMError):
        asyncio.run(
            client.call_structured(
                template=parser,
                model="gemini-2.0-flash",
                model_cls=ParsedQuestion,
                template_kwargs={"raw_ocr": "q"},
            )
        )
