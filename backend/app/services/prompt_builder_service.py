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
from app.llm.catalog import estimate_tokens, get_model
from app.llm.pricing import get_pricing_service
from app.llm.providers import LLMResponse, get_provider_registry
from app.schemas.api import (
    PromptBuilderContextResponse,
    PromptBuilderContextUsage,
    PromptBuilderImproveResponse,
    PromptBuilderRefineMessage,
    PromptBuilderRefineResponse,
)
from app.services.brain_service import DEFAULT_BRAIN_MODEL

logger = get_logger(__name__)

ALLOWED_ROLES = frozenset({"user", "assistant"})
PROMPT_BUILDER_MAX_OUTPUT_TOKENS = 20_000
COUNCIL_MAX_TOKENS = PROMPT_BUILDER_MAX_OUTPUT_TOKENS
REFEREE_MAX_TOKENS = PROMPT_BUILDER_MAX_OUTPUT_TOKENS
PROMPT_BUILDER_LLM_TIMEOUT_SECONDS = 300.0
OUTPUT_LIMIT_ERROR = (
    "Prompt Builder reached the model's output limit before completing the prompt. "
    "Your original prompt and Builder history are still saved."
)

CLARIFY_PREFIX = "CLARIFY:"

COUNCIL_SYSTEM = """You are a member of MultiMind's Prompt Builder council, acting as an expert prompt engineer.
Your only job is to turn the user's idea and the Prompt Builder conversation into a single upgraded prompt the user can paste into another AI.

When they genuinely help the result, draw on:
- A clear objective (what the output should accomplish).
- Relevant role or expertise framing for the AI that will receive the prompt.
- Necessary context the user has already given.
- Specific, actionable instructions.
- Constraints the user stated or clearly implied.
- Expected output format.
- A quality bar or success criteria.

Match effort to the request: a simple request deserves a short, direct prompt. Only add structure
(headers, numbered constraints, a format section) when the request is complex enough to need it —
never pad a simple prompt with sections it doesn't need.

Rules:
- Preserve the user's actual intent and every constraint they gave.
- Incorporate refinement instructions from later user messages.
- Never invent requirements, facts, or constraints the user did not request or clearly imply.
- Do not answer the underlying task — produce an improved prompt, not a response to it.
- Return ONLY the upgraded prompt text. No preamble, no analysis, no markdown fences unless the prompt itself needs them."""

COUNCIL_SYSTEM_FIRST_TURN = (
    COUNCIL_SYSTEM
    + f"""

Exception for this first message only: if the idea is genuinely too vague to produce a useful
prompt (for example it has no clear subject, audience, or goal), you may instead ask up to two
short, targeted clarifying questions. If you do, start your entire response with the exact token
"{CLARIFY_PREFIX}" followed by the questions and nothing else. Only ask when truly necessary —
if you can reasonably infer what's needed, produce the improved prompt directly instead."""
)

REFEREE_SYSTEM = """You are the Prompt Builder referee, an expert prompt engineer.
You receive independent prompt-improvement proposals from council models and must synthesize ONE best improved prompt.

Rules:
- Preserve the user's important intent and constraints from the Prompt Builder conversation.
- Prefer clarity, actionability, and fidelity to refinement instructions over verbosity.
- Keep simple requests short; only add structure (role, context, constraints, output format,
  quality bar) when the request's complexity genuinely calls for it.
- Never invent requirements, facts, or constraints the user did not request or clearly imply.
- Do not answer the underlying task — produce a usable final prompt.
- Return ONLY the final improved prompt text. No preamble, no analysis, no list of proposals."""

REFEREE_SYSTEM_FIRST_TURN = (
    REFEREE_SYSTEM
    + f"""

Exception for this first message only: if the proposals agree the idea is too vague to produce a
useful prompt, you may synthesize up to two short clarifying questions instead. If you do, start
your entire response with the exact token "{CLARIFY_PREFIX}" followed by the questions and nothing
else. Prefer producing the improved prompt directly whenever reasonably possible."""
)


@dataclass(frozen=True)
class _Proposal:
    model_id: str
    text: str
    tokens_input: int
    output_limit: int


@dataclass(frozen=True)
class _CallBudget:
    model_id: str
    model_name: str
    call: str
    estimated_input_tokens: int
    context_limit: int
    reserved_output_tokens: int

    @property
    def remaining_tokens(self) -> int:
        return self.context_limit - self.estimated_input_tokens - self.reserved_output_tokens


class PromptBuilderService:
    async def context(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        messages: list[PromptBuilderRefineMessage],
        model_set_id: str,
    ) -> PromptBuilderContextResponse:
        normalized = self._validate_messages(messages, require_user_last=False)
        model_set = await self._resolve_model_set(db, auth, model_set_id)
        is_first_turn = len(normalized) == 1
        council_system = COUNCIL_SYSTEM_FIRST_TURN if is_first_turn else COUNCIL_SYSTEM
        referee_system = REFEREE_SYSTEM_FIRST_TURN if is_first_turn else REFEREE_SYSTEM
        budgets = await self._projected_budgets(
            model_set,
            self._format_transcript(normalized),
            council_system=council_system,
            referee_system=referee_system,
        )
        limiting = min(budgets, key=lambda budget: budget.remaining_tokens)
        return PromptBuilderContextResponse(context_usage=self._usage(limiting))

    async def improve(
        self,
        _auth: AuthContext,
        raw_prompt: str | None,
    ) -> PromptBuilderImproveResponse:
        """Legacy one-shot improve (single model). Kept for backward compatibility."""
        prompt = (raw_prompt or "").strip()
        if not prompt:
            raise ValidationError("raw_prompt is required and cannot be empty")
        providers = get_provider_registry()
        providers.validate_configured()

        model = get_model(DEFAULT_BRAIN_MODEL)
        output_limit = await self._effective_output_tokens(DEFAULT_BRAIN_MODEL)
        provider = providers.get_provider(model.provider)
        response = await provider.complete(
            system=COUNCIL_SYSTEM,
            user=prompt,
            model=model.provider_model,
            max_tokens=output_limit,
            timeout=PROMPT_BUILDER_LLM_TIMEOUT_SECONDS,
        )
        self._raise_if_output_truncated(response)
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
        is_first_turn = len(normalized) == 1
        council_system = COUNCIL_SYSTEM_FIRST_TURN if is_first_turn else COUNCIL_SYSTEM
        referee_system = REFEREE_SYSTEM_FIRST_TURN if is_first_turn else REFEREE_SYSTEM

        providers = get_provider_registry()
        providers.validate_configured()

        transcript = self._format_transcript(normalized)
        projected = await self._projected_budgets(
            model_set, transcript, council_system=council_system, referee_system=referee_system
        )
        self._raise_if_overflow(projected)
        proposals = await self._run_council(
            providers,
            model_ids=list(model_set.models),
            transcript=transcript,
            system=council_system,
        )
        if not proposals:
            raise AppError(
                "Prompt Builder council failed to produce any proposals",
                code="LLM_ERROR",
            )

        referee_budget = await self._referee_budget(
            model_set.verdict_model, transcript, proposals, referee_system
        )
        self._raise_if_overflow([referee_budget])
        raw, referee_tokens = await self._run_referee(
            providers,
            verdict_model_id=model_set.verdict_model,
            transcript=transcript,
            proposals=proposals,
            system=referee_system,
        )
        actual_budgets = [
            await self._actual_budget(p.model_id, "council", p.tokens_input, p.output_limit)
            for p in proposals
        ]
        actual_budgets.append(
            await self._actual_budget(
                model_set.verdict_model,
                "referee",
                referee_tokens,
                referee_budget.reserved_output_tokens,
            )
        )
        limiting = min(actual_budgets, key=lambda budget: budget.remaining_tokens)
        usage = self._usage(limiting, actual=limiting.estimated_input_tokens)

        is_clarifying = is_first_turn and raw.strip().startswith(CLARIFY_PREFIX)
        question_text = raw.strip()[len(CLARIFY_PREFIX) :].strip() if is_clarifying else ""
        if is_clarifying and not question_text:
            is_clarifying = False

        if is_clarifying:
            return PromptBuilderRefineResponse(
                assistant_message=question_text,
                improved_prompt=None,
                is_clarifying_question=True,
                context_usage=usage,
            )
        return PromptBuilderRefineResponse(
            assistant_message=raw,
            improved_prompt=raw,
            is_clarifying_question=False,
            context_usage=usage,
        )

    def _validate_messages(
        self,
        messages: list[PromptBuilderRefineMessage],
        *,
        require_user_last: bool = True,
    ) -> list[PromptBuilderRefineMessage]:
        if not messages:
            raise ValidationError("messages must contain at least one message")
        normalized: list[PromptBuilderRefineMessage] = []
        for index, item in enumerate(messages):
            role = (item.role or "").strip().lower()
            content = item.content or ""
            if role not in ALLOWED_ROLES:
                raise ValidationError(f"messages[{index}].role must be one of: user, assistant")
            if not content.strip():
                raise ValidationError(f"messages[{index}].content cannot be empty")
            normalized.append(PromptBuilderRefineMessage(role=role, content=content))

        if require_user_last and normalized[-1].role != "user":
            raise ValidationError("messages must end with a user message")
        return normalized

    async def _model_limits(self, model_id: str) -> tuple[int, str, int]:
        pricing = get_pricing_service()
        await pricing.ensure_loaded()
        model = get_model(model_id)
        metadata = pricing.get_slug_metadata(model.provider_model) or {}
        value = metadata.get("context_length")
        if not isinstance(value, int) or value <= 0:
            raise AppError(
                f"Context length is unavailable for {model.name}.",
                code="CONTEXT_METADATA_UNAVAILABLE",
            )
        return value, model.name, self._effective_output_tokens_from_metadata(metadata)

    async def _effective_output_tokens(self, model_id: str) -> int:
        pricing = get_pricing_service()
        await pricing.ensure_loaded()
        model = get_model(model_id)
        metadata = pricing.get_slug_metadata(model.provider_model) or {}
        return self._effective_output_tokens_from_metadata(metadata)

    @staticmethod
    def _effective_output_tokens_from_metadata(metadata: dict[str, Any]) -> int:
        top_provider = metadata.get("top_provider")
        provider_output_limit = (
            top_provider.get("max_completion_tokens") if isinstance(top_provider, dict) else None
        )
        if not isinstance(provider_output_limit, int) or provider_output_limit <= 0:
            provider_output_limit = metadata.get("max_completion_tokens")
        if isinstance(provider_output_limit, int) and provider_output_limit > 0:
            return min(PROMPT_BUILDER_MAX_OUTPUT_TOKENS, provider_output_limit)
        return PROMPT_BUILDER_MAX_OUTPUT_TOKENS

    @staticmethod
    def _estimated_input(system: str, user: str) -> int:
        return estimate_tokens(f"system:\n{system}\n\nuser:\n{user}")

    async def _projected_budgets(
        self,
        model_set: ModelSet,
        transcript: str,
        *,
        council_system: str,
        referee_system: str,
    ) -> list[_CallBudget]:
        council_ids = list(model_set.models)
        council_user = self._council_user_payload(transcript)
        budgets: list[_CallBudget] = []
        for model_id in council_ids:
            limit, name, output_limit = await self._model_limits(model_id)
            budgets.append(
                _CallBudget(
                    model_id,
                    name,
                    "council",
                    self._estimated_input(council_system, council_user),
                    limit,
                    output_limit,
                )
            )

        # Referee preflight must be safe before proposal text exists. Reserve each
        # council model's full configured output; no history is compacted or removed.
        placeholder_proposals: list[_Proposal] = []
        for model_id in council_ids:
            output_limit = (await self._model_limits(model_id))[2]
            placeholder_proposals.append(
                _Proposal(model_id, "x" * (output_limit * 4), 0, output_limit)
            )
        budgets.append(
            await self._referee_budget(
                model_set.verdict_model, transcript, placeholder_proposals, referee_system
            )
        )
        return budgets

    async def _referee_budget(
        self, model_id: str, transcript: str, proposals: list[_Proposal], referee_system: str
    ) -> _CallBudget:
        limit, name, output_limit = await self._model_limits(model_id)
        return _CallBudget(
            model_id,
            name,
            "referee",
            self._estimated_input(
                referee_system, self._referee_user_payload(transcript, proposals)
            ),
            limit,
            output_limit,
        )

    async def _actual_budget(
        self, model_id: str, call: str, tokens: int, reserved: int
    ) -> _CallBudget:
        limit, name, _output_limit = await self._model_limits(model_id)
        return _CallBudget(model_id, name, call, tokens, limit, reserved)

    @staticmethod
    def _usage(budget: _CallBudget, *, actual: int | None = None) -> PromptBuilderContextUsage:
        return PromptBuilderContextUsage(
            estimated_input_tokens=budget.estimated_input_tokens,
            actual_input_tokens=actual,
            context_limit=budget.context_limit,
            reserved_output_tokens=budget.reserved_output_tokens,
            remaining_tokens=budget.remaining_tokens,
            limiting_model_id=budget.model_id,
            limiting_model_name=budget.model_name,
            limiting_call=budget.call,
            is_estimate=actual is None,
        )

    @staticmethod
    def _raise_if_overflow(budgets: list[_CallBudget]) -> None:
        if any(budget.remaining_tokens < 0 for budget in budgets):
            raise AppError(
                "Context limit reached. Your complete Prompt Builder history is still saved. Nothing was deleted. Start a new Builder session or use a model with a larger context window.",
                code="PROMPT_BUILDER_CONTEXT_LIMIT",
            )

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
        system: str,
    ) -> list[_Proposal]:
        user_payload = self._council_user_payload(transcript)

        async def call_one(model_id: str) -> _Proposal | None:
            try:
                model = get_model(model_id)
                output_limit = (await self._model_limits(model_id))[2]
                provider = providers.get_provider(model.provider)
                response: LLMResponse = await provider.complete(
                    system=system,
                    user=user_payload,
                    model=model.provider_model,
                    max_tokens=output_limit,
                    preserve_whitespace=True,
                    timeout=PROMPT_BUILDER_LLM_TIMEOUT_SECONDS,
                )
                text = response.text or ""
                self._raise_if_output_truncated(response)
                if not text.strip():
                    logger.warning("prompt_builder_council_empty", model_id=model_id)
                    return None
                return _Proposal(
                    model_id=model_id,
                    text=text,
                    tokens_input=response.tokens_input,
                    output_limit=output_limit,
                )
            except Exception as exc:
                if isinstance(exc, AppError) and exc.code == "PROMPT_BUILDER_OUTPUT_LIMIT":
                    raise
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
        system: str,
    ) -> tuple[str, int]:
        user_payload = self._referee_user_payload(transcript, proposals)

        try:
            model = get_model(verdict_model_id)
            output_limit = (await self._model_limits(verdict_model_id))[2]
            provider = providers.get_provider(model.provider)
            response = await provider.complete(
                system=system,
                user=user_payload,
                model=model.provider_model,
                max_tokens=output_limit,
                preserve_whitespace=True,
                timeout=PROMPT_BUILDER_LLM_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("prompt_builder_referee_failed", error=str(exc))
            raise AppError("Prompt Builder referee failed", code="LLM_ERROR") from exc

        improved = response.text or ""
        self._raise_if_output_truncated(response)
        if not improved.strip():
            raise AppError("Prompt Builder referee returned an empty prompt", code="LLM_ERROR")
        return improved, response.tokens_input

    @staticmethod
    def _raise_if_output_truncated(response: LLMResponse) -> None:
        reason = (response.finish_reason or "").strip().lower()
        if reason in {
            "length",
            "max_tokens",
            "max_output_tokens",
            "max_completion_tokens",
            "max_output_length",
        }:
            raise AppError(OUTPUT_LIMIT_ERROR, code="PROMPT_BUILDER_OUTPUT_LIMIT")

    @staticmethod
    def _council_user_payload(transcript: str) -> str:
        return f"## Prompt Builder conversation\n{transcript}\n\nReturn only your improved prompt."

    @staticmethod
    def _referee_user_payload(transcript: str, proposals: list[_Proposal]) -> str:
        proposal_blocks = []
        for index, proposal in enumerate(proposals, start=1):
            proposal_blocks.append(f"### Proposal {index} ({proposal.model_id})\n{proposal.text}")
        return (
            "## Prompt Builder conversation\n"
            f"{transcript}\n\n"
            "## Council proposals\n"
            + "\n\n".join(proposal_blocks)
            + "\n\nSynthesize the single best improved prompt now."
        )


prompt_builder_service = PromptBuilderService()
