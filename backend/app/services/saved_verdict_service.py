"""Personal saved verdict snapshot service."""

from sqlalchemy import String, cast, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.models import Chat, OrgRole, SavedVerdict, Turn, TurnStatus, Verdict
from app.schemas.api import (
    SavedVerdictDeleteResponse,
    SavedVerdictListItemResponse,
    SavedVerdictPurgeResponse,
    SavedVerdictSaveResponse,
    SavedVerdictUnsaveResponse,
)

SAVED_VERDICT_UNIQUE_CONSTRAINT = "uq_saved_verdict_user_source"
SAVED_VERDICT_UNIQUE_COLUMNS = (
    "saved_verdicts.org_id",
    "saved_verdicts.user_id",
    "saved_verdicts.source_verdict_id",
)


def savable_verdict_statement(verdict_id: str, org_id: str):
    return (
        select(Verdict, Turn, Chat)
        .join(Turn, Turn.id == Verdict.turn_id)
        .join(Chat, Chat.id == Turn.chat_id)
        .where(
            Verdict.id == verdict_id,
            Chat.org_id == org_id,
            cast(Turn.status, String).in_(
                [TurnStatus.COMPLETED.name, TurnStatus.PARTIAL.name]
            ),
        )
    )


class SavedVerdictService:
    async def save_verdict(
        self, db: AsyncSession, auth: AuthContext, verdict_id: str
    ) -> SavedVerdictSaveResponse:
        existing = await self._get_saved_by_source(db, auth, verdict_id)
        if existing is not None:
            return self._save_response(
                existing, original_chat_exists=await self._original_chat_exists(db, existing)
            )

        result = await db.execute(savable_verdict_statement(verdict_id, auth.org_id))
        row = result.first()
        if row is None:
            raise NotFoundError("Verdict", verdict_id)

        verdict, turn, chat = row
        saved = SavedVerdict(
            org_id=auth.org_id,
            user_id=auth.user.id,
            source_verdict_id=verdict.id,
            source_turn_id=turn.id,
            source_chat_id=chat.id,
            source_chat_title=chat.title,
            source_user_message=turn.user_message,
            verdict_text=verdict.text,
            verdict_reason=verdict.reason,
            verdict_model_id=verdict.model_id,
            strategy=verdict.strategy,
        )
        try:
            async with db.begin_nested():
                db.add(saved)
                await db.flush()
        except IntegrityError as exc:
            existing = await self._existing_after_unique_race(db, auth, verdict_id, exc)
            if existing is None:
                raise
            return self._save_response(
                existing, original_chat_exists=await self._original_chat_exists(db, existing)
            )
        return self._save_response(saved, original_chat_exists=True)

    async def unsave_verdict(
        self, db: AsyncSession, auth: AuthContext, verdict_id: str
    ) -> SavedVerdictUnsaveResponse:
        await db.execute(
            delete(SavedVerdict).where(
                SavedVerdict.org_id == auth.org_id,
                SavedVerdict.user_id == auth.user.id,
                SavedVerdict.source_verdict_id == verdict_id,
            )
        )
        await db.flush()
        return SavedVerdictUnsaveResponse(verdict_id=verdict_id, saved=False)

    async def delete_saved_verdict(
        self, db: AsyncSession, auth: AuthContext, saved_verdict_id: str
    ) -> SavedVerdictDeleteResponse:
        deleted = await db.execute(
            delete(SavedVerdict).where(
                SavedVerdict.id == saved_verdict_id,
                SavedVerdict.org_id == auth.org_id,
                SavedVerdict.user_id == auth.user.id,
            )
        )
        if deleted.rowcount != 1:
            raise NotFoundError("SavedVerdict", saved_verdict_id)
        await db.flush()
        return SavedVerdictDeleteResponse(id=saved_verdict_id, deleted=True)

    async def purge_organization_saved_verdicts(
        self, db: AsyncSession, auth: AuthContext
    ) -> SavedVerdictPurgeResponse:
        if auth.role not in (OrgRole.OWNER, OrgRole.ADMIN):
            raise ForbiddenError("Organization admin access required")
        deleted = await db.execute(delete(SavedVerdict).where(SavedVerdict.org_id == auth.org_id))
        await db.flush()
        return SavedVerdictPurgeResponse(deleted_count=deleted.rowcount or 0)

    async def list_saved_verdicts(
        self, db: AsyncSession, auth: AuthContext
    ) -> list[SavedVerdictListItemResponse]:
        result = await db.execute(
            select(SavedVerdict, Chat.id)
            .outerjoin(
                Chat,
                (Chat.id == SavedVerdict.source_chat_id) & (Chat.org_id == SavedVerdict.org_id),
            )
            .where(SavedVerdict.org_id == auth.org_id, SavedVerdict.user_id == auth.user.id)
            .order_by(SavedVerdict.saved_at.desc(), SavedVerdict.id.desc())
        )
        items: list[SavedVerdictListItemResponse] = []
        for saved, chat_id in result.all():
            original_chat_exists = chat_id is not None
            items.append(self._list_item(saved, original_chat_exists))
        return items

    async def saved_source_verdict_ids(
        self, db: AsyncSession, auth: AuthContext, verdict_ids: list[str]
    ) -> set[str]:
        if not verdict_ids:
            return set()
        result = await db.execute(
            select(SavedVerdict.source_verdict_id).where(
                SavedVerdict.org_id == auth.org_id,
                SavedVerdict.user_id == auth.user.id,
                SavedVerdict.source_verdict_id.in_(verdict_ids),
            )
        )
        return set(result.scalars().all())

    async def _get_saved_by_source(
        self, db: AsyncSession, auth: AuthContext, verdict_id: str
    ) -> SavedVerdict | None:
        result = await db.execute(
            select(SavedVerdict).where(
                SavedVerdict.org_id == auth.org_id,
                SavedVerdict.user_id == auth.user.id,
                SavedVerdict.source_verdict_id == verdict_id,
            )
        )
        return result.scalar_one_or_none()

    async def _existing_after_unique_race(
        self,
        db: AsyncSession,
        auth: AuthContext,
        verdict_id: str,
        exc: IntegrityError,
    ) -> SavedVerdict | None:
        if not self._is_expected_unique_violation(exc):
            return None
        return await self._get_saved_by_source(db, auth, verdict_id)

    def _is_expected_unique_violation(self, exc: IntegrityError) -> bool:
        orig = getattr(exc, "orig", None)
        sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        constraint_name = getattr(orig, "constraint_name", None)
        diag = getattr(orig, "diag", None)
        if constraint_name is None and diag is not None:
            constraint_name = getattr(diag, "constraint_name", None)
        cause = getattr(orig, "__cause__", None)
        if constraint_name is None and cause is not None:
            constraint_name = getattr(cause, "constraint_name", None)
        if sqlstate == "23505" and constraint_name == SAVED_VERDICT_UNIQUE_CONSTRAINT:
            return True

        message = str(orig or exc).lower()
        if SAVED_VERDICT_UNIQUE_CONSTRAINT in message:
            return True
        return all(column in message for column in SAVED_VERDICT_UNIQUE_COLUMNS)

    async def _original_chat_exists(self, db: AsyncSession, saved: SavedVerdict) -> bool:
        if not saved.source_chat_id:
            return False
        result = await db.execute(
            select(Chat.id).where(Chat.id == saved.source_chat_id, Chat.org_id == saved.org_id)
        )
        return result.scalar_one_or_none() is not None

    def _list_item(
        self, saved: SavedVerdict, original_chat_exists: bool
    ) -> SavedVerdictListItemResponse:
        return SavedVerdictListItemResponse(
            id=saved.id,
            source_verdict_id=saved.source_verdict_id,
            source_turn_id=saved.source_turn_id,
            source_chat_id=saved.source_chat_id if original_chat_exists else None,
            source_chat_title=saved.source_chat_title,
            source_user_message=saved.source_user_message,
            verdict_text=saved.verdict_text,
            verdict_reason=saved.verdict_reason,
            verdict_model_id=saved.verdict_model_id,
            strategy=saved.strategy,
            saved_at=saved.saved_at,
            original_chat_exists=original_chat_exists,
            original_chat_route=f"/chat?chatId={saved.source_chat_id}"
            if original_chat_exists and saved.source_chat_id
            else None,
        )

    def _save_response(
        self, saved: SavedVerdict, original_chat_exists: bool
    ) -> SavedVerdictSaveResponse:
        item = self._list_item(saved, original_chat_exists)
        return SavedVerdictSaveResponse(
            verdict_id=saved.source_verdict_id,
            saved=True,
            **item.model_dump(),
        )


saved_verdict_service = SavedVerdictService()
