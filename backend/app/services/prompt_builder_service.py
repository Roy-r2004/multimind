"""Prompt builder — isolated ephemeral council for prompt refinement.

Never creates Chat/Turn/ModelAnswer/Verdict rows and never loads Chat A context.
CostRecord requires turn_id, so usage is not persisted here (by design).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import AppError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import ModelSet
from app.llm.catalog import get_model
from app.llm.providers import LLMResponse, get_provider_registry
from app.schemas.api import (
    PromptBuilderImproveResponse,
    PromptBuilderRefineMessage,
    PromptBuilderRefineResponse,
)
from app.services.brain_service import DEFAULT_BRAIN_MODEL

logger = get_logger(__name__)

ALLOWED_ROLES = frozenset({"user", "assistant"})
MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 4000
MAX_TOTAL_CHARS = 48_000
COUNCIL_MAX_TOKENS = 1024
REFEREE_MAX_TOKENS = 1024

COUNCIL_SYSTEM = """You are a member of MultiMind's Prompt Builder council.
Your only job is to improve the user's prompt based on the Prompt Builder conversation.

Rules:
- Preserve the user's important intent and constraints.
- Incorporate refinement instructions from later user messages.
- Make the prompt clearer, more specific, and more actionable.
- Do not invent requirements the user did not request.
- Do not answer the underlying task — produce an improved prompt the user can paste elsewhere.
- Return ONLY the upgraded prompt text. No preamble, no analysis, no markdown fences unless the prompt itself needs them."""

REFEREE_SYSTEM = """You are the Prompt Builder referee.
You receive independent prompt-improvement proposals from council models and must synthesize ONE best improved prompt.

Rules:
- Preserve the user's important intent and constraints from the Prompt Builder conversation.
- Prefer clarity, actionability, and fidelity to refinement instructions over verbosity.
- Do not invent requirements the user did not request.
- Do not answer the underlying task — produce a usable final prompt.
- Return ONLY the final improved prompt text. No preamble, no analysis, no list of proposals."""


@dataclass(frozen=True)
class _Proposal:
    model_id: str
    text: str


class PromptBuilderService:
    async def improve(
        self,
        _auth: AuthContext,
        raw_prompt: str | None,
    ) -> PromptBuilderImproveResponse:
        """Legacy one-shot improve (single model). Kept for backward compatibility."""
        prompt = (raw_prompt or "").strip()
        if not prompt:
            raise ValidationError("raw_prompt is required and cannot be empty")
        if len(prompt) > MAX_MESSAGE_CHARS:
            raise ValidationError(f"raw_prompt must be {MAX_MESSAGE_CHARS} characters or fewer")

        providers = get_provider_registry()
        providers.validate_configured()

        model = get_model(DEFAULT_BRAIN_MODEL)
        provider = providers.get_provider(model.provider)
        response = await provider.complete(
            system=COUNCIL_SYSTEM,
            user=prompt,
            model=model.provider_model,
            max_tokens=COUNCIL_MAX_TOKENS,
        )
        improved = response.text.strip()
        if not improved:
            raise AppError("Prompt improvement failed", code="LLM_ERROR")
        return PromptBuilderImproveResponse(improved_prompt=improved)

    async def refine(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        messages: list[PromptBuilderRefineMessage],
        model_set_id: str,
    ) -> PromptBuilderRefineResponse:
        normalized = self._validate_messages(messages)
        model_set = await self._resolve_model_set(db, auth, model_set_id)

        providers = get_provider_registry()
        providers.validate_configured()

        transcript = self._format_transcript(normalized)
        proposals = await self._run_council(
            providers,
            model_ids=list(model_set.models),
            transcript=transcript,
        )
        if not proposals:
            raise AppError(
                "Prompt Builder council failed to produce any proposals",
                code="LLM_ERROR",
            )

        improved = await self._run_referee(
            providers,
            verdict_model_id=model_set.verdict_model,
            transcript=transcript,
            proposals=proposals,
        )
        return PromptBuilderRefineResponse(
            assistant_message=improved,
            improved_prompt=improved,
        )

    def _validate_messages(
        self, messages: list[PromptBuilderRefineMessage]
    ) -> list[PromptBuilderRefineMessage]:
        if not messages:
            raise ValidationError("messages must contain at least one message")
        if len(messages) > MAX_MESSAGES:
            raise ValidationError(f"messages must contain at most {MAX_MESSAGES} items")

        normalized: list[PromptBuilderRefineMessage] = []
        total_chars = 0
        for index, item in enumerate(messages):
            role = (item.role or "").strip().lower()
            content = (item.content or "").strip()
            if role not in ALLOWED_ROLES:
                raise ValidationError(
                    f"messages[{index}].role must be one of: user, assistant"
                )
            if not content:
                raise ValidationError(f"messages[{index}].content cannot be empty")
            if len(content) > MAX_MESSAGE_CHARS:
                raise ValidationError(
                    f"messages[{index}].content must be {MAX_MESSAGE_CHARS} characters or fewer"
                )
            total_chars += len(content)
            if total_chars > MAX_TOTAL_CHARS:
                raise ValidationError(
                    f"messages total content must be {MAX_TOTAL_CHARS} characters or fewer"
                )
            normalized.append(PromptBuilderRefineMessage(role=role, content=content))

        if normalized[-1].role != "user":
            raise ValidationError("messages must end with a user message")
        return normalized

    async def _resolve_model_set(
        self, db: AsyncSession, auth: AuthContext, model_set_id: str
    ) -> ModelSet:
        slug = (model_set_id or "").strip()
        if not slug:
            raise ValidationError("model_set_id is required")
        result = await db.execute(
            select(ModelSet).where(
                ModelSet.slug == slug,
                (ModelSet.org_id == auth.org_id) | (ModelSet.is_system.is_(True)),
            )
        )
        model_set = result.scalar_one_or_none()
        if model_set is None:
            raise NotFoundError("ModelSet", slug)
        if not model_set.models:
            raise ValidationError("Model set has no council models")
        if not (model_set.verdict_model or "").strip():
            raise ValidationError("Model set has no verdict model")
        return model_set

    @staticmethod
    def _format_transcript(messages: list[PromptBuilderRefineMessage]) -> str:
        parts: list[str] = []
        for message in messages:
            label = "User" if message.role == "user" else "Improved prompt"
            parts.append(f"{label}:\n{message.content}")
        return "\n\n".join(parts)

    async def _run_council(
        self,
        providers: Any,
        *,
        model_ids: list[str],
        transcript: str,
    ) -> list[_Proposal]:
        user_payload = (
            "## Prompt Builder conversation\n"
            f"{transcript}\n\n"
            "Return only your improved prompt."
        )

        async def call_one(model_id: str) -> _Proposal | None:
            try:
                model = get_model(model_id)
                provider = providers.get_provider(model.provider)
                response: LLMResponse = await provider.complete(
                    system=COUNCIL_SYSTEM,
                    user=user_payload,
                    model=model.provider_model,
                    max_tokens=COUNCIL_MAX_TOKENS,
                )
                text = (response.text or "").strip()
                if not text:
                    logger.warning("prompt_builder_council_empty", model_id=model_id)
                    return None
                return _Proposal(model_id=model_id, text=text)
            except Exception as exc:  # noqa: BLE001 - collect survivors
                logger.warning(
                    "prompt_builder_council_model_failed",
                    model_id=model_id,
                    error=str(exc),
                )
                return None

        results = await asyncio.gather(*(call_one(mid) for mid in model_ids))
        return [item for item in results if item is not None]

    async def _run_referee(
        self,
        providers: Any,
        *,
        verdict_model_id: str,
        transcript: str,
        proposals: list[_Proposal],
    ) -> str:
        proposal_blocks = []
        for index, proposal in enumerate(proposals, start=1):
            proposal_blocks.append(
                f"### Proposal {index} ({proposal.model_id})\n{proposal.text}"
            )
        user_payload = (
            "## Prompt Builder conversation\n"
            f"{transcript}\n\n"
            "## Council proposals\n"
            + "\n\n".join(proposal_blocks)
            + "\n\nSynthesize the single best improved prompt now."
        )

        try:
            model = get_model(verdict_model_id)
            provider = providers.get_provider(model.provider)
            response = await provider.complete(
                system=REFEREE_SYSTEM,
                user=user_payload,
                model=model.provider_model,
                max_tokens=REFEREE_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("prompt_builder_referee_failed", error=str(exc))
            raise AppError("Prompt Builder referee failed", code="LLM_ERROR") from exc

        improved = (response.text or "").strip()
        if not improved:
            raise AppError("Prompt Builder referee returned an empty prompt", code="LLM_ERROR")
        return improved


prompt_builder_service = PromptBuilderService()
