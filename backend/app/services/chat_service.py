"""Chat and turn business logic."""

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.db.models import (
    Chat,
    CostRecord,
    DecisionInsurance,
    ModelAnswer,
    ModelAnswerStatus,
    ModelSet,
    OrgRole,
    ShareLink,
    Turn,
    TurnStatus,
    Verdict,
)
from app.db.session import AsyncSessionLocal
from app.llm.catalog import get_model
from app.services.brain_service import brain_service
from app.llm.orchestrator import (
    ACTIVE_TURN_STATUSES,
    TurnContext,
    get_orchestrator,
    is_turn_cancel_requested_or_deleted,
)
from app.services.saved_verdict_service import saved_verdict_service
from app.schemas.api import (
    ChatCreateRequest,
    ChatResponse,
    ChatUpdateRequest,
    DecisionInsuranceResponse,
    ModelAnswerResponse,
    TurnDeleteResponse,
    TurnCreateRequest,
    TurnResponse,
    VerdictResponse,
)

logger = get_logger(__name__)
CHALLENGE_TURN_MARKER = "__multimind_challenge_turn__"
TURN_ALREADY_RUNNING_CODE = "TURN_ALREADY_RUNNING"
TURN_ALREADY_RUNNING_MESSAGE = "Turn orchestration is already running."
TURN_START_FAILED_CODE = "TURN_START_FAILED"
TURN_START_FAILED_MESSAGE = "Turn orchestration could not be started."
TURN_STREAM_INTERNAL_ERROR_CODE = "TURN_STREAM_INTERNAL_ERROR"
TURN_STREAM_INTERNAL_ERROR_MESSAGE = "An unexpected error occurred while processing the turn."
TURN_FAILED_CODE = "TURN_FAILED"
TURN_FAILED_MESSAGE = "Turn failed."
TURN_DELETE_FORBIDDEN_MESSAGE = "You do not have permission to delete this turn."
_orchestration_tasks: dict[str, asyncio.Task[None]] = {}


def _discard_orchestration_task(turn_id: str, task: Any | None) -> None:
    if task is not None and _orchestration_tasks.get(turn_id) is task:
        _orchestration_tasks.pop(turn_id, None)


def _consume_orchestration_task_result(turn_id: str, task: Any) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning(
            "orchestration_task_finished_after_delete_with_error",
            turn_id=turn_id,
            error=str(exc),
        )
    finally:
        _discard_orchestration_task(turn_id, task)


async def _cancel_orchestration_task_after_commit(
    turn_id: str, task: Any | None
) -> None:
    if task is None:
        return
    if task.done():
        _consume_orchestration_task_result(turn_id, task)
        return

    try:
        task.add_done_callback(
            lambda done_task: _consume_orchestration_task_result(turn_id, done_task)
        )
    except Exception as exc:
        logger.warning(
            "orchestration_task_done_callback_after_delete_failed",
            turn_id=turn_id,
            error=str(exc),
        )
        _discard_orchestration_task(turn_id, task)

    try:
        task.cancel()
    except Exception as exc:
        logger.warning(
            "orchestration_task_cancel_after_delete_failed",
            turn_id=turn_id,
            error=str(exc),
        )
        _discard_orchestration_task(turn_id, task)


async def _commit_or_rollback(db: AsyncSession) -> None:
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def _mark_claimed_turn_failed(db: AsyncSession, turn_id: str) -> None:
    try:
        await db.execute(
            update(Turn)
            .where(
                Turn.id == turn_id,
                Turn.status == TurnStatus.RUNNING,
                Turn.cancel_requested_at.is_(None),
            )
            .values(status=TurnStatus.FAILED, error_message=TURN_START_FAILED_MESSAGE)
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


def turn_stream_internal_error_event() -> dict[str, Any]:
    return {
        "type": "error",
        "data": {
            "code": TURN_STREAM_INTERNAL_ERROR_CODE,
            "message": TURN_STREAM_INTERNAL_ERROR_MESSAGE,
        },
    }


def turn_failed_event() -> dict[str, Any]:
    return {
        "type": "turn_failed",
        "data": {
            "code": TURN_FAILED_CODE,
            "error": TURN_FAILED_MESSAGE,
        },
    }


async def _recover_unowned_claimed_turn(db: AsyncSession, turn_id: str) -> None:
    try:
        await _mark_claimed_turn_failed(db, turn_id)
    except Exception as exc:
        logger.warning(
            "turn_claim_recovery_failed",
            turn_id=turn_id,
            error=str(exc),
        )


class ChatService:
    async def list_chats(self, db: AsyncSession, auth: AuthContext) -> list[ChatResponse]:
        result = await db.execute(
            select(Chat)
            .where(Chat.org_id == auth.org_id)
            .order_by(Chat.updated_at.desc())
        )
        return [self._chat_response(c) for c in result.scalars().all()]

    async def create_chat(
        self, db: AsyncSession, auth: AuthContext, data: ChatCreateRequest
    ) -> ChatResponse:
        chat = Chat(
            org_id=auth.org_id,
            project_id=data.project_id,
            created_by=auth.user.id,
            title=data.title,
        )
        db.add(chat)
        await db.flush()
        return self._chat_response(chat)

    async def get_chat(self, db: AsyncSession, auth: AuthContext, chat_id: str) -> Chat:
        result = await db.execute(
            select(Chat).where(Chat.id == chat_id, Chat.org_id == auth.org_id)
        )
        chat = result.scalar_one_or_none()
        if chat is None:
            raise NotFoundError("Chat", str(chat_id))
        return chat

    def _authorize_turn_delete(self, chat: Chat, auth: AuthContext) -> None:
        if auth.role in (OrgRole.OWNER, OrgRole.ADMIN):
            return
        if auth.role == OrgRole.MEMBER and chat.created_by == auth.user.id:
            return
        raise ForbiddenError(TURN_DELETE_FORBIDDEN_MESSAGE)

    async def update_chat(
        self, db: AsyncSession, auth: AuthContext, chat_id: str, data: ChatUpdateRequest
    ) -> ChatResponse:
        chat = await self.get_chat(db, auth, chat_id)
        if data.title is not None:
            chat.title = data.title.strip()
        if data.project_id is not None:
            chat.project_id = data.project_id
        await db.flush()
        return self._chat_response(chat)

    async def delete_chat(self, db: AsyncSession, auth: AuthContext, chat_id: str) -> None:
        await self.get_chat(db, auth, chat_id)
        turn_rows = (
            await db.execute(select(Turn.id, Turn.status).where(Turn.chat_id == chat_id))
        ).all()
        captured_tasks = {
            row.id: _orchestration_tasks.get(row.id)
            for row in turn_rows
            if row.status in ACTIVE_TURN_STATUSES
        }

        try:
            locked_chat = (
                await db.execute(
                    select(Chat)
                    .where(Chat.id == chat_id, Chat.org_id == auth.org_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if locked_chat is None:
                raise NotFoundError("Chat", str(chat_id))

            turn_ids = select(Turn.id).where(Turn.chat_id == chat_id)

            await db.execute(delete(CostRecord).where(CostRecord.chat_id == chat_id))
            await db.execute(
                delete(DecisionInsurance).where(DecisionInsurance.turn_id.in_(turn_ids))
            )
            await db.execute(delete(Verdict).where(Verdict.turn_id.in_(turn_ids)))
            await db.execute(delete(ModelAnswer).where(ModelAnswer.turn_id.in_(turn_ids)))
            await db.execute(delete(ShareLink).where(ShareLink.chat_id == chat_id))
            await db.execute(delete(Turn).where(Turn.chat_id == chat_id))
            await db.delete(locked_chat)
            await db.flush()
            await db.commit()
        except Exception:
            await db.rollback()
            raise

        for turn_id, task in captured_tasks.items():
            await _cancel_orchestration_task_after_commit(turn_id, task)

    async def delete_turn(
        self, db: AsyncSession, auth: AuthContext, chat_id: str, turn_id: str
    ) -> TurnDeleteResponse:
        chat = await self.get_chat(db, auth, chat_id)
        self._authorize_turn_delete(chat, auth)
        captured_task = _orchestration_tasks.get(turn_id)

        result = await db.execute(
            select(Turn.id)
            .where(Turn.id == turn_id, Turn.chat_id == chat_id)
            .with_for_update()
        )
        existing_turn_id = result.scalar_one_or_none()
        if existing_turn_id is None:
            any_turn = await db.execute(select(Turn.id).where(Turn.id == turn_id))
            if any_turn.scalar_one_or_none() is not None:
                raise NotFoundError("Turn", str(turn_id))
            return TurnDeleteResponse(turn_id=turn_id, deleted=True)

        try:
            await db.execute(delete(CostRecord).where(CostRecord.turn_id == turn_id))
            await db.execute(delete(DecisionInsurance).where(DecisionInsurance.turn_id == turn_id))
            await db.execute(delete(Verdict).where(Verdict.turn_id == turn_id))
            await db.execute(delete(ModelAnswer).where(ModelAnswer.turn_id == turn_id))
            await db.execute(delete(Turn).where(Turn.id == turn_id, Turn.chat_id == chat_id))
            await db.flush()
            await db.commit()
        except Exception:
            await db.rollback()
            raise

        await _cancel_orchestration_task_after_commit(turn_id, captured_task)
        return TurnDeleteResponse(turn_id=turn_id, deleted=True)

    async def _resolve_model_set(
        self, db: AsyncSession, auth: AuthContext, model_set_id: str
    ) -> ModelSet:
        result = await db.execute(
            select(ModelSet).where(
                ModelSet.slug == model_set_id,
                (ModelSet.org_id == auth.org_id) | (ModelSet.is_system.is_(True)),
            )
        )
        model_set = result.scalar_one_or_none()
        if model_set is None:
            raise NotFoundError("ModelSet", model_set_id)
        return model_set

    async def start_turn(
        self, db: AsyncSession, auth: AuthContext, chat_id: str, data: TurnCreateRequest
    ) -> TurnResponse:
        """Create a pending turn — orchestration runs via SSE stream."""
        chat = await self.get_chat(db, auth, chat_id)
        model_set = await self._resolve_model_set(db, auth, data.model_set_id)

        turn = Turn(
            chat_id=chat.id,
            user_message=data.user_message.strip(),
            model_set_id=model_set.slug,
            strategy=model_set.strategy,
            verdict_model=model_set.verdict_model,
            status=TurnStatus.PENDING,
            custom_instructions=data.custom_instructions or model_set.custom_instructions,
            decision_insurance_enabled=False,
        )
        db.add(turn)

        if chat.title == "New chat":
            chat.title = data.user_message.strip()[:80] or "New chat"

        await db.flush()

        for model_id in model_set.models:
            db.add(
                ModelAnswer(
                    turn_id=turn.id,
                    model_id=model_id,
                    status=ModelAnswerStatus.PENDING,
                )
            )
        await db.flush()
        loaded = await db.execute(
            select(Turn)
            .where(Turn.id == turn.id)
            .options(
                selectinload(Turn.model_answers),
                selectinload(Turn.verdict),
                selectinload(Turn.decision_insurance),
                selectinload(Turn.lesson),
            )
        )
        turn = loaded.scalar_one()
        return self._pending_turn_response(turn)

    def _pending_turn_response(self, turn: Turn) -> TurnResponse:
        answers = []
        for a in turn.model_answers:
            model = get_model(a.model_id)
            answers.append(
                ModelAnswerResponse(
                    model_id=a.model_id,
                    model_name=model.name,
                    text=a.text,
                    confidence=a.confidence,
                    status=a.status.value,
                    error_message=a.error_message,
                    tokens_input=a.tokens_input,
                    tokens_output=a.tokens_output,
                    cost_usd=a.cost_usd,
                )
            )
        return TurnResponse(
            id=turn.id,
            chat_id=turn.chat_id,
            user_message=turn.user_message,
            model_set_id=turn.model_set_id,
            strategy=turn.strategy,
            verdict_model=turn.verdict_model,
            status=turn.status.value,
            model_answers=answers,
            verdict=None,
            decision_insurance=None,
            lesson_id=None,
            created_at=turn.created_at,
        )

    async def _poll_turn_until_done(
        self, auth: AuthContext, turn_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Wait for an in-flight turn (e.g. after stream timeout or reconnect)."""
        while True:
            async with AsyncSessionLocal() as db:
                if await is_turn_cancel_requested_or_deleted(db, turn_id):
                    yield {"type": "turn_deleted", "data": {"turn_id": turn_id}}
                    return
                try:
                    turn = await self.get_turn(db, auth, turn_id)
                except NotFoundError:
                    yield {"type": "turn_deleted", "data": {"turn_id": turn_id}}
                    return
            status = turn.status
            if status in ("completed", "partial"):
                yield {"type": "turn_completed", "data": turn.model_dump(mode="json")}
                return
            if status == "failed":
                yield turn_failed_event()
                yield {"type": "turn_completed", "data": turn.model_dump(mode="json")}
                return
            yield {"type": "ping", "data": {}}
            await asyncio.sleep(2)

    async def execute_turn_stream(
        self, db: AsyncSession, auth: AuthContext, turn_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Run orchestrator and yield SSE event payloads in real time."""
        result = await db.execute(
            select(Turn)
            .join(Chat, Chat.id == Turn.chat_id)
            .where(Turn.id == turn_id, Chat.org_id == auth.org_id)
            .options(
                selectinload(Turn.model_answers),
                selectinload(Turn.verdict),
                selectinload(Turn.decision_insurance),
                selectinload(Turn.lesson),
            )
        )
        turn = result.scalar_one_or_none()
        if turn is None:
            yield {"type": "turn_deleted", "data": {"turn_id": turn_id}}
            return

        if turn.cancel_requested_at is not None:
            yield {"type": "turn_deleted", "data": {"turn_id": turn_id}}
            return

        if turn.status in (TurnStatus.COMPLETED, TurnStatus.PARTIAL):
            saved_verdict_ids = await self._saved_verdict_ids_for_turns(db, auth, [turn])
            yield {
                "type": "turn_completed",
                "data": self._turn_response(turn, saved_verdict_ids).model_dump(mode="json"),
            }
            return

        if turn.status == TurnStatus.RUNNING:
            yield {
                "type": "error",
                "data": {
                    "code": TURN_ALREADY_RUNNING_CODE,
                    "message": TURN_ALREADY_RUNNING_MESSAGE,
                },
            }
            return

        if turn.status == TurnStatus.FAILED:
            yield turn_failed_event()
            saved_verdict_ids = await self._saved_verdict_ids_for_turns(db, auth, [turn])
            yield {
                "type": "turn_completed",
                "data": self._turn_response(turn, saved_verdict_ids).model_dump(mode="json"),
            }
            return

        model_set = await self._resolve_model_set(db, auth, turn.model_set_id)
        chat = await self.get_chat(db, auth, turn.chat_id)
        user_brain_context = await brain_service.get_context_for_user(
            db, auth.user.id, auth.org_id, auth.user.full_name
        )
        previous_verdict_context = await self._latest_previous_verdict_context(
            db, chat.id, turn.id, turn.created_at
        )

        ctx = TurnContext(
            turn_id=turn.id,
            chat_id=chat.id,
            org_id=auth.org_id,
            project_id=chat.project_id,
            user_message=turn.user_message,
            model_ids=list(model_set.models),
            verdict_model_id=turn.verdict_model,
            strategy=turn.strategy,
            model_set_name=model_set.name,
            custom_instructions=turn.custom_instructions,
            user_brain_context=user_brain_context or None,
            previous_verdict_context=previous_verdict_context,
            skip_answer_seed=True,
        )

        claimed = await db.execute(
            update(Turn)
            .where(
                Turn.id == turn_id,
                Turn.status == TurnStatus.PENDING,
                Turn.cancel_requested_at.is_(None),
            )
            .values(status=TurnStatus.RUNNING, error_message=None)
        )
        if claimed.rowcount != 1:
            await db.rollback()
            fresh_result = await db.execute(
                select(Turn)
                .join(Chat, Chat.id == Turn.chat_id)
                .where(Turn.id == turn_id, Chat.org_id == auth.org_id)
                .options(
                    selectinload(Turn.model_answers),
                    selectinload(Turn.verdict),
                    selectinload(Turn.decision_insurance),
                    selectinload(Turn.lesson),
                )
                .execution_options(populate_existing=True)
            )
            fresh_turn = fresh_result.scalar_one_or_none()
            if fresh_turn is None or fresh_turn.cancel_requested_at is not None:
                yield {"type": "turn_deleted", "data": {"turn_id": turn_id}}
                return
            if fresh_turn.status == TurnStatus.RUNNING:
                yield {
                    "type": "error",
                    "data": {
                        "code": TURN_ALREADY_RUNNING_CODE,
                        "message": TURN_ALREADY_RUNNING_MESSAGE,
                    },
                }
                return
            if fresh_turn.status in (TurnStatus.COMPLETED, TurnStatus.PARTIAL):
                saved_verdict_ids = await self._saved_verdict_ids_for_turns(db, auth, [fresh_turn])
                yield {
                    "type": "turn_completed",
                    "data": self._turn_response(
                        fresh_turn, saved_verdict_ids
                    ).model_dump(mode="json"),
                }
                return
            if fresh_turn.status == TurnStatus.FAILED:
                yield turn_failed_event()
                saved_verdict_ids = await self._saved_verdict_ids_for_turns(db, auth, [fresh_turn])
                yield {
                    "type": "turn_completed",
                    "data": self._turn_response(
                        fresh_turn, saved_verdict_ids
                    ).model_dump(mode="json"),
                }
                return
            yield {
                "type": "error",
                "data": {
                    "code": TURN_ALREADY_RUNNING_CODE,
                    "message": TURN_ALREADY_RUNNING_MESSAGE,
                },
            }
            return
        try:
            await db.commit()
        except asyncio.CancelledError:
            await _recover_unowned_claimed_turn(db, turn_id)
            raise
        except Exception:
            await db.rollback()
            raise

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def on_event(event: str, data: dict[str, Any]) -> None:
            await queue.put({"type": event, "data": data})

        async def orchestrate() -> None:
            try:
                async with AsyncSessionLocal() as run_db:
                    await get_orchestrator().run(run_db, ctx, on_event=on_event)
                    await run_db.commit()
                async with AsyncSessionLocal() as read_db:
                    if await is_turn_cancel_requested_or_deleted(read_db, turn_id):
                        await queue.put({"type": "turn_deleted", "data": {"turn_id": turn_id}})
                        return
                    try:
                        final = await self.get_turn(read_db, auth, turn_id)
                    except NotFoundError:
                        await queue.put({"type": "turn_deleted", "data": {"turn_id": turn_id}})
                        return
                    await queue.put(
                        {"type": "turn_completed", "data": final.model_dump(mode="json")}
                    )
            except asyncio.CancelledError:
                await queue.put({"type": "turn_deleted", "data": {"turn_id": turn_id}})
            except Exception:
                logger.exception(
                    "turn_stream_orchestration_failed",
                    turn_id=turn_id,
                    chat_id=ctx.chat_id,
                )
                async with AsyncSessionLocal() as failure_db:
                    await _mark_claimed_turn_failed(failure_db, turn_id)
                await queue.put(turn_stream_internal_error_event())
            finally:
                _discard_orchestration_task(turn_id, asyncio.current_task())
                await queue.put(None)

        task_registered = False
        live_existing_owner = False
        coroutine = None
        task: asyncio.Task[None] | None = None
        try:
            existing_task = _orchestration_tasks.get(turn_id)
            if existing_task is not None:
                if existing_task.done():
                    _consume_orchestration_task_result(turn_id, existing_task)
                else:
                    logger.error(
                        "orchestration_task_registry_live_after_claim",
                        turn_id=turn_id,
                    )
                    live_existing_owner = True
                    yield {
                        "type": "error",
                        "data": {
                            "code": TURN_ALREADY_RUNNING_CODE,
                            "message": TURN_ALREADY_RUNNING_MESSAGE,
                        },
                    }
                    return

            coroutine = orchestrate()
            task = asyncio.create_task(coroutine)
            _orchestration_tasks[turn_id] = task
            task_registered = True
            task.add_done_callback(
                lambda done_task: _consume_orchestration_task_result(turn_id, done_task)
            )
        except asyncio.CancelledError:
            if coroutine is not None and task is None:
                coroutine.close()
            if task is not None:
                _discard_orchestration_task(turn_id, task)
            if not task_registered and not live_existing_owner:
                await _recover_unowned_claimed_turn(db, turn_id)
            raise
        except Exception:
            if coroutine is not None and task is None:
                coroutine.close()
            if task is not None:
                _discard_orchestration_task(turn_id, task)
            if not task_registered and not live_existing_owner:
                await _recover_unowned_claimed_turn(db, turn_id)
                yield {
                    "type": "error",
                    "data": {
                        "code": TURN_START_FAILED_CODE,
                        "message": TURN_START_FAILED_MESSAGE,
                    },
                }
                return
            raise

        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=12.0)
            except asyncio.TimeoutError:
                yield {"type": "ping", "data": {"ts": time.time()}}
                continue
            if item is None:
                break
            yield item
            if item["type"] in ("turn_completed", "turn_deleted", "error"):
                break

    async def get_turn(self, db: AsyncSession, auth: AuthContext, turn_id: str) -> TurnResponse:
        result = await db.execute(
            select(Turn)
            .join(Chat, Chat.id == Turn.chat_id)
            .where(Turn.id == turn_id, Chat.org_id == auth.org_id)
            .options(
                selectinload(Turn.model_answers),
                selectinload(Turn.verdict),
                selectinload(Turn.decision_insurance),
                selectinload(Turn.lesson),
            )
        )
        turn = result.scalar_one_or_none()
        if turn is None:
            raise NotFoundError("Turn", str(turn_id))
        saved_verdict_ids = await self._saved_verdict_ids_for_turns(db, auth, [turn])
        return self._turn_response(turn, saved_verdict_ids)

    async def list_turns(
        self, db: AsyncSession, auth: AuthContext, chat_id: str
    ) -> list[TurnResponse]:
        await self.get_chat(db, auth, chat_id)
        result = await db.execute(
            select(Turn)
            .where(
                Turn.chat_id == chat_id,
                (Turn.error_message.is_(None)) | (Turn.error_message != CHALLENGE_TURN_MARKER),
            )
            .options(
                selectinload(Turn.model_answers),
                selectinload(Turn.verdict),
                selectinload(Turn.decision_insurance),
                selectinload(Turn.lesson),
            )
            .order_by(Turn.created_at.asc())
        )
        turns = list(result.scalars().all())
        saved_verdict_ids = await self._saved_verdict_ids_for_turns(db, auth, turns)
        return [self._turn_response(t, saved_verdict_ids) for t in turns]

    async def _saved_verdict_ids_for_turns(
        self, db: AsyncSession, auth: AuthContext, turns: list[Turn]
    ) -> set[str]:
        verdict_ids = [str(turn.verdict.id) for turn in turns if turn.verdict]
        return await saved_verdict_service.saved_source_verdict_ids(db, auth, verdict_ids)

    async def _latest_previous_verdict_context(
        self,
        db: AsyncSession,
        chat_id: str,
        current_turn_id: str,
        current_turn_created_at: datetime | None,
    ) -> str | None:
        filters = [
            Turn.chat_id == chat_id,
            Turn.id != current_turn_id,
            (Turn.error_message.is_(None)) | (Turn.error_message != CHALLENGE_TURN_MARKER),
        ]
        if current_turn_created_at is not None:
            filters.append(Turn.created_at < current_turn_created_at)

        result = await db.execute(
            select(Turn, Verdict)
            .join(Verdict, Verdict.turn_id == Turn.id)
            .where(*filters)
            .order_by(Turn.created_at.desc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            logger.debug(
                "previous_verdict_context_lookup",
                previous_verdict_lookup_chat_id=chat_id,
                previous_verdict_lookup_current_turn_id=current_turn_id,
                previous_verdict_context_found=False,
                previous_verdict_context_chars=0,
                previous_verdict_turn_id=None,
            )
            return None

        previous_turn, verdict = row
        parts = [
            f"Previous user question:\n{previous_turn.user_message.strip()}",
            f"Previous final verdict:\n{verdict.text.strip()}",
        ]
        if verdict.reason:
            parts.append(f"Previous verdict rationale:\n{verdict.reason.strip()}")

        context = "\n\n".join(part for part in parts if part.strip())
        context = context[:6000] if context else None
        logger.debug(
            "previous_verdict_context_lookup",
            previous_verdict_lookup_chat_id=chat_id,
            previous_verdict_lookup_current_turn_id=current_turn_id,
            previous_verdict_context_found=context is not None,
            previous_verdict_context_chars=len(context or ""),
            previous_verdict_turn_id=previous_turn.id,
        )
        return context

    def _chat_response(self, chat: Chat) -> ChatResponse:
        return ChatResponse(
            id=chat.id,  # type: ignore[arg-type]
            title=chat.title,
            project_id=chat.project_id,
            updated_at=chat.updated_at,
        )

    def _turn_response(
        self, turn: Turn, saved_verdict_ids: set[str] | None = None
    ) -> TurnResponse:
        answers = []
        for a in turn.model_answers:
            model = get_model(a.model_id)
            answers.append(
                ModelAnswerResponse(
                    model_id=a.model_id,
                    model_name=model.name,
                    text=a.text,
                    confidence=a.confidence,
                    status=a.status.value,
                    error_message=a.error_message,
                    tokens_input=a.tokens_input,
                    tokens_output=a.tokens_output,
                    cost_usd=a.cost_usd,
                )
            )

        verdict = None
        if turn.verdict:
            verdict = VerdictResponse(
                id=str(turn.verdict.id),
                model_id=turn.verdict.model_id,
                strategy=turn.verdict.strategy,
                text=turn.verdict.text,
                reason=turn.verdict.reason,
                saved=str(turn.verdict.id) in (saved_verdict_ids or set()),
                tokens_input=turn.verdict.tokens_input,
                tokens_output=turn.verdict.tokens_output,
                cost_usd=turn.verdict.cost_usd,
            )

        insurance = None
        if turn.decision_insurance:
            insurance = DecisionInsuranceResponse(
                best_case=turn.decision_insurance.best_case,
                worst_case=turn.decision_insurance.worst_case,
                risk_level=turn.decision_insurance.risk_level,
                potential_loss=turn.decision_insurance.potential_loss,
                mitigation_plan=turn.decision_insurance.mitigation_plan,
                tokens_input=turn.decision_insurance.tokens_input,
                tokens_output=turn.decision_insurance.tokens_output,
                cost_usd=turn.decision_insurance.cost_usd,
            )

        return TurnResponse(
            id=turn.id,
            chat_id=turn.chat_id,
            user_message=turn.user_message,
            model_set_id=turn.model_set_id,
            strategy=turn.strategy,
            verdict_model=turn.verdict_model,
            status=turn.status.value,
            model_answers=answers,
            verdict=verdict,
            decision_insurance=insurance,
            lesson_id=turn.lesson.id if turn.lesson else None,
            lesson_status=turn.lesson.status.value if turn.lesson else None,
            created_at=turn.created_at,
        )


chat_service = ChatService()
