"""Multi-model turn orchestrator — parallel answers, verdict, decision insurance."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    CostRecord,
    DecisionInsurance,
    ModelAnswer,
    ModelAnswerStatus,
    Strategy,
    Turn,
    TurnStatus,
    UsageKind,
    Verdict,
)
from app.llm.catalog import get_model, resolve_llm_cost
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry

logger = get_logger(__name__)

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class TurnContext:
    turn_id: str
    chat_id: str
    org_id: str
    project_id: str | None
    user_message: str
    model_ids: list[str]
    verdict_model_id: str
    strategy: Strategy
    model_set_name: str
    custom_instructions: str | None = None
    template_instructions: str | None = None
    user_brain_context: str | None = None
    decision_insurance_enabled: bool = False
    skip_answer_seed: bool = False


@dataclass
class OrchestratorResult:
    model_answers: list[ModelAnswer] = field(default_factory=list)
    verdict: Verdict | None = None
    decision_insurance: DecisionInsurance | None = None
    cost_records: list[CostRecord] = field(default_factory=list)


def format_llm_error(exc: Exception) -> str:
    """Surface the underlying OpenRouter message instead of opaque RetryError text."""
    if isinstance(exc, RetryError) and exc.last_attempt.failed:
        inner = exc.last_attempt.exception()
        if inner is not None:
            return str(inner)
    return str(exc)


class TurnOrchestrator:
    """Enterprise orchestration engine for multi-model turns."""

    def __init__(self) -> None:
        self._prompts = get_prompt_engine()
        self._providers = get_provider_registry()

    async def run(
        self,
        db: AsyncSession,
        ctx: TurnContext,
        on_event: EventCallback | None = None,
    ) -> OrchestratorResult:
        turn = await db.get(Turn, ctx.turn_id)
        if turn is None:
            raise ValueError(f"Turn {ctx.turn_id} not found")

        turn.status = TurnStatus.RUNNING
        await db.flush()

        result = OrchestratorResult()
        answer_rows: dict[str, ModelAnswer] = {}
        db_lock = asyncio.Lock()

        if ctx.skip_answer_seed:
            from sqlalchemy import select

            existing = await db.execute(
                select(ModelAnswer).where(ModelAnswer.turn_id == ctx.turn_id)
            )
            for row in existing.scalars().all():
                answer_rows[row.model_id] = row
        else:
            for model_id in ctx.model_ids:
                row = ModelAnswer(
                    turn_id=ctx.turn_id,
                    model_id=model_id,
                    status=ModelAnswerStatus.PENDING,
                )
                db.add(row)
                answer_rows[model_id] = row
            await db.flush()

        async def emit(event: str, data: dict[str, Any]) -> None:
            if on_event:
                await on_event(event, data)

        # Phase 1: parallel model answers
        await emit("turn_started", {"turn_id": str(ctx.turn_id), "models": ctx.model_ids})

        async def call_model(model_id: str) -> None:
            model = get_model(model_id)
            row = answer_rows[model_id]
            async with db_lock:
                row.status = ModelAnswerStatus.RUNNING
                await db.flush()
            await emit("model_answer_started", {"model_id": model_id})

            system = self._prompts.model_answer_prompt(
                user_message=ctx.user_message,
                model_id=model.id,
                model_name=model.name,
                vendor=model.vendor,
                model_set_name=ctx.model_set_name,
                custom_instructions=ctx.custom_instructions,
                template_instructions=ctx.template_instructions,
                user_brain_context=ctx.user_brain_context,
            )

            try:
                provider = self._providers.get_provider(model.provider)
                response = await provider.complete(
                    system=system,
                    user=ctx.user_message,
                    model=model.provider_model,
                    max_tokens=4096,
                )
                row.text = response.text
                row.confidence = response.confidence or 85
                row.tokens_input = response.tokens_input
                row.tokens_output = response.tokens_output
                row.cost_usd = resolve_llm_cost(
                    model_id,
                    response.tokens_input,
                    response.tokens_output,
                    response.cost_usd,
                )
                row.status = ModelAnswerStatus.COMPLETED

                cost = CostRecord(
                    org_id=ctx.org_id,
                    chat_id=ctx.chat_id,
                    project_id=ctx.project_id,
                    turn_id=ctx.turn_id,
                    model_id=model_id,
                    kind=UsageKind.ANSWER,
                    tokens_input=response.tokens_input,
                    tokens_output=response.tokens_output,
                    cost_usd=row.cost_usd,
                )
                async with db_lock:
                    db.add(cost)
                    result.cost_records.append(cost)
                    result.model_answers.append(row)
                    await db.flush()
                await emit(
                    "model_answer_completed",
                    {
                        "model_id": model_id,
                        "model_name": model.name,
                        "text": row.text,
                        "confidence": row.confidence,
                        "tokens_input": row.tokens_input,
                        "tokens_output": row.tokens_output,
                        "cost_usd": row.cost_usd,
                    },
                )
            except Exception as exc:
                message = format_llm_error(exc)
                logger.warning("model_answer_failed", model_id=model_id, error=message)
                row.status = ModelAnswerStatus.FAILED
                row.error_message = message
                async with db_lock:
                    await db.flush()
                await emit(
                    "model_answer_failed",
                    {"model_id": model_id, "error": message},
                )

        await asyncio.gather(*(call_model(mid) for mid in ctx.model_ids))

        # Build answer context for verdict
        answer_context = []
        for model_id in ctx.model_ids:
            row = answer_rows[model_id]
            model = get_model(model_id)
            answer_context.append(
                {
                    "model_id": model_id,
                    "model_name": model.name,
                    "text": row.text or "",
                    "confidence": row.confidence or 0,
                    "failed": row.status == ModelAnswerStatus.FAILED,
                    "error_message": row.error_message,
                }
            )

        successful = [a for a in answer_context if not a["failed"]]
        if not successful:
            turn.status = TurnStatus.FAILED
            turn.error_message = "All models failed to respond"
            await db.flush()
            await emit("turn_failed", {"error": turn.error_message})
            return result

        # Phase 2: Verdict
        await emit("verdict_started", {"model_id": ctx.verdict_model_id})

        verdict_system = self._prompts.verdict_prompt(
            strategy=ctx.strategy.value,
            user_message=ctx.user_message,
            model_answers=answer_context,
            custom_instructions=ctx.custom_instructions,
            template_instructions=ctx.template_instructions,
            user_brain_context=ctx.user_brain_context,
        )

        verdict_model = get_model(ctx.verdict_model_id)
        provider = self._providers.get_provider(verdict_model.provider)

        try:
            verdict_response = await provider.complete(
                system=verdict_system,
                user="Produce the verdict JSON now.",
                model=verdict_model.provider_model,
                max_tokens=2048,
            )
            parsed = provider.parse_json_response(verdict_response.text)

            verdict_row = Verdict(
                turn_id=ctx.turn_id,
                model_id=ctx.verdict_model_id,
                strategy=ctx.strategy,
                text=parsed.get("text", verdict_response.text),
                reason=parsed.get("reason", "Synthesized from model responses."),
                tokens_input=verdict_response.tokens_input,
                tokens_output=verdict_response.tokens_output,
                cost_usd=resolve_llm_cost(
                    ctx.verdict_model_id,
                    verdict_response.tokens_input,
                    verdict_response.tokens_output,
                    verdict_response.cost_usd,
                ),
            )
            db.add(verdict_row)
            result.verdict = verdict_row

            cost = CostRecord(
                org_id=ctx.org_id,
                chat_id=ctx.chat_id,
                project_id=ctx.project_id,
                turn_id=ctx.turn_id,
                model_id=ctx.verdict_model_id,
                kind=UsageKind.VERDICT,
                tokens_input=verdict_response.tokens_input,
                tokens_output=verdict_response.tokens_output,
                cost_usd=verdict_row.cost_usd,
            )
            db.add(cost)
            result.cost_records.append(cost)

            await emit(
                "verdict_completed",
                {
                    "model_id": ctx.verdict_model_id,
                    "strategy": ctx.strategy.value,
                    "text": verdict_row.text,
                    "reason": verdict_row.reason,
                    "tokens_input": verdict_row.tokens_input,
                    "tokens_output": verdict_row.tokens_output,
                    "cost_usd": verdict_row.cost_usd,
                },
            )
        except Exception as exc:
            message = format_llm_error(exc)
            logger.error("verdict_failed", error=message)
            turn.status = TurnStatus.FAILED
            turn.error_message = f"Verdict generation failed: {message}"
            await db.flush()
            await emit("turn_failed", {"error": turn.error_message})
            return result

        # Phase 3: Decision Insurance (always after a successful verdict)
        if result.verdict:
            await emit("decision_insurance_started", {})

            insurance_system = self._prompts.decision_insurance_prompt(
                user_message=ctx.user_message,
                strategy=ctx.strategy.value,
                model_answers=answer_context,
                verdict_text=result.verdict.text,
                verdict_reason=result.verdict.reason,
            )

            try:
                insurance_response = await provider.complete(
                    system=insurance_system,
                    user="Produce the decision insurance JSON now.",
                    model=verdict_model.provider_model,
                    max_tokens=2048,
                )
                insurance_data = provider.parse_json_response(insurance_response.text)

                insurance_row = DecisionInsurance(
                    turn_id=ctx.turn_id,
                    best_case=insurance_data["best_case"],
                    worst_case=insurance_data["worst_case"],
                    risk_level=insurance_data["risk_level"],
                    potential_loss=insurance_data["potential_loss"],
                    mitigation_plan=insurance_data["mitigation_plan"],
                    tokens_input=insurance_response.tokens_input,
                    tokens_output=insurance_response.tokens_output,
                    cost_usd=resolve_llm_cost(
                        ctx.verdict_model_id,
                        insurance_response.tokens_input,
                        insurance_response.tokens_output,
                        insurance_response.cost_usd,
                    ),
                )
                db.add(insurance_row)
                result.decision_insurance = insurance_row

                cost = CostRecord(
                    org_id=ctx.org_id,
                    chat_id=ctx.chat_id,
                    project_id=ctx.project_id,
                    turn_id=ctx.turn_id,
                    model_id=ctx.verdict_model_id,
                    kind=UsageKind.INSURANCE,
                    tokens_input=insurance_response.tokens_input,
                    tokens_output=insurance_response.tokens_output,
                    cost_usd=insurance_row.cost_usd,
                )
                db.add(cost)
                result.cost_records.append(cost)

                await emit(
                    "decision_insurance_completed",
                    {
                        "best_case": insurance_row.best_case,
                        "worst_case": insurance_row.worst_case,
                        "risk_level": insurance_row.risk_level,
                        "potential_loss": insurance_row.potential_loss,
                        "mitigation_plan": insurance_row.mitigation_plan,
                        "tokens_input": insurance_row.tokens_input,
                        "tokens_output": insurance_row.tokens_output,
                        "cost_usd": insurance_row.cost_usd,
                    },
                )
            except Exception as exc:
                logger.warning("decision_insurance_failed", error=str(exc))

        failed_count = sum(1 for a in answer_context if a["failed"])
        turn.status = TurnStatus.PARTIAL if failed_count else TurnStatus.COMPLETED
        await db.flush()

        await emit(
            "turn_completed",
            {"turn_id": str(ctx.turn_id), "status": turn.status.value},
        )
        return result


_orchestrator: TurnOrchestrator | None = None


def get_orchestrator() -> TurnOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TurnOrchestrator()
    return _orchestrator
