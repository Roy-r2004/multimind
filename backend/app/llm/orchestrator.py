"""Multi-model turn orchestrator — parallel answers and verdict."""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import exists, select, update
from tenacity import RetryError

from app.core.logging import get_logger
from app.db.models import (
    CostRecord,
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
ACTIVE_TURN_STATUSES = (TurnStatus.PENDING, TurnStatus.RUNNING)
CANCELLATION_POLL_INTERVAL_SECONDS = 0.5


class TurnCancellationDetected(Exception):
    """Raised internally when a turn was cancelled or deleted durably."""


async def is_turn_cancel_requested_or_deleted(db: AsyncSession, turn_id: str) -> bool:
    result = await db.execute(
        select(Turn.id, Turn.cancel_requested_at)
        .where(Turn.id == turn_id)
        .execution_options(populate_existing=True)
    )
    row = result.one_or_none()
    return row is None or row.cancel_requested_at is not None


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
    previous_verdict_context: str | None = None
    skip_answer_seed: bool = False


@dataclass
class OrchestratorResult:
    model_answers: list[ModelAnswer] = field(default_factory=list)
    verdict: Verdict | None = None
    cost_records: list[CostRecord] = field(default_factory=list)


@dataclass
class ModelCallResult:
    model_id: str
    model_name: str
    response: Any | None = None
    error: Exception | None = None


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

    async def _ensure_not_cancelled(self, db: AsyncSession, turn_id: str) -> None:
        if await is_turn_cancel_requested_or_deleted(db, turn_id):
            raise TurnCancellationDetected

    def _active_turn_exists(self, turn_id: str):
        return exists().where(
            Turn.id == turn_id,
            Turn.cancel_requested_at.is_(None),
            Turn.status.in_(ACTIVE_TURN_STATUSES),
        )

    async def _lock_active_turn_for_persistence(self, db: AsyncSession, turn_id: str) -> None:
        result = await db.execute(
            select(Turn.id)
            .where(
                Turn.id == turn_id,
                Turn.cancel_requested_at.is_(None),
                Turn.status.in_(ACTIVE_TURN_STATUSES),
            )
            .with_for_update()
        )
        if result.scalar_one_or_none() is None:
            raise TurnCancellationDetected

    async def _fresh_cancellation_check(self, turn_id: str) -> bool:
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as cancellation_db:
            return await is_turn_cancel_requested_or_deleted(cancellation_db, turn_id)

    async def _await_provider_complete(
        self,
        turn_id: str,
        provider_call: Awaitable[Any],
    ) -> Any:
        provider_task = asyncio.create_task(provider_call)
        try:
            while True:
                done, _ = await asyncio.wait(
                    {provider_task},
                    timeout=CANCELLATION_POLL_INTERVAL_SECONDS,
                )
                if provider_task in done:
                    return await provider_task
                if await self._fresh_cancellation_check(turn_id):
                    provider_task.cancel()
                    await asyncio.gather(provider_task, return_exceptions=True)
                    raise TurnCancellationDetected
        except asyncio.CancelledError:
            if not provider_task.done():
                provider_task.cancel()
                await asyncio.gather(provider_task, return_exceptions=True)
            raise

    async def _get_answer(
        self, db: AsyncSession, turn_id: str, model_id: str
    ) -> ModelAnswer | None:
        result = await db.execute(
            select(ModelAnswer).where(
                ModelAnswer.turn_id == turn_id,
                ModelAnswer.model_id == model_id,
            )
        )
        return result.scalar_one_or_none()

    async def _get_answer_id(self, db: AsyncSession, turn_id: str, model_id: str) -> str | None:
        result = await db.execute(
            select(ModelAnswer.id).where(
                ModelAnswer.turn_id == turn_id,
                ModelAnswer.model_id == model_id,
            )
        )
        return result.scalar_one_or_none()

    async def run(
        self,
        db: AsyncSession,
        ctx: TurnContext,
        on_event: EventCallback | None = None,
    ) -> OrchestratorResult:
        async def emit(event: str, data: dict[str, Any]) -> None:
            if on_event:
                await on_event(event, data)

        async def rollback_quietly() -> None:
            try:
                await db.rollback()
            except Exception:
                logger.warning("orchestrator_rollback_failed", turn_id=ctx.turn_id)

        result = OrchestratorResult()

        # Short transaction: mark the turn and answer rows running, then release DB locks before
        # any external provider call.
        started = await db.execute(
            update(Turn)
            .where(
                Turn.id == ctx.turn_id,
                Turn.cancel_requested_at.is_(None),
                Turn.status == TurnStatus.PENDING,
            )
            .values(status=TurnStatus.RUNNING)
        )
        if started.rowcount != 1:
            await rollback_quietly()
            return result

        if ctx.skip_answer_seed:
            await db.execute(
                update(ModelAnswer)
                .where(
                    ModelAnswer.turn_id == ctx.turn_id,
                    ModelAnswer.model_id.in_(ctx.model_ids),
                    self._active_turn_exists(ctx.turn_id),
                )
                .values(status=ModelAnswerStatus.RUNNING)
            )
        else:
            await self._ensure_not_cancelled(db, ctx.turn_id)
            for model_id in ctx.model_ids:
                db.add(
                    ModelAnswer(
                        turn_id=ctx.turn_id,
                        model_id=model_id,
                        status=ModelAnswerStatus.RUNNING,
                    )
                )
        await db.commit()

        # Phase 1: parallel model answers
        try:
            await self._ensure_not_cancelled(db, ctx.turn_id)
        except TurnCancellationDetected:
            await rollback_quietly()
            return result

        await emit("turn_started", {"turn_id": str(ctx.turn_id), "models": ctx.model_ids})
        for model_id in ctx.model_ids:
            await emit("model_answer_started", {"model_id": model_id})

        async def call_model(model_id: str) -> ModelCallResult:
            await self._ensure_not_cancelled(db, ctx.turn_id)
            model = get_model(model_id)
            system = self._prompts.model_answer_prompt(
                user_message=ctx.user_message,
                model_id=model.id,
                model_name=model.name,
                vendor=model.vendor,
                model_set_name=ctx.model_set_name,
                custom_instructions=ctx.custom_instructions,
                template_instructions=ctx.template_instructions,
                user_brain_context=ctx.user_brain_context,
                previous_verdict_context=ctx.previous_verdict_context,
            )

            try:
                provider = self._providers.get_provider(model.provider)
                await self._ensure_not_cancelled(db, ctx.turn_id)
                response = await self._await_provider_complete(
                    ctx.turn_id,
                    provider.complete(
                        system=system,
                        user=ctx.user_message,
                        model=model.provider_model,
                        max_tokens=4096,
                    ),
                )
                await self._ensure_not_cancelled(db, ctx.turn_id)
                return ModelCallResult(model_id=model_id, model_name=model.name, response=response)
            except asyncio.CancelledError:
                raise
            except TurnCancellationDetected:
                raise
            except Exception as exc:
                return ModelCallResult(model_id=model_id, model_name=model.name, error=exc)

        async def persist_model_result(call_result: ModelCallResult) -> None:
            if call_result.error is not None:
                message = format_llm_error(call_result.error)
                logger.warning(
                    "model_answer_failed", model_id=call_result.model_id, error=message
                )
                await self._lock_active_turn_for_persistence(db, ctx.turn_id)
                updated = await db.execute(
                    update(ModelAnswer)
                    .where(
                        ModelAnswer.turn_id == ctx.turn_id,
                        ModelAnswer.model_id == call_result.model_id,
                    )
                    .values(status=ModelAnswerStatus.FAILED, error_message=message)
                )
                if updated.rowcount != 1:
                    await rollback_quietly()
                    return
                await db.commit()
                await emit(
                    "model_answer_failed",
                    {"model_id": call_result.model_id, "error": message},
                )
                return

            response = call_result.response
            if response is None:
                await rollback_quietly()
                return

            cost_usd = resolve_llm_cost(
                call_result.model_id,
                response.tokens_input,
                response.tokens_output,
                response.cost_usd,
            )
            await self._lock_active_turn_for_persistence(db, ctx.turn_id)
            updated = await db.execute(
                update(ModelAnswer)
                .where(
                    ModelAnswer.turn_id == ctx.turn_id,
                    ModelAnswer.model_id == call_result.model_id,
                )
                .values(
                    text=response.text,
                    confidence=response.confidence or 85,
                    tokens_input=response.tokens_input,
                    tokens_output=response.tokens_output,
                    cost_usd=cost_usd,
                    status=ModelAnswerStatus.COMPLETED,
                    error_message=None,
                )
            )
            if updated.rowcount != 1:
                await rollback_quietly()
                return

            cost = CostRecord(
                org_id=ctx.org_id,
                chat_id=ctx.chat_id,
                project_id=ctx.project_id,
                turn_id=ctx.turn_id,
                model_id=call_result.model_id,
                kind=UsageKind.ANSWER,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                cost_usd=cost_usd,
            )
            db.add(cost)
            result.cost_records.append(cost)
            await db.commit()
            await emit(
                "model_answer_completed",
                {
                    "model_id": call_result.model_id,
                    "model_name": call_result.model_name,
                    "text": response.text,
                    "confidence": response.confidence or 85,
                    "tokens_input": response.tokens_input,
                    "tokens_output": response.tokens_output,
                    "cost_usd": cost_usd,
                },
            )

        tasks = [asyncio.create_task(call_model(mid)) for mid in ctx.model_ids]
        try:
            for task in asyncio.as_completed(tasks):
                call_result = await task
                await persist_model_result(call_result)
        except TurnCancellationDetected:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await rollback_quietly()
            return result
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        try:
            await self._ensure_not_cancelled(db, ctx.turn_id)
        except TurnCancellationDetected:
            await rollback_quietly()
            return result

        fresh_answers = await db.execute(
            select(ModelAnswer).where(ModelAnswer.turn_id == ctx.turn_id)
        )
        answer_rows = {row.model_id: row for row in fresh_answers.scalars().all()}
        await db.commit()

        # Build answer context for verdict
        answer_context = []
        for model_id in ctx.model_ids:
            row = answer_rows.get(model_id)
            if row is None:
                return result
            model = get_model(model_id)
            answer_context.append(
                {
                    "model_id": model_id,
                    "model_name": model.name,
                    "text": row.text or "",
                    "confidence": row.confidence or 0,
                    "failed": row.status != ModelAnswerStatus.COMPLETED,
                    "error_message": row.error_message,
                }
            )

        successful = [a for a in answer_context if not a["failed"]]
        if not successful:
            try:
                await self._lock_active_turn_for_persistence(db, ctx.turn_id)
            except TurnCancellationDetected:
                await rollback_quietly()
                return result
            failed_update = await db.execute(
                update(Turn)
                .where(Turn.id == ctx.turn_id)
                .values(
                    status=TurnStatus.FAILED,
                    error_message="All models failed to respond",
                )
            )
            if failed_update.rowcount != 1:
                await rollback_quietly()
                return result
            await db.commit()
            await emit("turn_failed", {"error": "All models failed to respond"})
            return result

        # Phase 2: Verdict
        try:
            await self._ensure_not_cancelled(db, ctx.turn_id)
        except TurnCancellationDetected:
            await rollback_quietly()
            return result

        await emit("verdict_started", {"model_id": ctx.verdict_model_id})

        verdict_system = self._prompts.verdict_prompt(
            strategy=ctx.strategy.value,
            user_message=ctx.user_message,
            model_answers=answer_context,
            custom_instructions=ctx.custom_instructions,
            template_instructions=ctx.template_instructions,
            user_brain_context=ctx.user_brain_context,
            previous_verdict_context=ctx.previous_verdict_context,
        )

        verdict_model = get_model(ctx.verdict_model_id)
        provider = self._providers.get_provider(verdict_model.provider)

        try:
            await self._ensure_not_cancelled(db, ctx.turn_id)
            verdict_response = await self._await_provider_complete(
                ctx.turn_id,
                provider.complete(
                    system=verdict_system,
                    user="Produce the verdict JSON now.",
                    model=verdict_model.provider_model,
                    max_tokens=2048,
                ),
            )
            await self._ensure_not_cancelled(db, ctx.turn_id)
            parsed = provider.parse_json_response(verdict_response.text)

            failed_count = sum(1 for a in answer_context if a["failed"])
            final_status = TurnStatus.PARTIAL if failed_count else TurnStatus.COMPLETED
            await self._lock_active_turn_for_persistence(db, ctx.turn_id)
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
            await db.flush()

            await self._ensure_not_cancelled(db, ctx.turn_id)
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
            await db.flush()

            turn_updated = await db.execute(
                update(Turn)
                .where(Turn.id == ctx.turn_id)
                .values(status=final_status, error_message=None)
            )
            if turn_updated.rowcount != 1:
                await rollback_quietly()
                return result
            await db.commit()
            await self._ensure_not_cancelled(db, ctx.turn_id)

            await emit(
                "verdict_completed",
                {
                    "id": str(verdict_row.id),
                    "model_id": ctx.verdict_model_id,
                    "strategy": ctx.strategy.value,
                    "text": verdict_row.text,
                    "reason": verdict_row.reason,
                    "tokens_input": verdict_row.tokens_input,
                    "tokens_output": verdict_row.tokens_output,
                    "cost_usd": verdict_row.cost_usd,
                },
            )
        except asyncio.CancelledError:
            raise
        except TurnCancellationDetected:
            await rollback_quietly()
            return result
        except Exception as exc:
            if await is_turn_cancel_requested_or_deleted(db, ctx.turn_id):
                await rollback_quietly()
                return result
            message = format_llm_error(exc)
            logger.error("verdict_failed", error=message)
            try:
                await self._lock_active_turn_for_persistence(db, ctx.turn_id)
            except TurnCancellationDetected:
                await rollback_quietly()
                return result
            failed_update = await db.execute(
                update(Turn)
                .where(Turn.id == ctx.turn_id)
                .values(
                    status=TurnStatus.FAILED,
                    error_message=f"Verdict generation failed: {message}",
                )
            )
            if failed_update.rowcount != 1:
                await rollback_quietly()
                return result
            await db.commit()
            await emit("turn_failed", {"error": f"Verdict generation failed: {message}"})
            return result

        if await is_turn_cancel_requested_or_deleted(db, ctx.turn_id):
            await rollback_quietly()
            return result
        await emit(
            "turn_completed",
            {
                "turn_id": str(ctx.turn_id),
                "status": (TurnStatus.PARTIAL if failed_count else TurnStatus.COMPLETED).value,
            },
        )
        return result


_orchestrator: TurnOrchestrator | None = None


def get_orchestrator() -> TurnOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TurnOrchestrator()
    return _orchestrator
