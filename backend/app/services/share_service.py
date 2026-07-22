"""Share link service — public read-only chat access."""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
from app.db.models import ModelSet, ShareLink, Turn, User
from app.schemas.api import ShareLinkResponse, SharedChatResponse
from app.services.chat_service import CHALLENGE_TURN_MARKER, chat_service


class ShareService:
    async def create_link(
        self,
        db: AsyncSession,
        auth: AuthContext,
        chat_id: str,
        *,
        expires_days: int | None = 30,
    ) -> ShareLinkResponse:
        chat = await chat_service.get_chat(db, auth, chat_id)
        token = secrets.token_urlsafe(32)
        expires_at = (
            datetime.now(UTC) + timedelta(days=expires_days) if expires_days else None
        )
        link = ShareLink(
            chat_id=chat.id,
            token=token,
            created_by=auth.user.id,
            expires_at=expires_at,
        )
        db.add(link)
        await db.flush()

        settings = get_settings()
        return ShareLinkResponse(
            token=token,
            url=f"{settings.public_app_url.rstrip('/')}/shared/{token}",
            expires_at=expires_at,
        )

    async def get_shared_chat(self, db: AsyncSession, token: str) -> SharedChatResponse:
        result = await db.execute(
            select(ShareLink)
            .where(ShareLink.token == token, ShareLink.revoked_at.is_(None))
            .options(selectinload(ShareLink.chat))
        )
        link = result.scalar_one_or_none()
        if link is None:
            raise NotFoundError("Share link", token)

        if link.expires_at and link.expires_at < datetime.now(UTC):
            raise NotFoundError("Share link expired", token)

        chat = link.chat
        if chat is None:
            raise NotFoundError("Chat", token)

        creator = await db.get(User, link.created_by)
        shared_by = creator.full_name if creator else "MultiAI user"

        turns_result = await db.execute(
            select(Turn)
            .where(
                Turn.chat_id == chat.id,
                (Turn.error_message.is_(None)) | (Turn.error_message != CHALLENGE_TURN_MARKER),
            )
            .options(
                selectinload(Turn.model_answers),
                selectinload(Turn.verdict),
                selectinload(Turn.decision_insurance),
            )
            .order_by(Turn.created_at.asc())
        )
        turns = [chat_service._turn_response(t) for t in turns_result.scalars().all()]

        model_set_name = "Model Set"
        if turns:
            ms_result = await db.execute(
                select(ModelSet).where(ModelSet.slug == turns[-1].model_set_id)
            )
            ms = ms_result.scalar_one_or_none()
            if ms:
                model_set_name = ms.name

        return SharedChatResponse(
            title=chat.title,
            shared_by=shared_by,
            model_set_name=model_set_name,
            turns=turns,
        )


share_service = ShareService()
