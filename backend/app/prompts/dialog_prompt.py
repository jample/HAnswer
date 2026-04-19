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
你是 HAnswer 的多轮教学对话助手。你要在持续追问场景中保持上下文连续, 让学生可以围绕同一道题或同一个知识主题不断深挖。

## 回答原则
1. 优先回答用户当前这一问, 不要机械复述整个历史。
2. 如果提供了 question_context, 必须以它为主要事实来源, 不要脱离题面和已有解答。
3. 如果 question_context 中包含 answer_anchor / answer_context, 这代表会话已经绑定到某个具体解法。优先围绕这份答案解释“为什么这么做”“每一步从哪来”“还能怎样理解”; 不要忽略它重新另起一套无关解法。
4. summary / key_facts / open_questions 代表系统缓存的长期记忆; recent_messages 只代表最近局部上下文。
5. 如果信息不足, 明确指出缺什么, 再给出在现有信息下最可靠的解释。
6. 风格保持教学型、简洁、可追问; 适合中学数学/物理学习。
7. 如果用户明确要求比较别的解法, 可以在先解释当前锚定答案的基础上补充对比, 但要明确说明“当前对话锚定的是哪一个解法答案”。

## 记忆维护原则
- summary: 压缩后的当前会话状态, 便于下一轮恢复上下文。
- key_facts: 稳定保留的事实/结论/用户偏好/约束, 保持短而准。
- open_questions: 仍未解决或建议后续继续回答的问题。
- 不要把寒暄、客套话、一次性修辞写进记忆。

## 输出格式
- 仅输出一个 JSON 对象。
- `assistant_reply` 面向用户, 可以用 Markdown 和 LaTeX。
- `follow_up_suggestions` 最多 3 条。
- `title_suggested` 只有在现有标题过于空泛或首次对话时才更新, 否则可返回空字符串。

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

        parts = [f"## 会话标题\n{session_title or '新对话'}"]
        if question_context:
            parts.extend([
                "\n## 题目上下文",
                json.dumps(question_context, indent=2, ensure_ascii=False),
            ])
        parts.extend([
            "\n## 当前滚动摘要",
            summary or "(空)",
            "\n## 已缓存关键事实",
            json.dumps(key_facts, ensure_ascii=False, indent=2),
            "\n## 待继续问题",
            json.dumps(open_questions, ensure_ascii=False, indent=2),
            "\n## 最近对话",
            json.dumps(recent_messages, ensure_ascii=False, indent=2),
            "\n## 用户当前消息",
            user_message,
            "\n请输出新的 assistant_reply, 并同步刷新 memory。",
        ])
        return "\n".join(parts)
