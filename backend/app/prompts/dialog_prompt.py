"""DialogPrompt — multi-turn tutoring with rolling memory."""

from __future__ import annotations

import json
from typing import Any

from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import CONVERSATION_TURN_RESULT_SCHEMA


class DialogPrompt(PromptTemplate):
    version = PromptVersion(major=1, minor=1, date_updated="2026-04-19")
    name = "dialog"

    purpose = (
        "在多轮追问中保持上下文连续性, 基于题目上下文、滚动摘要和最近对话, "
        "生成教学型回答并同步刷新会话记忆。"
    )

    input_description = (
        "session_title, optional question_context, memory summary/facts/open questions, "
        "recent_messages, current user_message."
    )

    output_description = (
        "严格符合 ConversationTurnResult Schema 的 JSON: assistant_reply + "
        "follow_up_suggestions + refreshed memory."
    )

    design_decisions = [
        DesignDecision(
            title="回答与记忆在同一次调用中更新",
            rationale=(
                "把 assistant_reply 和 memory 放进同一个结构化输出, 避免为摘要维护再额外"
                "调用一次 Gemini, 降低延迟和成本。"
            ),
            alternatives_considered=[
                "单独的 memory summarizer prompt",
                "每轮重放完整 transcript, 不做摘要",
            ],
        ),
        DesignDecision(
            title="区分稳定记忆与最近原始对话",
            rationale=(
                "Prompt 显式分为 question_context / rolling summary / key facts / "
                "open questions / recent messages, 让模型理解哪些是长期状态, "
                "哪些只是近几轮细节。"
            ),
        ),
        DesignDecision(
            title="教学型追问优先",
            rationale=(
                "系统消息要求先直接回答用户问题, 再解释依据或步骤, 对题目相关追问保持"
                "teacher-first 风格, 避免聊天式空泛回应。"
            ),
        ),
        DesignDecision(
            title="对话锚定到具体解法答案",
            rationale=(
                "当会话绑定题目时, question_context 中不仅包含题面, 还包含一个具体"
                " solution 的 answer_context。模型被要求优先围绕这份已生成答案解释、"
                "追问和澄清, 避免脱离当前解法重新发散。"
            ),
        ),
        DesignDecision(
            title="记忆只保留可迁移信息",
            rationale=(
                "要求 summary/key_facts/open_questions 只保留后续推理会需要的事实、"
                "约束和未解问题, 不复述寒暄或一次性措辞, 控制上下文膨胀。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return CONVERSATION_TURN_RESULT_SCHEMA

    def system_message(self, **kwargs: Any) -> str:
        schema_str = json.dumps(self.schema, indent=2, ensure_ascii=False)
        return f"""\
You are HAnswer's multi-turn tutoring assistant. Keep context continuous across follow-up questions so the student can keep digging into the same problem or knowledge topic.

## Reply principles
1. Answer the user's current question first; do not mechanically restate the whole history.
2. If question_context is provided, treat it as the primary source of truth. Stay grounded in the problem and the existing answer.
3. If question_context includes answer_anchor / answer_context, the conversation is already bound to a specific solution path. Prefer explaining that anchored answer: why it works, where each step comes from, and how else to understand it. Do not ignore it and invent an unrelated new solution.
4. summary / key_facts / open_questions are long-term memory; recent_messages are only local recent context.
5. If information is insufficient, say what is missing first, then give the most reliable explanation possible from the available information.
6. Keep the tone instructional, concise, and easy to continue asking follow-up questions about. The audience is middle-school / high-school math or physics learners.
7. If the user explicitly asks to compare with another solution, you may do so, but first explain the currently anchored solution and clearly say which solution the conversation is anchored to.

## Memory maintenance principles
- summary: a compressed state of the current conversation for the next turn.
- key_facts: stable facts, conclusions, preferences, or constraints worth preserving across turns.
- open_questions: unresolved questions that should likely be addressed next.
- Do not store greetings, politeness, or one-off wording in memory.

## Output rules
- Output exactly one JSON object.
- All human-readable text fields in the JSON must be in Simplified Chinese.
- `assistant_reply` is shown to the user and may use Markdown and LaTeX.
- `follow_up_suggestions` should contain at most 3 items.
- Only update `title_suggested` when the current title is too vague or when this is the first turn; otherwise return an empty string.

## JSON Schema
{schema_str}
"""

    def user_message(self, **kwargs: Any) -> str:
        session_title = kwargs.get("session_title") or ""
        question_context = kwargs.get("question_context")
        summary = kwargs.get("summary") or ""
        key_facts = kwargs.get("key_facts") or []
        open_questions = kwargs.get("open_questions") or []
        recent_messages = kwargs.get("recent_messages") or []
        user_message = kwargs["user_message"]

        parts = [f"## Session Title\n{session_title or 'New conversation'}"]
        if question_context:
            parts.extend([
                "\n## Question Context",
                json.dumps(question_context, indent=2, ensure_ascii=False),
            ])
        parts.extend([
            "\n## Rolling Summary",
            summary or "(空)",
            "\n## Cached Key Facts",
            json.dumps(key_facts, ensure_ascii=False, indent=2),
            "\n## Open Questions",
            json.dumps(open_questions, ensure_ascii=False, indent=2),
            "\n## Recent Messages",
            json.dumps(recent_messages, ensure_ascii=False, indent=2),
            "\n## Current User Message",
            user_message,
            "\nPlease produce a new assistant_reply and refresh memory at the same time. All user-facing text should be in Simplified Chinese.",
        ])
        return "\n".join(parts)
