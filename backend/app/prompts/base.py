"""PromptTemplate base class, PromptVersion, DesignDecision (§7.1.1-7.1.2).

Every HAnswer prompt subclasses `PromptTemplate` and MUST set:
  - class-level: `version`, `name`, `purpose`, `input_description`,
    `output_description`, `design_decisions`
  - methods:    `system_message`, `user_message`, `schema`
"""

from __future__ import annotations

import difflib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PromptVersion:
    """Semantic version for a prompt template."""

    major: int
    minor: int
    date_updated: str  # ISO date of last change

    def __str__(self) -> str:
        return f"v{self.major}.{self.minor} ({self.date_updated})"


@dataclass
class DesignDecision:
    """One deliberate design choice inside a prompt.

    Recording these is mandatory: they let future editors understand
    *why* a prompt is worded the way it is before changing it.
    """

    title: str
    rationale: str
    alternatives_considered: list[str] = field(default_factory=list)

    def render(self) -> str:
        alt = ""
        if self.alternatives_considered:
            alt = "\n    Alternatives considered: " + "; ".join(self.alternatives_considered)
        return f"  • {self.title}\n    {self.rationale}{alt}"


class PromptTemplate(ABC):
    """Base class for all HAnswer LLM prompts."""

    # -- Required class-level metadata (subclass must override) ----------
    version: PromptVersion
    name: str
    purpose: str
    input_description: str
    output_description: str
    design_decisions: list[DesignDecision]

    # -- Required methods ------------------------------------------------

    @abstractmethod
    def system_message(self, **kwargs: Any) -> str: ...

    @abstractmethod
    def user_message(self, **kwargs: Any) -> str: ...

    @property
    @abstractmethod
    def schema(self) -> dict: ...

    # -- Optional overrides ----------------------------------------------

    def fewshot_examples(self, **kwargs: Any) -> list[dict]:
        """Topic-aware few-shot messages. Default: none.

        Returns a list of {role, content} dicts inserted between the
        system message and the final user message.
        """
        return []

    # -- Public API ------------------------------------------------------

    def build(self, **kwargs: Any) -> list[dict]:
        """Assemble [system, *few-shot, user] message list for the Gemini gateway."""
        messages: list[dict] = [
            {"role": "system", "content": self.system_message(**kwargs)},
        ]
        messages.extend(self.fewshot_examples(**kwargs))
        messages.append({"role": "user", "content": self.user_message(**kwargs)})
        return messages

    def preview(self, **kwargs: Any) -> str:
        """Human-readable dump of the assembled prompt + schema.

        Run this before sending to the LLM to validate wording and
        variable substitution.
        """
        messages = self.build(**kwargs)
        parts: list[str] = [
            "=" * 70,
            f"PROMPT: {self.name}  |  {self.version}",
            "=" * 70,
        ]
        for msg in messages:
            parts.append(f"\n--- [{msg['role'].upper()}] ---")
            parts.append(msg["content"])
        parts.append("\n" + "=" * 70)
        parts.append("OUTPUT SCHEMA:")
        parts.append(json.dumps(self.schema, indent=2, ensure_ascii=False))
        parts.append("=" * 70)
        return "\n".join(parts)

    def explain(self) -> str:
        """Rich summary of purpose + design decisions. Read before editing."""
        lines: list[str] = [
            "=" * 60,
            f"Prompt: {self.name}",
            f"Version: {self.version}",
            "=" * 60,
            f"\nPURPOSE:\n  {self.purpose}",
            f"\nINPUT:\n  {self.input_description}",
            f"\nOUTPUT:\n  {self.output_description}",
            f"\nDESIGN DECISIONS ({len(self.design_decisions)}):",
        ]
        for d in self.design_decisions:
            lines.append(d.render())
        lines.append("\n" + "=" * 60)
        return "\n".join(lines)

    def diff_preview(self, old_kwargs: dict, new_kwargs: dict) -> str:
        """Unified diff showing how a kwargs change affects the rendered prompt."""
        old = self.preview(**old_kwargs).splitlines()
        new = self.preview(**new_kwargs).splitlines()
        return "\n".join(
            difflib.unified_diff(old, new, fromfile="old", tofile="new", lineterm="")
        )

    # -- Traceability helper --------------------------------------------

    def trace_tag(self) -> dict[str, str]:
        """Identifier to record alongside every LLM call for cost/quality analysis."""
        return {"prompt_name": self.name, "prompt_version": str(self.version)}
