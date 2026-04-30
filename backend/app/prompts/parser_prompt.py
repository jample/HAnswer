"""ParserPrompt — image → ParsedQuestion (§7.2.1).

PURPOSE
    Read a photo of a Chinese middle/high-school math or physics problem
    and return a structured ParsedQuestion JSON.

OPTIMIZATION
    1. `ParserPrompt().preview(subject_hint="math")` to see the full prompt.
    2. `ParserPrompt().explain()` to read design rationale.
    3. Modify wording; bump `minor` version; re-run Parser eval set (§11.3).
"""

from __future__ import annotations

import base64
from typing import Any

from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import PARSED_QUESTION_SCHEMA


class ParserPrompt(PromptTemplate):

    version = PromptVersion(major=1, minor=0, date_updated="2026-04-17")
    name = "parser"

    purpose = (
        "将数学/物理题目的照片解析为结构化 JSON (ParsedQuestion), "
        "包含学科、学段、知识点路径、题目文本(LaTeX)、已知、求解、"
        "图形描述、难度和置信度。"
    )

    input_description = (
        "一张含单道中文数学/物理题目的图片 (JPG/PNG/HEIC/WEBP, ≤8MB)。"
        "可选: subject_hint (用户预选的学科, 可为 None)。"
    )

    output_description = (
        "严格符合 ParsedQuestion JSON Schema 的单个 JSON 对象。"
    )

    design_decisions = [
        DesignDecision(
            title="教师视角, 不是 OCR",
            rationale=(
                "让 LLM 以'阅读学生照片的老师'自居, 会自动补全被手/物体遮挡的文字、"
                "归一化公式, 并用自然语言描述图形。"
            ),
            alternatives_considered=[
                "先 OCR 再理解 — 丢失图形信息且双阶段延迟高",
                "不设角色直接要 JSON — 图形描述质量明显下降",
            ],
        ),
        DesignDecision(
            title="LaTeX 强制归一化",
            rationale="公式统一 $ 包裹, 让下游 Solver 不必再处理自然语言数学表达。",
        ),
        DesignDecision(
            title="置信度字段",
            rationale="confidence<0.5 触发 UI 确认, 避免在错误输入上浪费 Solver token。",
        ),
        DesignDecision(
            title="topic_path 由粗到细",
            rationale="例如 ['代数','一元二次方程','求根公式']; 便于选择 few-shot 和映射 taxonomy。",
        ),
        DesignDecision(
            title="diagram_description 必填",
            rationale=(
                "Solver 和 VizCoder 只收到文本不收到图片, 此字段是它们理解"
                "几何/物理示意图的唯一途径。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return PARSED_QUESTION_SCHEMA

    # ── System ──────────────────────────────────────────────────────

    def system_message(self, **kwargs: Any) -> str:
        """System prompt.

        Structure:
          1. Role: teacher reading photos.
          2. Output contract: JSON only, no prose.
          3. Quality rules: LaTeX, topic_path, difficulty, confidence, diagram_description.
          4. Schema (verbatim).

        Knobs to tune:
          - Difficulty scale wording.
          - Confidence threshold language.
          - Diagram description verbosity.
        """
        return f"""\
    You are an experienced middle-school / high-school math and physics teacher who reads photos submitted by students.
    Your task is to parse the problem shown in the image into structured JSON. Follow the schema exactly and do not output fields outside the schema.

    ## Output requirements
    - Output exactly one JSON object and nothing else. Do not include ```json fences or explanatory prose.
    - All human-readable text fields in the JSON must be in Simplified Chinese.
    - All math expressions must use LaTeX wrapped in $...$ (for example $x^2+2x+1=0$).
    - topic_path must go from broad subject area to specific knowledge point, for example ["代数", "一元二次方程", "求根公式"].
    - difficulty must be an integer from 1 to 5: 1=basic, 2=easy, 3=medium, 4=hard, 5=competition / olympiad style.
    - confidence must be a float in [0, 1] and should honestly reflect parsing certainty; lower it when the image is blurry, cropped, or ambiguous.
    - If the image includes a diagram or figure, diagram_description must describe it in detail using text (labeled points, segment relations, angle marks, coordinates, axes, etc.), because downstream modules cannot see the image.
    - Split given and find into separate list items, one fact per string.
    - The backend separately enforces the exact ParsedQuestion JSON Schema, so every required field must be present with the correct type.
    - Required top-level fields are: subject, grade_band, topic_path, question_text, given, find, diagram_description, difficulty, tags, confidence.
"""

    # ── User ────────────────────────────────────────────────────────

    def user_message(self, **kwargs: Any) -> str:
        """User prompt.

        kwargs:
          subject_hint (str|None):  "math" | "physics" | None.
          image_description (str|None):  rare hint about image quality.
        """
        subject_hint: str | None = kwargs.get("subject_hint")
        image_description: str | None = kwargs.get("image_description")

        parts: list[str] = ["Please parse the problem shown in the image below."]
        if subject_hint:
            cn = {"math": "数学", "physics": "物理"}.get(subject_hint, subject_hint)
            parts.append(f"Hint: the user selected subject '{cn}'.")
        if image_description:
            parts.append(f"Image note: {image_description}")
        return "\n".join(parts)

    # ── Multimodal build ────────────────────────────────────────────

    def build_multimodal(
        self, image_bytes: bytes, mime_type: str, **kwargs: Any,
    ) -> list[dict]:
        """Build messages with the image as an inline_data part.

        Use for Gemini vision calls. Text-only `.build()` still works
        for previewing the prompt shape without the image.
        """
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return [
            {"role": "system", "content": self.system_message(**kwargs)},
            *self.fewshot_examples(**kwargs),
            {
                "role": "user",
                "parts": [
                    {"text": self.user_message(**kwargs)},
                    {"inline_data": {"mime_type": mime_type, "data": b64}},
                ],
            },
        ]
