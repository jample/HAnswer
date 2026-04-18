"""HAnswer Prompt Template Framework (§7.1).

All LLM calls go through this package. Raw ad-hoc prompt strings are
forbidden in application code. Every prompt is a `PromptTemplate`
subclass carrying:
  - Semantic version (tracked in `llm_calls`).
  - Documented `design_decisions` (the *why* behind wording).
  - `.preview()` / `.explain()` / `.diff_preview()` for inspection.
  - `.build()` to assemble messages for the Gemini client.
  - `.schema` JSON Schema for the expected output.

Usage:
    from app.prompts import ParserPrompt, SolverPrompt, VizCoderPrompt, PromptRegistry

    p = ParserPrompt()
    print(p.preview(subject_hint="math"))       # inspect
    print(p.explain())                           # read design rationale
    messages = p.build(subject_hint="math")      # send to LLM

    PromptRegistry.list()                        # see all registered prompts
"""

from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.parser_prompt import ParserPrompt
from app.prompts.registry import PromptRegistry
from app.prompts.solver_prompt import SolverPrompt
from app.prompts.variant_synth_prompt import VariantSynthPrompt
from app.prompts.vizcoder_prompt import VizCoderPrompt

__all__ = [
    "DesignDecision",
    "ParserPrompt",
    "PromptRegistry",
    "PromptTemplate",
    "PromptVersion",
    "SolverPrompt",
    "VariantSynthPrompt",
    "VizCoderPrompt",
]
