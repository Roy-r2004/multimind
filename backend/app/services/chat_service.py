"""Chat and turn business logic."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
from app.db.models import (
    Chat,
    CostRecord,
    DecisionInsurance,
    ModelAnswer,
    ModelAnswerStatus,
    ModelSet,
    ShareLink,
    Strategy,
    Turn,
    TurnStatus,
    Verdict,
)
from app.db.session import AsyncSessionLocal
from app.llm.catalog import get_model
from app.services.brain_service import brain_service
from app.llm.orchestrator import TurnContext, get_orchestrator
from app.schemas.api import (
    ChatCreateRequest,
    ChatResponse,
    ChatUpdateRequest,
    DecisionInsuranceResponse,
    ModelAnswerResponse,
    TurnCreateRequest,
    TurnResponse,
    VerdictResponse,
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
        chat = await self.get_chat(db, auth, chat_id)
        turn_ids = select(Turn.id).where(Turn.chat_id == chat_id)

        await db.execute(delete(CostRecord).where(CostRecord.chat_id == chat_id))
        await db.execute(delete(DecisionInsurance).where(DecisionInsurance.turn_id.in_(turn_ids)))
        await db.execute(delete(Verdict).where(Verdict.turn_id.in_(turn_ids)))
        await db.execute(delete(ModelAnswer).where(ModelAnswer.turn_id.in_(turn_ids)))
        await db.execute(delete(ShareLink).where(ShareLink.chat_id == chat_id))
        await db.execute(delete(Turn).where(Turn.chat_id == chat_id))
        await db.delete(chat)

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
            decision_insurance_enabled=True,
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
                turn = await self.get_turn(db, auth, turn_id)
            status = turn.status
            if status in ("completed", "partial"):
                yield {"type": "turn_completed", "data": turn.model_dump(mode="json")}
                return
            if status == "failed":
                err = next((a.error_message for a in turn.model_answers if a.error_message), None)
                yield {"type": "turn_failed", "data": {"error": err or "Turn failed"}}
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
            .options(selectinload(Turn.model_answers))
        )
        turn = result.scalar_one_or_none()
        if turn is None:
            raise NotFoundError("Turn", turn_id)

        if turn.status in (TurnStatus.COMPLETED, TurnStatus.PARTIAL):
            yield {"type": "turn_completed", "data": self._turn_response(turn).model_dump(mode="json")}
            return

        if turn.status == TurnStatus.RUNNING:
            async for event in self._poll_turn_until_done(auth, turn_id):
                yield event
            return

        if turn.status == TurnStatus.FAILED:
            err = next((a.error_message for a in turn.model_answers if a.error_message), None) or turn.error_message
            yield {"type": "turn_failed", "data": {"error": err or "Turn failed"}}
            yield {"type": "turn_completed", "data": self._turn_response(turn).model_dump(mode="json")}
            return

        model_set = await self._resolve_model_set(db, auth, turn.model_set_id)
        chat = await self.get_chat(db, auth, turn.chat_id)
        user_brain_context = await brain_service.get_context_for_user(
            db, auth.user.id, auth.org_id, auth.user.full_name
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
            decision_insurance_enabled=True,
            skip_answer_seed=True,
        )

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def on_event(event: str, data: dict[str, Any]) -> None:
            await queue.put({"type": event, "data": data})

        async def orchestrate() -> None:
            try:
                async with AsyncSessionLocal() as run_db:
                    await get_orchestrator().run(run_db, ctx, on_event=on_event)
                    await run_db.commit()
                async with AsyncSessionLocal() as read_db:
                    final = await self.get_turn(read_db, auth, turn_id)
                    await queue.put({"type": "turn_completed", "data": final.model_dump(mode="json")})
            except Exception as exc:
                await queue.put({"type": "error", "data": {"message": str(exc)}})
            finally:
                await queue.put(None)

        asyncio.create_task(orchestrate())

        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=12.0)
            except asyncio.TimeoutError:
                yield {"type": "ping", "data": {"ts": time.time()}}
                continue
            if item is None:
                break
            yield item
            if item["type"] in ("turn_completed", "error"):
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
        return self._turn_response(turn)

    async def list_turns(
        self, db: AsyncSession, auth: AuthContext, chat_id: str
    ) -> list[TurnResponse]:
        await self.get_chat(db, auth, chat_id)
        result = await db.execute(
            select(Turn)
            .where(Turn.chat_id == chat_id)
            .options(
                selectinload(Turn.model_answers),
                selectinload(Turn.verdict),
                selectinload(Turn.decision_insurance),
                selectinload(Turn.lesson),
            )
            .order_by(Turn.created_at.asc())
        )
        return [self._turn_response(t) for t in result.scalars().all()]

    def _chat_response(self, chat: Chat) -> ChatResponse:
        return ChatResponse(
            id=chat.id,  # type: ignore[arg-type]
            title=chat.title,
            project_id=chat.project_id,
            updated_at=chat.updated_at,
        )

    def _turn_response(self, turn: Turn) -> TurnResponse:
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
                model_id=turn.verdict.model_id,
                strategy=turn.verdict.strategy,
                text=turn.verdict.text,
                reason=turn.verdict.reason,
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
