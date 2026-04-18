"""Prompt registry (§7.1.3).

Central discovery point for all prompt templates. Supports:
  - `PromptRegistry.list()`  — overview of all registered prompts.
  - `PromptRegistry.get(name)` — fetch a singleton instance by name.

Registration is automatic at import time (see `__init__.py`).
"""

from __future__ import annotations

from app.prompts.base import PromptTemplate
from app.prompts.dialog_prompt import DialogPrompt
from app.prompts.parser_prompt import ParserPrompt
from app.prompts.solver_prompt import SolverPrompt
from app.prompts.variant_synth_prompt import VariantSynthPrompt
from app.prompts.vizcoder_prompt import VizCoderPrompt


class _Registry:
    def __init__(self) -> None:
        self._templates: dict[str, PromptTemplate] = {}
        self._register(DialogPrompt())
        self._register(ParserPrompt())
        self._register(SolverPrompt())
        self._register(VizCoderPrompt())
        self._register(VariantSynthPrompt())

    def _register(self, template: PromptTemplate) -> None:
        if template.name in self._templates:
            raise ValueError(f"Duplicate prompt name: {template.name}")
        self._templates[template.name] = template

    def get(self, name: str) -> PromptTemplate:
        if name not in self._templates:
            raise KeyError(
                f"Unknown prompt '{name}'. Known: {sorted(self._templates)}"
            )
        return self._templates[name]

    def names(self) -> list[str]:
        return sorted(self._templates)

    def list(self) -> list[dict]:
        """Summary table for CLI / admin inspection."""
        rows = []
        for name in self.names():
            t = self._templates[name]
            rows.append(
                {
                    "name": t.name,
                    "version": str(t.version),
                    "purpose": t.purpose,
                    "design_decisions": len(t.design_decisions),
                }
            )
        return rows


PromptRegistry = _Registry()
