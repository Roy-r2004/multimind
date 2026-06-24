"""Jinja2 prompt rendering engine — enterprise template management."""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

STRATEGY_TEMPLATE_MAP = {
    "Reconcile": "system/verdict.j2",
    "Synthesize": "system/verdict.j2",
    "Rank": "system/verdict.j2",
    "Pick Best": "system/verdict.j2",
    "Debate": "system/verdict.j2",
}


class PromptEngine:
    """Renders LLM prompts from version-controlled Jinja2 templates."""

    def __init__(self, prompts_dir: Path | None = None) -> None:
        settings = get_settings()
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
        template = self._env.get_template(template_name)
        rendered = template.render(**context)
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
        )

    def verdict_prompt(
        self,
        *,
        strategy: str,
        user_message: str,
        model_answers: list[dict[str, Any]],
        custom_instructions: str | None = None,
        template_instructions: str | None = None,
    ) -> str:
        template = STRATEGY_TEMPLATE_MAP.get(strategy, "system/verdict.j2")
        return self.render(
            template,
            strategy=strategy,
            user_message=user_message,
            model_answers=model_answers,
            custom_instructions=custom_instructions,
            template_instructions=template_instructions,
        )

    def decision_insurance_prompt(
        self,
        *,
        user_message: str,
        strategy: str,
        model_answers: list[dict[str, Any]],
        verdict_text: str,
        verdict_reason: str,
    ) -> str:
        return self.render(
            "system/decision_insurance.j2",
            user_message=user_message,
            strategy=strategy,
            model_answers=model_answers,
            verdict_text=verdict_text,
            verdict_reason=verdict_reason,
        )


_prompt_engine: PromptEngine | None = None


def get_prompt_engine() -> PromptEngine:
    global _prompt_engine
    if _prompt_engine is None:
        _prompt_engine = PromptEngine()
    return _prompt_engine
