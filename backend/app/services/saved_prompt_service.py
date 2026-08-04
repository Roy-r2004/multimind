"""Saved user prompts with the final verdict from the same chat turn."""

from __future__ import annotations

import re

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import Chat, ContentLabel, SavedPrompt, SavedPromptLabel, Turn, Verdict
from app.schemas.api import SavedPromptLabelBrief, SavedPromptResponse
from app.services.saved_document_service import saved_document_service

VERDICT_REQUIRED_MESSAGE = "The verdict must finish before this prompt can be saved."


class SavedPromptService:
    async def create_from_turn(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        turn_id: str,
        prompt_text: str | None,
        title: str | None,
        label_ids: list[str],
        label_names: list[str],
    ) -> SavedPromptResponse:
        turn, chat, verdict = await self._load_turn_with_verdict(db, auth, turn_id)
        text = (prompt_text if prompt_text is not None else turn.user_message or "").strip()
        if not text:
            raise ValidationError("Prompt text is required")

        verdict_text = (verdict.text or "").strip()
        if not verdict_text:
            raise ValidationError(VERDICT_REQUIRED_MESSAGE)

        cleaned_title = self._clean_optional_title(title)
        row = SavedPrompt(
            org_id=auth.org_id,
            user_id=auth.user.id,
            title=cleaned_title,
            prompt_text=text,
            verdict_text=verdict_text,
            chat_id=chat.id,
            turn_id=turn.id,
        )
        db.add(row)
        await db.flush()

        labels = await self._resolve_labels(db, auth, label_ids, label_names)
        for label in labels:
            db.add(SavedPromptLabel(prompt_id=row.id, label_id=label.id))
        await db.flush()
        return await self.get_prompt(db, auth, row.id)

    async def update_prompt(
        self,
        db: AsyncSession,
        auth: AuthContext,
        prompt_id: str,
        *,
        title: str | None,
        prompt_text: str | None,
        label_ids: list[str] | None,
    ) -> SavedPromptResponse:
        row = await self._get_prompt(db, auth, prompt_id)
        if title is not None:
            row.title = self._clean_optional_title(title)
        if prompt_text is not None:
            cleaned = prompt_text.strip()
            if not cleaned:
                raise ValidationError("Prompt text is required")
            row.prompt_text = cleaned
        if label_ids is not None:
            await db.execute(
                delete(SavedPromptLabel).where(SavedPromptLabel.prompt_id == row.id)
            )
            labels = await self._resolve_labels(db, auth, label_ids, [])
            for label in labels:
                db.add(SavedPromptLabel(prompt_id=row.id, label_id=label.id))
        # verdict_text is intentionally immutable via normal edit.
        await db.flush()
        return await self.get_prompt(db, auth, row.id)

    async def delete_prompt(self, db: AsyncSession, auth: AuthContext, prompt_id: str) -> None:
        row = await self._get_prompt(db, auth, prompt_id)
        await db.delete(row)
        await db.flush()

    async def get_prompt(
        self, db: AsyncSession, auth: AuthContext, prompt_id: str
    ) -> SavedPromptResponse:
        row = await self._get_prompt(db, auth, prompt_id, with_labels=True)
        return self._response(row)

    async def search(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        q: str | None = None,
        label_id: str | None = None,
    ) -> list[SavedPromptResponse]:
        stmt = (
            select(SavedPrompt)
            .where(
                SavedPrompt.org_id == auth.org_id,
                SavedPrompt.user_id == auth.user.id,
            )
            .options(selectinload(SavedPrompt.labels))
            .order_by(SavedPrompt.updated_at.desc())
        )
        if label_id:
            stmt = stmt.join(SavedPromptLabel).where(SavedPromptLabel.label_id == label_id)
        if q and q.strip():
            term = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    SavedPrompt.prompt_text.ilike(term),
                    SavedPrompt.title.ilike(term),
                    SavedPrompt.verdict_text.ilike(term),
                )
            )
        result = await db.execute(stmt)
        rows = list(result.scalars().unique().all())
        if q and q.strip():
            needle = q.strip().lower()
            rows = [
                row
                for row in rows
                if needle in (row.prompt_text or "").lower()
                or needle in (row.title or "").lower()
                or needle in (row.verdict_text or "").lower()
                or any(needle in label.name.lower() for label in row.labels)
            ]
        return [self._response(row) for row in rows]

    async def _load_turn_with_verdict(
        self, db: AsyncSession, auth: AuthContext, turn_id: str
    ) -> tuple[Turn, Chat, Verdict]:
        result = await db.execute(
            select(Turn)
            .where(Turn.id == turn_id)
            .options(
                selectinload(Turn.chat),
                selectinload(Turn.verdict),
            )
        )
        turn = result.scalar_one_or_none()
        if turn is None or turn.chat is None or turn.chat.org_id != auth.org_id:
            raise NotFoundError("Turn", turn_id)
        verdict = turn.verdict
        if verdict is None or not (verdict.text or "").strip():
            raise ValidationError(VERDICT_REQUIRED_MESSAGE)
        # Guard: stored verdict must belong to this exact turn (relationship + column).
        if str(verdict.turn_id) != str(turn.id):
            raise ValidationError(VERDICT_REQUIRED_MESSAGE)
        return turn, turn.chat, verdict

    async def _get_prompt(
        self,
        db: AsyncSession,
        auth: AuthContext,
        prompt_id: str,
        *,
        with_labels: bool = False,
    ) -> SavedPrompt:
        stmt = select(SavedPrompt).where(
            SavedPrompt.id == prompt_id,
            SavedPrompt.org_id == auth.org_id,
            SavedPrompt.user_id == auth.user.id,
        )
        if with_labels:
            stmt = stmt.options(selectinload(SavedPrompt.labels))
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError("SavedPrompt", prompt_id)
        return row

    async def _resolve_labels(
        self,
        db: AsyncSession,
        auth: AuthContext,
        label_ids: list[str],
        label_names: list[str],
    ) -> list[ContentLabel]:
        labels: list[ContentLabel] = []
        seen: set[str] = set()
        for label_id in label_ids:
            label = await saved_document_service._get_label(db, auth, label_id)  # noqa: SLF001
            if label.id not in seen:
                labels.append(label)
                seen.add(label.id)
        for raw in label_names:
            created = await saved_document_service.create_label(db, auth, raw)
            label = await saved_document_service._get_label(db, auth, created.id)  # noqa: SLF001
            if label.id not in seen:
                labels.append(label)
                seen.add(label.id)
        return labels

    def _clean_optional_title(self, title: str | None) -> str | None:
        if title is None:
            return None
        cleaned = re.sub(r"\s+", " ", title.strip())
        return cleaned[:255] or None

    def _response(self, row: SavedPrompt) -> SavedPromptResponse:
        return SavedPromptResponse(
            id=row.id,
            title=row.title,
            prompt_text=row.prompt_text,
            verdict_text=row.verdict_text,
            chat_id=row.chat_id,
            turn_id=row.turn_id,
            labels=[
                SavedPromptLabelBrief(id=label.id, name=label.name)
                for label in (row.labels or [])
            ],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


saved_prompt_service = SavedPromptService()
