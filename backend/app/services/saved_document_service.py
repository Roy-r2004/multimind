"""Saved chat-turn documents with user-managed labels."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import (
    Chat,
    ContentLabel,
    ModelAnswer,
    Project,
    SavedDocument,
    SavedDocumentLabel,
    Turn,
    Verdict,
)
from app.llm.catalog import get_model
from app.llm.providers import get_provider_registry
from app.schemas.api import (
    ContentLabelResponse,
    SavedDocumentLabelBrief,
    SavedDocumentResponse,
    SavedDocumentSuggestResponse,
)
from app.services.brain_knowledge_service import (
    SOURCE_SAVED_DOCUMENT,
    brain_knowledge_service,
)

logger = get_logger(__name__)

DEFAULT_SUGGEST_MODEL = "gpt-4.1"


class SavedDocumentService:
    async def list_labels(self, db: AsyncSession, auth: AuthContext) -> list[ContentLabelResponse]:
        result = await db.execute(
            select(ContentLabel)
            .where(
                ContentLabel.org_id == auth.org_id,
                ContentLabel.user_id == auth.user.id,
            )
            .order_by(ContentLabel.name.asc())
        )
        labels = list(result.scalars().all())
        counts: dict[str, int] = {}
        if labels:
            count_rows = await db.execute(
                select(SavedDocumentLabel.label_id, func.count())
                .where(SavedDocumentLabel.label_id.in_([label.id for label in labels]))
                .group_by(SavedDocumentLabel.label_id)
            )
            counts = {lid: int(n) for lid, n in count_rows.all()}
        return [
            ContentLabelResponse(
                id=label.id,
                name=label.name,
                document_count=counts.get(label.id, 0),
                created_at=label.created_at,
                updated_at=label.updated_at,
            )
            for label in labels
        ]

    async def create_label(
        self, db: AsyncSession, auth: AuthContext, name: str
    ) -> ContentLabelResponse:
        cleaned = self._clean_label_name(name)
        existing = await db.execute(
            select(ContentLabel).where(
                ContentLabel.org_id == auth.org_id,
                ContentLabel.user_id == auth.user.id,
                ContentLabel.name == cleaned,
            )
        )
        label = existing.scalar_one_or_none()
        if label is None:
            label = ContentLabel(org_id=auth.org_id, user_id=auth.user.id, name=cleaned)
            db.add(label)
            await db.flush()
        return ContentLabelResponse(
            id=label.id,
            name=label.name,
            document_count=0,
            created_at=label.created_at,
            updated_at=label.updated_at,
        )

    async def rename_label(
        self, db: AsyncSession, auth: AuthContext, label_id: str, name: str
    ) -> ContentLabelResponse:
        label = await self._get_label(db, auth, label_id)
        label.name = self._clean_label_name(name)
        await db.flush()
        return ContentLabelResponse(
            id=label.id,
            name=label.name,
            document_count=0,
            created_at=label.created_at,
            updated_at=label.updated_at,
        )

    async def delete_label(self, db: AsyncSession, auth: AuthContext, label_id: str) -> None:
        label = await self._get_label(db, auth, label_id)
        await db.delete(label)
        await db.flush()

    async def suggest(
        self, db: AsyncSession, auth: AuthContext, turn_id: str
    ) -> SavedDocumentSuggestResponse:
        turn, chat, verdict, answers = await self._load_turn_bundle(db, auth, turn_id)
        fallback_name = self._fallback_name(turn.user_message)
        existing_labels = await self.list_labels(db, auth)
        label_names = [label.name for label in existing_labels]
        try:
            model = get_model(DEFAULT_SUGGEST_MODEL)
            provider = get_provider_registry().get_provider(model.provider)
            council = "; ".join(
                f"{a.model_id}: {(a.text or '')[:120]}" for a in answers if a.text
            )[:800]
            system = (
                "You name saved research documents. Return JSON only: "
                '{"name":"short title","label_suggestions":["Label",...]} '
                "Use 2-6 word title. Suggest 1-3 labels (existing when relevant)."
            )
            user = (
                f"Existing labels: {label_names}\n"
                f"Question: {turn.user_message[:500]}\n"
                f"Verdict: {(verdict.text if verdict else '')[:500]}\n"
                f"Council: {council}"
            )
            response = await provider.complete(system=system, user=user, model=model.provider_model)
            parsed = provider.parse_json_response(response.text)
            name = str(parsed.get("name") or fallback_name).strip()[:255] or fallback_name
            suggestions = [
                str(x).strip()[:120]
                for x in (parsed.get("label_suggestions") or [])
                if str(x).strip()
            ][:5]
            return SavedDocumentSuggestResponse(name=name, label_suggestions=suggestions)
        except Exception as exc:  # noqa: BLE001
            logger.warning("saved_document_suggest_failed", error=str(exc))
            return SavedDocumentSuggestResponse(
                name=fallback_name,
                label_suggestions=label_names[:3],
            )

    async def create_from_turn(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        turn_id: str,
        name: str | None,
        label_ids: list[str],
        label_names: list[str],
    ) -> SavedDocumentResponse:
        turn, chat, verdict, answers = await self._load_turn_bundle(db, auth, turn_id)
        doc_name = (name or "").strip()
        if not doc_name:
            suggestion = await self.suggest(db, auth, turn_id)
            doc_name = suggestion.name
        project_name = None
        if chat.project_id:
            project = (
                await db.execute(select(Project).where(Project.id == chat.project_id))
            ).scalar_one_or_none()
            project_name = project.name if project else None

        snapshot = {
            "user_message": turn.user_message,
            "council_answers": [
                {
                    "model_id": a.model_id,
                    "text": a.text,
                    "status": a.status.value if hasattr(a.status, "value") else str(a.status),
                }
                for a in answers
            ],
            "verdict": (
                {
                    "id": verdict.id,
                    "text": verdict.text,
                    "reason": verdict.reason,
                    "model_id": verdict.model_id,
                    "strategy": verdict.strategy.value
                    if hasattr(verdict.strategy, "value")
                    else str(verdict.strategy),
                }
                if verdict
                else None
            ),
            "metadata": {
                "chat_id": chat.id,
                "chat_title": chat.title,
                "turn_id": turn.id,
                "project_id": chat.project_id,
            },
        }
        doc = SavedDocument(
            org_id=auth.org_id,
            user_id=auth.user.id,
            name=doc_name[:255],
            chat_id=chat.id,
            turn_id=turn.id,
            project_id=chat.project_id,
            chat_title=chat.title,
            project_name=project_name,
            snapshot_json=snapshot,
        )
        db.add(doc)
        await db.flush()

        labels = await self._resolve_labels(db, auth, label_ids, label_names)
        for label in labels:
            db.add(SavedDocumentLabel(document_id=doc.id, label_id=label.id))
        await db.flush()

        try:
            await brain_knowledge_service.upsert_item(
                db,
                org_id=auth.org_id,
                user_id=auth.user.id,
                project_id=chat.project_id,
                source_type=SOURCE_SAVED_DOCUMENT,
                source_id=doc.id,
                title=doc.name,
                content=self._snapshot_text(snapshot),
                metadata={"chat_title": chat.title, "turn_id": turn.id},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("saved_document_brain_ingest_failed", error=str(exc))

        return await self.get_document(db, auth, doc.id)

    async def update_document(
        self,
        db: AsyncSession,
        auth: AuthContext,
        document_id: str,
        *,
        name: str | None,
        label_ids: list[str] | None,
    ) -> SavedDocumentResponse:
        doc = await self._get_document(db, auth, document_id)
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise ValidationError("Document name cannot be empty")
            doc.name = cleaned[:255]
        if label_ids is not None:
            await db.execute(
                delete(SavedDocumentLabel).where(SavedDocumentLabel.document_id == doc.id)
            )
            labels = await self._resolve_labels(db, auth, label_ids, [])
            for label in labels:
                db.add(SavedDocumentLabel(document_id=doc.id, label_id=label.id))
        await db.flush()
        try:
            await brain_knowledge_service.upsert_item(
                db,
                org_id=auth.org_id,
                user_id=auth.user.id,
                project_id=doc.project_id,
                source_type=SOURCE_SAVED_DOCUMENT,
                source_id=doc.id,
                title=doc.name,
                content=self._snapshot_text(doc.snapshot_json or {}),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("saved_document_brain_update_failed", error=str(exc))
        return await self.get_document(db, auth, doc.id)

    async def delete_document(self, db: AsyncSession, auth: AuthContext, document_id: str) -> None:
        doc = await self._get_document(db, auth, document_id)
        await brain_knowledge_service.delete_source(
            db,
            org_id=auth.org_id,
            user_id=auth.user.id,
            source_type=SOURCE_SAVED_DOCUMENT,
            source_id=doc.id,
        )
        await db.delete(doc)
        await db.flush()

    async def get_document(
        self, db: AsyncSession, auth: AuthContext, document_id: str
    ) -> SavedDocumentResponse:
        doc = await self._get_document(db, auth, document_id, with_labels=True)
        return self._document_response(doc)

    async def search(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        q: str | None = None,
        label_id: str | None = None,
    ) -> list[SavedDocumentResponse]:
        stmt = (
            select(SavedDocument)
            .where(
                SavedDocument.org_id == auth.org_id,
                SavedDocument.user_id == auth.user.id,
            )
            .options(selectinload(SavedDocument.labels))
            .order_by(SavedDocument.updated_at.desc())
        )
        if label_id:
            stmt = stmt.join(SavedDocumentLabel).where(SavedDocumentLabel.label_id == label_id)
        if q and q.strip():
            term = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    SavedDocument.name.ilike(term),
                    SavedDocument.chat_title.ilike(term),
                    SavedDocument.project_name.ilike(term),
                )
            )
        result = await db.execute(stmt)
        docs = list(result.scalars().unique().all())
        if q and q.strip():
            needle = q.strip().lower()
            docs = [
                doc
                for doc in docs
                if needle in doc.name.lower()
                or needle in (doc.chat_title or "").lower()
                or needle in (doc.project_name or "").lower()
                or needle in self._snapshot_text(doc.snapshot_json or {}).lower()
                or any(needle in label.name.lower() for label in doc.labels)
            ]
        return [self._document_response(doc) for doc in docs]

    async def _load_turn_bundle(
        self, db: AsyncSession, auth: AuthContext, turn_id: str
    ) -> tuple[Turn, Chat, Verdict | None, list[ModelAnswer]]:
        result = await db.execute(
            select(Turn)
            .where(Turn.id == turn_id)
            .options(
                selectinload(Turn.chat),
                selectinload(Turn.verdict),
                selectinload(Turn.model_answers),
            )
        )
        turn = result.scalar_one_or_none()
        if turn is None or turn.chat is None or turn.chat.org_id != auth.org_id:
            raise NotFoundError("Turn", turn_id)
        return turn, turn.chat, turn.verdict, list(turn.model_answers or [])

    async def _get_label(self, db: AsyncSession, auth: AuthContext, label_id: str) -> ContentLabel:
        result = await db.execute(
            select(ContentLabel).where(
                ContentLabel.id == label_id,
                ContentLabel.org_id == auth.org_id,
                ContentLabel.user_id == auth.user.id,
            )
        )
        label = result.scalar_one_or_none()
        if label is None:
            raise NotFoundError("ContentLabel", label_id)
        return label

    async def _get_document(
        self,
        db: AsyncSession,
        auth: AuthContext,
        document_id: str,
        *,
        with_labels: bool = False,
    ) -> SavedDocument:
        stmt = select(SavedDocument).where(
            SavedDocument.id == document_id,
            SavedDocument.org_id == auth.org_id,
            SavedDocument.user_id == auth.user.id,
        )
        if with_labels:
            stmt = stmt.options(selectinload(SavedDocument.labels))
        result = await db.execute(stmt)
        doc = result.scalar_one_or_none()
        if doc is None:
            raise NotFoundError("SavedDocument", document_id)
        return doc

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
            label = await self._get_label(db, auth, label_id)
            if label.id not in seen:
                labels.append(label)
                seen.add(label.id)
        for raw in label_names:
            created = await self.create_label(db, auth, raw)
            label = await self._get_label(db, auth, created.id)
            if label.id not in seen:
                labels.append(label)
                seen.add(label.id)
        return labels

    def _document_response(self, doc: SavedDocument) -> SavedDocumentResponse:
        return SavedDocumentResponse(
            id=doc.id,
            name=doc.name,
            chat_id=doc.chat_id,
            turn_id=doc.turn_id,
            project_id=doc.project_id,
            chat_title=doc.chat_title or "",
            project_name=doc.project_name,
            labels=[
                SavedDocumentLabelBrief(id=label.id, name=label.name)
                for label in (doc.labels or [])
            ],
            snapshot_json=doc.snapshot_json or {},
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )

    def _clean_label_name(self, name: str) -> str:
        cleaned = re.sub(r"\s+", " ", (name or "").strip())
        if not cleaned:
            raise ValidationError("Label name is required")
        return cleaned[:120]

    def _fallback_name(self, user_message: str) -> str:
        words = re.findall(r"[A-Za-z0-9]+", user_message or "")
        if not words:
            return "Saved turn"
        return " ".join(words[:6]).title()[:255]

    def _snapshot_text(self, snapshot: dict[str, Any]) -> str:
        parts = [str(snapshot.get("user_message") or "")]
        verdict = snapshot.get("verdict") or {}
        if isinstance(verdict, dict) and verdict.get("text"):
            parts.append(str(verdict["text"]))
        for answer in snapshot.get("council_answers") or []:
            if isinstance(answer, dict) and answer.get("text"):
                parts.append(str(answer["text"])[:400])
        return "\n\n".join(p for p in parts if p)


saved_document_service = SavedDocumentService()
