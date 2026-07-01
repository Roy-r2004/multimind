"""User brain — persistent memory of how a user thinks, learned from disagreements."""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.logging import get_logger
from app.db.models import CostRecord, LessonStatus, UsageKind, UserBrain, VerdictLesson
from app.llm.catalog import get_model, resolve_llm_cost
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry
from app.schemas.api import BrainMemoryResponse, BrainResponse

logger = get_logger(__name__)

DEFAULT_BRAIN_MODEL = "gpt-4.1"


class BrainService:
    async def get_brain(self, db: AsyncSession, auth: AuthContext) -> BrainResponse:
        brain = await self._get_or_create(db, auth)
        try:
            await self._reconcile(db, auth, brain)
        except Exception as exc:
            logger.warning("brain_reconcile_failed", user_id=auth.user.id, error=str(exc))
        return self._response(brain)

    async def forget_lesson(self, db: AsyncSession, auth: AuthContext, lesson_id: str) -> None:
        brain = await self._get_or_create(db, auth)
        memories = [
            m
            for m in (brain.memories or [])
            if not (m.get("source") == "lesson" and m.get("source_id") == lesson_id)
        ]
        if len(memories) != len(brain.memories or []):
            brain.memories = memories
        await self._sync_lesson_count(db, auth, brain)
        await db.flush()

    async def _reconcile(self, db: AsyncSession, auth: AuthContext, brain: UserBrain) -> None:
        result = await db.execute(
            select(VerdictLesson.id).where(VerdictLesson.user_id == auth.user.id)
        )
        valid_ids = {row[0] for row in result.all()}
        memories = [
            m
            for m in (brain.memories or [])
            if m.get("source") != "lesson" or m.get("source_id") in valid_ids
        ]
        count = await self._completed_lesson_count(db, auth)
        changed = memories != (brain.memories or []) or brain.lesson_count != count
        brain.memories = memories
        brain.lesson_count = count
        if changed:
            await db.flush()

    async def _completed_lesson_count(self, db: AsyncSession, auth: AuthContext) -> int:
        result = await db.execute(
            select(func.count())
            .select_from(VerdictLesson)
            .where(
                VerdictLesson.user_id == auth.user.id,
                VerdictLesson.status == LessonStatus.COMPLETED.value,
            )
        )
        return int(result.scalar() or 0)

    async def _sync_lesson_count(self, db: AsyncSession, auth: AuthContext, brain: UserBrain) -> None:
        brain.lesson_count = await self._completed_lesson_count(db, auth)

    async def get_context_for_user(
        self, db: AsyncSession, user_id: str, org_id: str, user_name: str
    ) -> str:
        result = await db.execute(select(UserBrain).where(UserBrain.user_id == user_id))
        brain = result.scalar_one_or_none()
        if brain is None:
            return ""
        return self._format_context(brain)

    async def learn_from_lesson(
        self,
        db: AsyncSession,
        auth: AuthContext,
        lesson: VerdictLesson,
    ) -> None:
        if lesson.status.value != "completed":
            return

        brain = await self._get_or_create(db, auth)
        comparison = lesson.comparison or {}
        lesson_block = comparison.get("lesson") or {}

        system = get_prompt_engine().brain_update_prompt(
            user_name=auth.user.full_name,
            current_summary=brain.summary,
            current_thinking_style=brain.thinking_style,
            current_likes=list(brain.likes or []),
            current_dislikes=list(brain.dislikes or []),
            current_memories=list(brain.memories or []),
            lesson_title=lesson.title,
            lesson_summary=lesson.summary,
            user_position=lesson.user_position,
            disagreement_reason=lesson.disagreement_reason,
            key_insight=lesson_block.get("key_insight", ""),
            what_to_remember=lesson_block.get("what_to_remember") or [],
        )

        model = get_model(DEFAULT_BRAIN_MODEL)
        provider = get_provider_registry().get_provider(model.provider)
        try:
            response = await provider.complete(
                system=system,
                user="Update the user brain profile JSON now.",
                model=model.provider_model,
            )
            parsed = provider.parse_json_response(response.text)
            brain.summary = parsed.get("summary", brain.summary)
            brain.thinking_style = parsed.get("thinking_style", brain.thinking_style)
            brain.likes = self._merge_unique(brain.likes, parsed.get("likes") or [], limit=12)
            brain.dislikes = self._merge_unique(brain.dislikes, parsed.get("dislikes") or [], limit=12)

            new_mem = parsed.get("new_memory") or {}
            memory_entry = {
                "id": str(uuid.uuid4()),
                "source": "lesson",
                "source_id": lesson.id,
                "title": new_mem.get("title", lesson.title),
                "insight": new_mem.get("insight", lesson.summary),
                "likes": new_mem.get("likes") or [],
                "dislikes": new_mem.get("dislikes") or [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            memories = list(brain.memories or [])
            memories.append(memory_entry)
            brain.memories = memories[-50:]
            brain.lesson_count += 1
            brain.user_name = auth.user.full_name

            cost_usd = resolve_llm_cost(
                DEFAULT_BRAIN_MODEL,
                response.tokens_input,
                response.tokens_output,
                response.cost_usd,
            )
            db.add(
                CostRecord(
                    org_id=auth.org_id,
                    chat_id=lesson.chat_id,
                    project_id=None,
                    turn_id=lesson.turn_id,
                    model_id=DEFAULT_BRAIN_MODEL,
                    kind=UsageKind.BRAIN,
                    tokens_input=response.tokens_input,
                    tokens_output=response.tokens_output,
                    cost_usd=cost_usd,
                )
            )
            await db.flush()
            logger.info("brain_updated", user_id=auth.user.id, lesson_id=lesson.id)
        except Exception as exc:
            logger.warning("brain_update_failed", user_id=auth.user.id, error=str(exc))

    async def _get_or_create(self, db: AsyncSession, auth: AuthContext) -> UserBrain:
        result = await db.execute(select(UserBrain).where(UserBrain.user_id == auth.user.id))
        brain = result.scalar_one_or_none()
        if brain is None:
            brain = UserBrain(
                user_id=auth.user.id,
                org_id=auth.org_id,
                user_name=auth.user.full_name,
                summary="",
                thinking_style="",
                likes=[],
                dislikes=[],
                memories=[],
                lesson_count=0,
            )
            db.add(brain)
            await db.flush()
        return brain

    def _merge_unique(self, existing: list[str], incoming: list[str], limit: int) -> list[str]:
        seen: set[str] = set()
        merged: list[str] = []
        for item in [*existing, *incoming]:
            key = item.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item.strip())
        return merged[-limit:]

    def _format_context(self, brain: UserBrain) -> str:
        if not brain.summary and not brain.memories and not brain.likes and not brain.dislikes:
            return ""
        lines = [f"**Profile: {brain.user_name}**", ""]
        if brain.summary:
            lines.append(f"How they think: {brain.summary}")
        if brain.thinking_style:
            lines.append(f"Reasoning style: {brain.thinking_style}")
        if brain.likes:
            lines.append("Tends to prefer: " + "; ".join(brain.likes[:10]))
        if brain.dislikes:
            lines.append("Tends to reject: " + "; ".join(brain.dislikes[:10]))
        recent = (brain.memories or [])[-6:]
        if recent:
            lines.append("")
            lines.append("Recent learnings:")
            for m in recent:
                lines.append(f"- {m.get('title', 'Lesson')}: {m.get('insight', '')}")
        return "\n".join(lines)

    def _coerce_str_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _coerce_optional_str(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def _response(self, brain: UserBrain) -> BrainResponse:
        memories = []
        for raw in brain.memories or []:
            if not isinstance(raw, dict):
                continue
            memories.append(
                BrainMemoryResponse(
                    id=str(raw.get("id") or ""),
                    source=str(raw.get("source") or "lesson"),
                    source_id=self._coerce_optional_str(raw.get("source_id")),
                    title=str(raw.get("title") or ""),
                    insight=str(raw.get("insight") or ""),
                    likes=self._coerce_str_list(raw.get("likes")),
                    dislikes=self._coerce_str_list(raw.get("dislikes")),
                    created_at=self._coerce_optional_str(raw.get("created_at")),
                )
            )
        return BrainResponse(
            user_name=brain.user_name,
            summary=brain.summary,
            thinking_style=brain.thinking_style,
            likes=list(brain.likes or []),
            dislikes=list(brain.dislikes or []),
            memories=memories,
            lesson_count=brain.lesson_count,
            updated_at=brain.updated_at,
        )


brain_service = BrainService()
