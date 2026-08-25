"""Jinja2 prompt rendering engine — enterprise template management."""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.core.logging import get_logger

logger = get_logger(__name__)

STRATEGY_TEMPLATE_MAP = {
    "Reconcile": "system/verdict.j2",
    "Synthesize": "system/verdict.j2",
    "Rank": "system/verdict.j2",
    "Pick Best": "system/verdict.j2",
    "Debate": "system/verdict.j2",
    "Referee": "system/referee.j2",
}

STRICT_REFEREE_BEHAVIOR = """You are the **Referee AI** embedded within my LLM platform. Your role is strictly limited to the following:

#### 1. Data Scope and Boundaries

- Your **only source of information** is the set of answers provided by the individual AI agents in response to my prompt.
- You must **not** consult external web resources, your own pre-trained knowledge, or any information outside the supplied AI answers.

#### 2. Task Definition

- **Synthesize**: Carefully read all AI responses and construct a single, comprehensive answer that:
  - Reflects the strongest, most relevant, and well-supported points from each AI response.
  - Resolves contradictions, highlights areas of consensus, and faithfully incorporates important nuances or minority viewpoints where relevant.
  - Presents the unified answer in a logically structured, clear, and unambiguous manner, avoiding unnecessary repetition.
  - **Do NOT** provide a summary, a resume, or a concise answer. Your output must be fully explicit and elaborate, spelling out all reasoning, details, and supporting logic from the provided AI responses. Every component of your output should be as detailed and explicit as possible, leaving no reasoning or nuance implicit or abbreviated.
- **Do NOT** answer the original prompt independently, nor inject new content or reasoning not present in the AI responses.

#### 3. Operational Logic

- Treat yourself as a specialized synthesis engine, not a general-purpose AI assistant.
- Your **entire output** must be derived solely from the set of AI-generated answers provided for each prompt.

---

This prompt strictly prohibits any summarization, resumes, or concise forms. The Referee AI is directed to be as explicit and detailed as possible, ensuring exhaustive elaboration and clarity in every output."""

# Included by base.j2 — must always exist when using StrictUndefined.
_BASE_CONTEXT_DEFAULTS = {
    "custom_instructions": None,
    "template_instructions": None,
    "user_brain_context": "",
    "rolling_chat_memory": "",
    "recent_conversation_context": "",
    "playbook_context": "",
}


class PromptEngine:
    """Renders LLM prompts from version-controlled Jinja2 templates."""

    def __init__(self, prompts_dir: Path | None = None) -> None:
        base = prompts_dir or Path(__file__).resolve().parent.parent / "prompts"
        self._env = Environment(
            loader=FileSystemLoader(str(base)),
            autoescape=select_autoescape(default=False),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        logger.info("prompt_engine_initialized", prompts_dir=str(base))

    def render(self, template_name: str, **context: Any) -> str:
        merged = {**_BASE_CONTEXT_DEFAULTS, **context}
        template = self._env.get_template(template_name)
        rendered = template.render(**merged)
        logger.debug("prompt_rendered", template=template_name, chars=len(rendered))
        return rendered

    def model_answer_prompt(
        self,
        *,
        user_message: str,
        model_id: str,
        model_name: str,
        vendor: str,
        model_set_name: str,
        custom_instructions: str | None = None,
        template_instructions: str | None = None,
        chat_history: list[dict[str, str]] | None = None,
        user_brain_context: str | None = None,
        rolling_chat_memory: str | None = None,
        recent_conversation_context: str | None = None,
        playbook_context: str | None = None,
    ) -> str:
        return self.render(
            "system/model_answer.j2",
            user_message=user_message,
            model_id=model_id,
            model_name=model_name,
            vendor=vendor,
            model_set_name=model_set_name,
            custom_instructions=custom_instructions,
            template_instructions=template_instructions,
            chat_history=chat_history or [],
            user_brain_context=user_brain_context or "",
            rolling_chat_memory=rolling_chat_memory or "",
            recent_conversation_context=recent_conversation_context or "",
            playbook_context=playbook_context or "",
        )

    def verdict_prompt(
        self,
        *,
        strategy: str,
        user_message: str,
        model_answers: list[dict[str, Any]],
        custom_instructions: str | None = None,
        template_instructions: str | None = None,
        user_brain_context: str | None = None,
        rolling_chat_memory: str | None = None,
        recent_conversation_context: str | None = None,
        playbook_context: str | None = None,
    ) -> str:
        template = STRATEGY_TEMPLATE_MAP.get(strategy, "system/verdict.j2")
        return self.render(
            template,
            strict_referee_behavior=STRICT_REFEREE_BEHAVIOR,
            strategy=strategy,
            user_message=user_message,
            model_answers=model_answers,
            custom_instructions=custom_instructions,
            template_instructions=template_instructions,
            user_brain_context=user_brain_context or "",
            rolling_chat_memory=rolling_chat_memory or "",
            recent_conversation_context=recent_conversation_context or "",
            playbook_context=playbook_context or "",
        )

    def decision_insurance_prompt(
        self,
        *,
        user_message: str,
        strategy: str,
        model_answers: list[dict[str, Any]],
        verdict_text: str,
        verdict_reason: str,
        custom_instructions: str | None = None,
        template_instructions: str | None = None,
        user_brain_context: str | None = None,
    ) -> str:
        return self.render(
            "system/decision_insurance.j2",
            user_message=user_message,
            strategy=strategy,
            model_answers=model_answers,
            verdict_text=verdict_text,
            verdict_reason=verdict_reason,
            custom_instructions=custom_instructions,
            template_instructions=template_instructions,
            user_brain_context=user_brain_context or "",
        )

    def verdict_lesson_prompt(
        self,
        *,
        user_name: str,
        user_message: str,
        strategy: str,
        model_answers: list[dict[str, Any]],
        verdict_model_name: str,
        verdict_text: str,
        verdict_reason: str,
        disagreement_reason: str,
        user_position: str,
        discussion_messages: list[dict[str, Any]] | None = None,
    ) -> str:
        return self.render(
            "system/verdict_lesson.j2",
            user_name=user_name,
            user_message=user_message,
            strategy=strategy,
            model_answers=model_answers,
            verdict_model_name=verdict_model_name,
            verdict_text=verdict_text,
            verdict_reason=verdict_reason,
            disagreement_reason=disagreement_reason,
            user_position=user_position,
            discussion_messages=discussion_messages or [],
        )

    def disagree_discuss_prompt(
        self,
        *,
        user_name: str,
        user_message: str,
        strategy: str,
        model_answers: list[dict[str, Any]],
        verdict_model_name: str,
        verdict_text: str,
        verdict_reason: str,
        messages: list[dict[str, Any]],
    ) -> str:
        return self.render(
            "system/disagree_discuss.j2",
            user_name=user_name,
            user_message=user_message,
            strategy=strategy,
            model_answers=model_answers,
            verdict_model_name=verdict_model_name,
            verdict_text=verdict_text,
            verdict_reason=verdict_reason,
            messages=messages,
        )

    def disagree_finalize_prompt(
        self,
        *,
        user_name: str,
        user_message: str,
        strategy: str,
        verdict_model_name: str,
        verdict_text: str,
        messages: list[dict[str, Any]],
    ) -> str:
        return self.render(
            "system/disagree_finalize.j2",
            user_name=user_name,
            user_message=user_message,
            strategy=strategy,
            verdict_model_name=verdict_model_name,
            verdict_text=verdict_text,
            messages=messages,
        )

    def brain_update_prompt(
        self,
        *,
        user_name: str,
        current_summary: str,
        current_thinking_style: str,
        current_likes: list[str],
        current_dislikes: list[str],
        current_memories: list[dict[str, Any]],
        lesson_title: str,
        lesson_summary: str,
        user_position: str,
        disagreement_reason: str,
        key_insight: str,
        what_to_remember: list[str],
    ) -> str:
        return self.render(
            "system/brain_update.j2",
            user_name=user_name,
            current_summary=current_summary,
            current_thinking_style=current_thinking_style,
            current_likes=current_likes,
            current_dislikes=current_dislikes,
            current_memories=current_memories,
            lesson_title=lesson_title,
            lesson_summary=lesson_summary,
            user_position=user_position,
            disagreement_reason=disagreement_reason,
            key_insight=key_insight,
            what_to_remember=what_to_remember,
        )

    def chat_memory_update_prompt(
        self,
        *,
        current_rolling_memory: str,
        user_message: str,
        verdict_text: str,
        verdict_reason: str | None = None,
    ) -> str:
        return self.render(
            "system/chat_memory_update.j2",
            current_rolling_memory=current_rolling_memory or "",
            user_message=user_message,
            verdict_text=verdict_text,
            verdict_reason=verdict_reason or "",
        )


_prompt_engine: PromptEngine | None = None


def get_prompt_engine() -> PromptEngine:
    global _prompt_engine
    if _prompt_engine is None:
        _prompt_engine = PromptEngine()
    return _prompt_engine
