"""Verdict disagreement lessons — build structured user vs model comparisons."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.db.models import (
    Chat,
    CostRecord,
    LessonStatus,
    Turn,
    TurnStatus,
    UsageKind,
    VerdictLesson,
)
from app.services.brain_service import brain_service
from app.llm.catalog import get_model, resolve_llm_cost
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry
from app.schemas.api import (
    DiscussMessageItem,
    DiscussResponse,
    LessonComparisonResponse,
    LessonDetailResponse,
    LessonListItemResponse,
    VerdictDisagreeRequest,
)

logger = get_logger(__name__)

CHAFIC_OPENING = (
    "I read the verdict and your pushback matters. Walk me through what feels wrong — "
    "what did the council get wrong, and what would you do instead?"
)


class LessonService:
    async def list_lessons(
        self, db: AsyncSession, auth: AuthContext
    ) -> list[LessonListItemResponse]:
        result = await db.execute(
            select(VerdictLesson)
            .where(VerdictLesson.user_id == auth.user.id)
            .order_by(VerdictLesson.created_at.desc())
        )
        return [self._list_item(lesson) for lesson in result.scalars().all()]

    async def get_lesson(
        self, db: AsyncSession, auth: AuthContext, lesson_id: str
    ) -> LessonDetailResponse:
        lesson = await self._get_lesson(db, auth, lesson_id)
        return self._detail(lesson)

    async def delete_lesson(self, db: AsyncSession, auth: AuthContext, lesson_id: str) -> None:
        lesson = await self._get_lesson(db, auth, lesson_id)
        await brain_service.forget_lesson(db, auth, lesson.id)
        await db.delete(lesson)

    async def start_discussion(
        self, db: AsyncSession, auth: AuthContext, turn_id: str
    ) -> DiscussResponse:
        turn, _chat, verdict_model, _answer_context = await self._load_turn_context(
            db, auth, turn_id
        )
        if turn.lesson is not None:
            lesson = turn.lesson
            if lesson.status == LessonStatus.COMPLETED:
                raise ConflictError("A lesson already exists for this verdict")
            if lesson.status == LessonStatus.BUILDING:
                raise ConflictError("Lesson is still being built")
            if lesson.status == LessonStatus.FAILED:
                raise ConflictError("Lesson build failed — delete it and try again")
            if not lesson.discussion_messages:
                lesson.discussion_messages = [{"role": "Chafic", "content": CHAFIC_OPENING}]
                await db.flush()
            return self._discuss_response(lesson)

        lesson = VerdictLesson(
            turn_id=turn.id,
            chat_id=turn.chat_id,
            org_id=auth.org_id,
            user_id=auth.user.id,
            user_name=auth.user.full_name,
            user_message=turn.user_message,
            disagreement_reason="Discussion in progress",
            user_position="Discussion in progress",
            verdict_model_id=turn.verdict.model_id,
            verdict_model_name=verdict_model.name,
            verdict_text=turn.verdict.text,
            verdict_reason=turn.verdict.reason,
            strategy=turn.strategy,
            title="Discussing disagreement…",
            summary="",
            comparison={},
            discussion_messages=[{"role": "Chafic", "content": CHAFIC_OPENING}],
            status=LessonStatus.DISCUSSING,
        )
        db.add(lesson)
        await db.flush()
        # Return immediately — opening greeting is static so the UI is never blocked
        # waiting on the LLM cold start.
        return self._discuss_response(lesson)

    async def discuss_message(
        self,
        db: AsyncSession,
        auth: AuthContext,
        turn_id: str,
        message: str,
    ) -> DiscussResponse:
        turn, _chat, verdict_model, answer_context = await self._load_turn_context(
            db, auth, turn_id
        )
        if turn.lesson is None or turn.lesson.status != LessonStatus.DISCUSSING:
            raise ConflictError("Start a discussion before sending messages")

        lesson = turn.lesson
        messages = list(lesson.discussion_messages or [])
        messages.append({"role": auth.user.full_name, "content": message.strip()})

        reply = await self._chafic_reply(
            turn=turn,
            verdict_model=verdict_model,
            answer_context=answer_context,
            messages=messages,
            user_name=auth.user.full_name,
        )
        messages.append({"role": "Chafic", "content": reply})
        lesson.discussion_messages = messages
        await db.flush()
        return self._discuss_response(lesson)

    async def finalize_discussion(
        self, db: AsyncSession, auth: AuthContext, turn_id: str
    ) -> LessonDetailResponse:
        turn, chat, verdict_model, answer_context = await self._load_turn_context(
            db, auth, turn_id
        )
        if turn.lesson is None or turn.lesson.status != LessonStatus.DISCUSSING:
            raise ConflictError("No active discussion to finalize")

        lesson = turn.lesson
        messages = lesson.discussion_messages or []
        user_turns = [m for m in messages if m.get("role") != "Chafic"]
        if len(user_turns) < 1:
            raise ConflictError("Share your disagreement before building the lesson")

        provider = get_provider_registry().get_provider(verdict_model.provider)
        extract_system = get_prompt_engine().disagree_finalize_prompt(
            user_name=auth.user.full_name,
            user_message=turn.user_message,
            strategy=turn.strategy.value,
            verdict_model_name=verdict_model.name,
            verdict_text=turn.verdict.text,
            messages=messages,
        )
        try:
            extract_resp = await provider.complete(
                system=extract_system,
                user="Extract the disagreement fields as JSON.",
                model=verdict_model.provider_model,
            )
            extracted = provider.parse_json_response(extract_resp.text)
            lesson.disagreement_reason = extracted.get(
                "disagreement_reason", lesson.disagreement_reason
            )
            lesson.user_position = extracted.get("user_position", lesson.user_position)
        except Exception as exc:
            logger.warning("discuss_extract_failed", turn_id=turn_id, error=str(exc))

        lesson.status = LessonStatus.BUILDING
        lesson.title = "Building lesson…"
        await db.flush()
        await self._build_lesson_from_context(
            db, auth, turn, chat, lesson, verdict_model, answer_context
        )
        await db.refresh(lesson)
        return self._detail(lesson)

    async def disagree_with_verdict(
        self,
        db: AsyncSession,
        auth: AuthContext,
        turn_id: str,
        data: VerdictDisagreeRequest,
    ) -> LessonDetailResponse:
        turn, chat, verdict_model, answer_context = await self._load_turn_context(
            db, auth, turn_id
        )
        if turn.lesson is not None:
            if turn.lesson.status == LessonStatus.DISCUSSING:
                raise ConflictError("Use the discussion flow to disagree with this verdict")
            return self._detail(turn.lesson)

        lesson = VerdictLesson(
            turn_id=turn.id,
            chat_id=turn.chat_id,
            org_id=auth.org_id,
            user_id=auth.user.id,
            user_name=auth.user.full_name,
            user_message=turn.user_message,
            disagreement_reason=data.reason.strip(),
            user_position=data.user_position.strip(),
            verdict_model_id=turn.verdict.model_id,
            verdict_model_name=verdict_model.name,
            verdict_text=turn.verdict.text,
            verdict_reason=turn.verdict.reason,
            strategy=turn.strategy,
            title="Building lesson…",
            summary="",
            comparison={},
            discussion_messages=[],
            status=LessonStatus.BUILDING,
        )
        db.add(lesson)
        await db.flush()
        await self._build_lesson_from_context(
            db, auth, turn, chat, lesson, verdict_model, answer_context
        )
        await db.refresh(lesson)
        return self._detail(lesson)

    async def _load_turn_context(
        self, db: AsyncSession, auth: AuthContext, turn_id: str
    ) -> tuple[Turn, Chat | None, object, list[dict]]:
        result = await db.execute(
            select(Turn)
            .join(Chat, Chat.id == Turn.chat_id)
            .where(Turn.id == turn_id, Chat.org_id == auth.org_id)
            .options(
                selectinload(Turn.model_answers),
                selectinload(Turn.verdict),
                selectinload(Turn.lesson),
            )
        )
        turn = result.scalar_one_or_none()
        if turn is None:
            raise NotFoundError("Turn", turn_id)
        if turn.status not in (TurnStatus.COMPLETED, TurnStatus.PARTIAL):
            raise ConflictError("Turn must be completed before disagreeing with the verdict")
        if turn.verdict is None:
            raise ConflictError("This turn has no verdict to disagree with")

        chat = await db.get(Chat, turn.chat_id)
        verdict_model = get_model(turn.verdict.model_id)
        answer_context = []
        for answer in turn.model_answers:
            model = get_model(answer.model_id)
            answer_context.append(
                {
                    "model_id": answer.model_id,
                    "model_name": model.name,
                    "text": answer.text or "",
                    "confidence": answer.confidence or 0,
                    "failed": answer.status.value == "failed",
                    "error_message": answer.error_message,
                }
            )
        return turn, chat, verdict_model, answer_context

    async def _chafic_reply(
        self,
        *,
        turn: Turn,
        verdict_model: object,
        answer_context: list[dict],
        messages: list[dict[str, str]],
        user_name: str,
        fallback: str | None = None,
    ) -> str:
        system = get_prompt_engine().disagree_discuss_prompt(
            user_name=user_name,
            user_message=turn.user_message,
            strategy=turn.strategy.value,
            model_answers=answer_context,
            verdict_model_name=verdict_model.name,
            verdict_text=turn.verdict.text,
            verdict_reason=turn.verdict.reason,
            messages=messages,
        )
        provider = get_provider_registry().get_provider(verdict_model.provider)
        try:
            response = await provider.complete(
                system=system,
                user="Respond as Chafic with your next message.",
                model=verdict_model.provider_model,
                max_tokens=800,
            )
            text = (response.text or "").strip()
            return text or fallback or CHAFIC_OPENING
        except Exception as exc:
            logger.error("discuss_reply_failed", turn_id=turn.id, error=str(exc))
            return fallback or CHAFIC_OPENING

    async def _build_lesson_from_context(
        self,
        db: AsyncSession,
        auth: AuthContext,
        turn: Turn,
        chat: Chat | None,
        lesson: VerdictLesson,
        verdict_model: object,
        answer_context: list[dict],
    ) -> None:
        system = get_prompt_engine().verdict_lesson_prompt(
            user_name=auth.user.full_name,
            user_message=turn.user_message,
            strategy=turn.strategy.value,
            model_answers=answer_context,
            verdict_model_name=verdict_model.name,
            verdict_text=turn.verdict.text,
            verdict_reason=turn.verdict.reason,
            disagreement_reason=lesson.disagreement_reason,
            user_position=lesson.user_position,
            discussion_messages=lesson.discussion_messages or [],
        )

        provider = get_provider_registry().get_provider(verdict_model.provider)
        try:
            response = await provider.complete(
                system=system,
                user="Produce the disagreement lesson JSON now.",
                model=verdict_model.provider_model,
            )
            parsed = provider.parse_json_response(response.text)
            comparison = self._normalize_comparison(parsed)
            lesson.title = parsed.get("title", "Verdict disagreement lesson")
            lesson.summary = parsed.get("summary", "")
            lesson.comparison = comparison
            lesson.status = LessonStatus.COMPLETED
            lesson.tokens_input += response.tokens_input
            lesson.tokens_output += response.tokens_output
            lesson.cost_usd += resolve_llm_cost(
                turn.verdict.model_id,
                response.tokens_input,
                response.tokens_output,
                response.cost_usd,
            )

            db.add(
                CostRecord(
                    org_id=auth.org_id,
                    chat_id=turn.chat_id,
                    project_id=chat.project_id if chat else None,
                    turn_id=turn.id,
                    model_id=turn.verdict.model_id,
                    kind=UsageKind.LESSON,
                    tokens_input=response.tokens_input,
                    tokens_output=response.tokens_output,
                    cost_usd=lesson.cost_usd,
                )
            )
            await brain_service.learn_from_lesson(db, auth, lesson)
        except Exception as exc:
            logger.error("lesson_build_failed", turn_id=turn.id, error=str(exc))
            lesson.status = LessonStatus.FAILED
            lesson.error_message = str(exc)
            lesson.title = "Lesson could not be built"
            lesson.summary = "The comparison could not be generated. Try again later."
            lesson.comparison = {}

        await db.flush()

    def _discuss_response(self, lesson: VerdictLesson) -> DiscussResponse:
        messages = lesson.discussion_messages or []
        user_turns = [m for m in messages if m.get("role") != "Chafic"]
        return DiscussResponse(
            lesson_id=lesson.id,
            messages=[DiscussMessageItem(role=m["role"], content=m["content"]) for m in messages],
            can_finalize=len(user_turns) >= 1,
        )

    async def _get_lesson(
        self, db: AsyncSession, auth: AuthContext, lesson_id: str
    ) -> VerdictLesson:
        result = await db.execute(
            select(VerdictLesson).where(
                VerdictLesson.id == lesson_id,
                VerdictLesson.user_id == auth.user.id,
            )
        )
        lesson = result.scalar_one_or_none()
        if lesson is None:
            raise NotFoundError("Lesson", lesson_id)
        return lesson

    def _normalize_comparison(self, parsed: dict) -> dict:
        lesson_block = parsed.get("lesson") or {}
        return {
            "overview": parsed.get("overview", ""),
            "user_position_summary": parsed.get("user_position_summary", ""),
            "model_position_summary": parsed.get("model_position_summary", ""),
            "agreements": parsed.get("agreements") or [],
            "disagreements": parsed.get("disagreements") or [],
            "evidence": parsed.get("evidence") or [],
            "assumptions": parsed.get("assumptions") or {"user": [], "model": []},
            "blind_spots": parsed.get("blind_spots") or {"user": [], "model": []},
            "lesson": {
                "headline": lesson_block.get("headline", ""),
                "key_insight": lesson_block.get("key_insight", ""),
                "what_to_remember": lesson_block.get("what_to_remember") or [],
                "when_user_might_be_right": lesson_block.get("when_user_might_be_right", ""),
                "when_model_might_be_right": lesson_block.get("when_model_might_be_right", ""),
                "recommended_next_step": lesson_block.get("recommended_next_step", ""),
            },
        }

    def _list_item(self, lesson: VerdictLesson) -> LessonListItemResponse:
        return LessonListItemResponse(
            id=lesson.id,
            turn_id=lesson.turn_id,
            chat_id=lesson.chat_id,
            title=lesson.title,
            summary=lesson.summary,
            user_name=lesson.user_name,
            verdict_model_name=lesson.verdict_model_name,
            status=lesson.status.value,
            created_at=lesson.created_at,
        )

    def _detail(self, lesson: VerdictLesson) -> LessonDetailResponse:
        return LessonDetailResponse(
            id=lesson.id,
            turn_id=lesson.turn_id,
            chat_id=lesson.chat_id,
            user_name=lesson.user_name,
            user_message=lesson.user_message,
            disagreement_reason=lesson.disagreement_reason,
            user_position=lesson.user_position,
            verdict_model_id=lesson.verdict_model_id,
            verdict_model_name=lesson.verdict_model_name,
            verdict_text=lesson.verdict_text,
            verdict_reason=lesson.verdict_reason,
            strategy=lesson.strategy,
            title=lesson.title,
            summary=lesson.summary,
            comparison=LessonComparisonResponse.model_validate(lesson.comparison),
            discussion_messages=[
                DiscussMessageItem(role=m["role"], content=m["content"])
                for m in (lesson.discussion_messages or [])
            ],
            status=lesson.status.value,
            error_message=lesson.error_message,
            created_at=lesson.created_at,
        )


lesson_service = LessonService()
