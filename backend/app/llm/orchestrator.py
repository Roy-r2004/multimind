"""Multi-model turn orchestrator — parallel answers and verdict."""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from sqlalchemy import String, cast, exists, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
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
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry

logger = get_logger(__name__)

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
ACTIVE_TURN_STATUSES = (TurnStatus.PENDING, TurnStatus.RUNNING)
DELETION_POLL_INTERVAL_SECONDS = 0.5
MODEL_ANSWER_FAILED_CODE = "MODEL_ANSWER_FAILED"
MODEL_ANSWER_FAILED_MESSAGE = "A model failed to respond."
TURN_FAILED_CODE = "TURN_FAILED"
TURN_FAILED_MESSAGE = "Turn failed."
VERDICT_MAX_ATTEMPTS = 2
VERDICT_MAX_TOKENS = 4096
VERDICT_DEFAULT_REASON = "Synthesized from model responses."


class TurnNoLongerWritable(Exception):
    """Raised internally when a turn was deleted or no longer accepts writes."""


async def is_turn_deleted(db: AsyncSession, turn_id: str) -> bool:
    result = await db.execute(
        select(Turn.id)
        .where(Turn.id == turn_id, Turn.deleted_at.is_(None))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none() is None


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
    council_runtime_context: str | None = None
    referee_instructions: str | None = None
    template_instructions: str | None = None
    user_brain_context: str | None = None
    rolling_chat_memory: str | None = None
    recent_conversation_context: str | None = None
    playbook_context: str | None = None
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


def _verdict_fields(provider: Any, raw_text: str) -> tuple[str, str, dict[str, Any] | None]:
    """Extract verdict fields while preserving its structured evaluation payload.

    Strict JSON is preferred, but a verdict that arrives wrapped in prose or
    truncated by the token cap still carries the answer the user asked for.
    Returning the raw text keeps the turn usable instead of discarding it.
    """
    parsed = provider.parse_json_object_lenient(raw_text)
    fallback = (raw_text or "").strip()
    if not isinstance(parsed, dict):
        return fallback, VERDICT_DEFAULT_REASON, None

    text = parsed.get("text")
    text = text.strip() if isinstance(text, str) else ""
    reason = parsed.get("reason")
    reason = reason.strip() if isinstance(reason, str) else ""

    if not text:
        # Recovered JSON without a usable verdict body (e.g. truncated before
        # "text"): prefer the raw response over an empty verdict card.
        text = fallback
    if not reason:
        reason = VERDICT_DEFAULT_REASON
    return text, reason, parsed


def _validated_answer_scores(
    parsed: dict[str, Any] | None,
    answer_rows: list[ModelAnswer],
) -> list[dict[str, Any]]:
    """Return valid Referee scores mapped to completed rows by persisted answer ID."""
    if not parsed:
        return []
    evaluations = parsed.get("evaluations")
    if not isinstance(evaluations, list):
        return []

    completed_by_id = {
        str(row.id): row for row in answer_rows if row.status == ModelAnswerStatus.COMPLETED
    }
    identifier_counts: dict[str, int] = {}
    for item in evaluations:
        if isinstance(item, dict) and isinstance(item.get("answer_id"), str):
            answer_id = item["answer_id"].strip()
            if answer_id:
                identifier_counts[answer_id] = identifier_counts.get(answer_id, 0) + 1

    valid: list[dict[str, Any]] = []
    for item in evaluations:
        if not isinstance(item, dict):
            logger.warning("verdict_evaluation_malformed")
            continue
        raw_answer_id = item.get("answer_id")
        answer_id = raw_answer_id.strip() if isinstance(raw_answer_id, str) else ""
        if not answer_id:
            logger.warning("verdict_evaluation_missing_answer_id")
            continue
        if identifier_counts.get(answer_id, 0) != 1:
            logger.warning("verdict_evaluation_duplicate_answer_id", answer_id=answer_id)
            continue
        row = completed_by_id.get(answer_id)
        if row is None:
            logger.warning("verdict_evaluation_unknown_or_ineligible_answer", answer_id=answer_id)
            continue
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
            logger.warning(
                "verdict_evaluation_invalid_score",
                answer_id=answer_id,
                score=score,
            )
            continue
        valid.append(
            {"answer_id": answer_id, "model_id": row.model_id, "score": score}
        )
    return valid


def _answer_score_update_statement(
    turn_id: str,
    evaluation: dict[str, Any],
):
    """Build the score update with VARCHAR semantics for legacy status columns."""
    return (
        update(ModelAnswer)
        .where(
            ModelAnswer.id == evaluation["answer_id"],
            ModelAnswer.turn_id == turn_id,
            func.lower(cast(ModelAnswer.status, String))
            == ModelAnswerStatus.COMPLETED.value,
        )
        .values(confidence=evaluation["score"])
    )


class TurnOrchestrator:
    """Enterprise orchestration engine for multi-model turns."""

    def __init__(self) -> None:
        self._prompts = get_prompt_engine()
        self._providers = get_provider_registry()

    async def _ensure_not_deleted(self, db: AsyncSession, turn_id: str) -> None:
        if await is_turn_deleted(db, turn_id):
            raise TurnNoLongerWritable

    def _active_turn_exists(self, turn_id: str):
        return exists().where(
            Turn.id == turn_id,
            Turn.status.in_(ACTIVE_TURN_STATUSES),
        )

    async def _lock_active_turn_for_persistence(self, db: AsyncSession, turn_id: str) -> None:
        result = await db.execute(
            select(Turn.id)
            .where(
                Turn.id == turn_id,
                Turn.status.in_(ACTIVE_TURN_STATUSES),
            )
            .with_for_update()
        )
        if result.scalar_one_or_none() is None:
            raise TurnNoLongerWritable

    async def _fresh_deletion_check(self, turn_id: str) -> bool:
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as deletion_db:
            return await is_turn_deleted(deletion_db, turn_id)

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
                    timeout=DELETION_POLL_INTERVAL_SECONDS,
                )
                if provider_task in done:
                    return await provider_task
                if await self._fresh_deletion_check(turn_id):
                    provider_task.cancel()
                    await asyncio.gather(provider_task, return_exceptions=True)
                    raise TurnNoLongerWritable
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

    async def _turn_deleted_after_rollback(
        self,
        db: AsyncSession,
        turn_id: str,
    ) -> bool:
        return await is_turn_deleted(db, turn_id)

    async def _persist_answer_scores(
        self,
        db: AsyncSession,
        turn_id: str,
        answer_scores: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Persist optional Referee scores without poisoning verdict persistence."""
        persisted: list[dict[str, Any]] = []
        for evaluation in answer_scores:
            try:
                async with db.begin_nested():
                    updated = await db.execute(
                        _answer_score_update_statement(turn_id, evaluation)
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError("validated answer score did not update one row")
                persisted.append(evaluation)
            except (SQLAlchemyError, RuntimeError) as exc:
                logger.error(
                    "verdict_answer_score_persistence_failed",
                    turn_id=turn_id,
                    answer_id=evaluation["answer_id"],
                    score=evaluation["score"],
                    operation="update_model_answer_confidence",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
        return persisted

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

        # Streaming calls arrive with a service-owned RUNNING claim. Direct unseeded calls
        # atomically claim their freshly-created pending turn here before provider work.
        if ctx.skip_answer_seed:
            active = await db.execute(
                select(Turn.id).where(
                    Turn.id == ctx.turn_id,
                    Turn.status == TurnStatus.RUNNING,
                )
            )
            if active.scalar_one_or_none() is None:
                await rollback_quietly()
                return result
        else:
            claimed = await db.execute(
                update(Turn)
                .where(
                    Turn.id == ctx.turn_id,
                    Turn.status == TurnStatus.PENDING,
                )
                .values(status=TurnStatus.RUNNING)
            )
            if claimed.rowcount != 1:
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
            await self._ensure_not_deleted(db, ctx.turn_id)
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
            await self._ensure_not_deleted(db, ctx.turn_id)
        except TurnNoLongerWritable:
            await rollback_quietly()
            return result

        await emit("turn_started", {"turn_id": str(ctx.turn_id), "models": ctx.model_ids})
        for model_id in ctx.model_ids:
            await emit("model_answer_started", {"model_id": model_id})

        async def call_model(model_id: str) -> ModelCallResult:
            await self._ensure_not_deleted(db, ctx.turn_id)
            model = get_model(model_id)
            system = self._prompts.model_answer_prompt(
                user_message=ctx.user_message,
                model_id=model.id,
                model_name=model.name,
                vendor=model.vendor,
                model_set_name=ctx.model_set_name,
                council_runtime_context=ctx.council_runtime_context,
                user_brain_context=ctx.user_brain_context,
                rolling_chat_memory=ctx.rolling_chat_memory,
                recent_conversation_context=ctx.recent_conversation_context,
                playbook_context=ctx.playbook_context,
            )

            try:
                provider = self._providers.get_provider(model.provider)
                await self._ensure_not_deleted(db, ctx.turn_id)
                response = await self._await_provider_complete(
                    ctx.turn_id,
                    provider.complete(
                        system=system,
                        user=ctx.user_message,
                        model=model.provider_model,
                        max_tokens=20000,
                    ),
                )
                await self._ensure_not_deleted(db, ctx.turn_id)
                return ModelCallResult(model_id=model_id, model_name=model.name, response=response)
            except asyncio.CancelledError:
                raise
            except TurnNoLongerWritable:
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
                    {
                        "model_id": call_result.model_id,
                        "code": MODEL_ANSWER_FAILED_CODE,
                        "error": MODEL_ANSWER_FAILED_MESSAGE,
                    },
                )
                return

            response = call_result.response
            if response is None:
                await rollback_quietly()
                return

            # Persist / emit only the OpenRouter-reported charge (never estimate).
            reported_cost = response.cost_usd
            stored_cost = float(reported_cost) if reported_cost is not None else 0.0
            await self._lock_active_turn_for_persistence(db, ctx.turn_id)
            updated = await db.execute(
                update(ModelAnswer)
                .where(
                    ModelAnswer.turn_id == ctx.turn_id,
                    ModelAnswer.model_id == call_result.model_id,
                )
                .values(
                    text=response.text,
                    confidence=None,
                    tokens_input=response.tokens_input,
                    tokens_output=response.tokens_output,
                    cost_usd=stored_cost,
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
                cost_usd=stored_cost,
            )
            db.add(cost)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                if await self._turn_deleted_after_rollback(
                    db,
                    ctx.turn_id,
                ):
                    raise TurnNoLongerWritable
                raise
            result.cost_records.append(cost)
            await emit(
                "model_answer_completed",
                {
                    "model_id": call_result.model_id,
                    "model_name": call_result.model_name,
                    "text": response.text,
                    "confidence": None,
                    "tokens_input": response.tokens_input,
                    "tokens_output": response.tokens_output,
                    "cost_usd": reported_cost,
                },
            )

        tasks = [asyncio.create_task(call_model(mid)) for mid in ctx.model_ids]
        try:
            for task in asyncio.as_completed(tasks):
                call_result = await task
                await persist_model_result(call_result)
        except TurnNoLongerWritable:
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
            await self._ensure_not_deleted(db, ctx.turn_id)
        except TurnNoLongerWritable:
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
                    "answer_id": str(row.id),
                    "model_id": model_id,
                    "model_name": model.name,
                    "text": row.text or "",
                    "failed": row.status != ModelAnswerStatus.COMPLETED,
                    "error_message": row.error_message,
                }
            )

        successful = [a for a in answer_context if not a["failed"]]
        if not successful:
            try:
                await self._lock_active_turn_for_persistence(db, ctx.turn_id)
            except TurnNoLongerWritable:
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
            await emit("turn_failed", {"code": TURN_FAILED_CODE, "error": TURN_FAILED_MESSAGE})
            return result

        # A one-member Council is a normal single-AI turn. Its persisted answer
        # is final, so do not invoke or account for a Referee. This deliberately
        # uses selected membership rather than the number of successful calls.
        if len(ctx.model_ids) == 1:
            failed_count = sum(1 for answer in answer_context if answer["failed"])
            final_status = TurnStatus.PARTIAL if failed_count else TurnStatus.COMPLETED
            try:
                await self._lock_active_turn_for_persistence(db, ctx.turn_id)
            except TurnNoLongerWritable:
                await rollback_quietly()
                return result
            turn_updated = await db.execute(
                update(Turn)
                .where(Turn.id == ctx.turn_id)
                .values(status=final_status, error_message=None)
            )
            if turn_updated.rowcount != 1:
                await rollback_quietly()
                return result
            await db.commit()
            try:
                await self._ensure_not_deleted(db, ctx.turn_id)
            except TurnNoLongerWritable:
                await rollback_quietly()
                return result
            await emit(
                "turn_completed",
                {"turn_id": str(ctx.turn_id), "status": final_status.value},
            )
            return result

        # Phase 2: Verdict
        try:
            await self._ensure_not_deleted(db, ctx.turn_id)
        except TurnNoLongerWritable:
            await rollback_quietly()
            return result

        await emit("verdict_started", {"model_id": ctx.verdict_model_id})

        verdict_system = self._prompts.verdict_prompt(
            strategy=ctx.strategy.value,
            user_message=ctx.user_message,
            model_answers=answer_context,
            referee_instructions=ctx.referee_instructions,
            custom_instructions=ctx.referee_instructions,
            template_instructions=ctx.template_instructions,
            user_brain_context=ctx.user_brain_context,
            rolling_chat_memory=ctx.rolling_chat_memory,
            recent_conversation_context=ctx.recent_conversation_context,
            playbook_context=ctx.playbook_context,
        )

        verdict_model = get_model(ctx.verdict_model_id)
        provider = self._providers.get_provider(verdict_model.provider)

        try:
            await self._ensure_not_deleted(db, ctx.turn_id)
            # The verdict call is the single point where a whole turn used to be
            # thrown away: a transient provider error or a response that was not
            # strict JSON produced "4 answers, no verdict". Retry once, then fall
            # back to the raw text rather than failing the turn.
            verdict_response = None
            last_error: Exception | None = None
            for attempt in range(VERDICT_MAX_ATTEMPTS):
                try:
                    verdict_response = await self._await_provider_complete(
                        ctx.turn_id,
                        provider.complete(
                            system=verdict_system,
                            user="Produce the verdict JSON now.",
                            model=verdict_model.provider_model,
                            max_tokens=VERDICT_MAX_TOKENS,
                        ),
                    )
                    break
                except (asyncio.CancelledError, TurnNoLongerWritable):
                    raise
                except Exception as exc:  # noqa: BLE001 - retried below
                    last_error = exc
                    if attempt + 1 >= VERDICT_MAX_ATTEMPTS:
                        raise
                    logger.warning(
                        "verdict_attempt_failed",
                        attempt=attempt + 1,
                        error=format_llm_error(exc),
                    )
                    await self._ensure_not_deleted(db, ctx.turn_id)

            if verdict_response is None:  # pragma: no cover - defensive
                raise last_error or RuntimeError("Verdict model returned no response")

            await self._ensure_not_deleted(db, ctx.turn_id)
            verdict_text, verdict_reason, parsed_verdict = _verdict_fields(
                provider, verdict_response.text
            )
            if not verdict_text:
                raise ValueError("Verdict model returned an empty response")

            answer_scores = _validated_answer_scores(
                parsed_verdict,
                list(answer_rows.values()),
            )

            failed_count = sum(1 for a in answer_context if a["failed"])
            final_status = TurnStatus.PARTIAL if failed_count else TurnStatus.COMPLETED
            await self._lock_active_turn_for_persistence(db, ctx.turn_id)
            persisted_answer_scores = await self._persist_answer_scores(
                db,
                ctx.turn_id,
                answer_scores,
            )
            reported_verdict_cost = verdict_response.cost_usd
            stored_verdict_cost = (
                float(reported_verdict_cost) if reported_verdict_cost is not None else 0.0
            )
            verdict_row = Verdict(
                turn_id=ctx.turn_id,
                model_id=ctx.verdict_model_id,
                strategy=ctx.strategy,
                text=verdict_text,
                reason=verdict_reason,
                tokens_input=verdict_response.tokens_input,
                tokens_output=verdict_response.tokens_output,
                cost_usd=stored_verdict_cost,
            )
            db.add(verdict_row)
            result.verdict = verdict_row
            await db.flush()

            await self._ensure_not_deleted(db, ctx.turn_id)
            cost = CostRecord(
                org_id=ctx.org_id,
                chat_id=ctx.chat_id,
                project_id=ctx.project_id,
                turn_id=ctx.turn_id,
                model_id=ctx.verdict_model_id,
                kind=UsageKind.VERDICT,
                tokens_input=verdict_response.tokens_input,
                tokens_output=verdict_response.tokens_output,
                cost_usd=stored_verdict_cost,
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
            await self._ensure_not_deleted(db, ctx.turn_id)

            await emit(
                "verdict_completed",
                {
                    "id": str(verdict_row.id),
                    "model_id": ctx.verdict_model_id,
                    "strategy": ctx.strategy.value,
                    "text": verdict_row.text,
                    "reason": verdict_row.reason,
                    "answer_scores": persisted_answer_scores,
                    "tokens_input": verdict_row.tokens_input,
                    "tokens_output": verdict_row.tokens_output,
                    "cost_usd": reported_verdict_cost,
                },
            )
        except asyncio.CancelledError:
            raise
        except TurnNoLongerWritable:
            await rollback_quietly()
            return result
        except Exception as exc:
            message = format_llm_error(exc)
            logger.error(
                "verdict_failed",
                turn_id=ctx.turn_id,
                operation="persist_verdict",
                error_type=type(exc).__name__,
                error=message,
            )
            # PostgreSQL leaves the transaction unusable after a statement
            # failure. Roll back before issuing deletion or status queries so
            # the original error is not masked by InFailedSQLTransactionError.
            await rollback_quietly()
            if await is_turn_deleted(db, ctx.turn_id):
                return result
            try:
                await self._lock_active_turn_for_persistence(db, ctx.turn_id)
            except TurnNoLongerWritable:
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
            await emit("turn_failed", {"code": TURN_FAILED_CODE, "error": TURN_FAILED_MESSAGE})
            return result

        if await is_turn_deleted(db, ctx.turn_id):
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
