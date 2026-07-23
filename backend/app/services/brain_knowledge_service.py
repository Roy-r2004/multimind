"""Hybrid Brain knowledge index — ingest + permissioned retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.logging import get_logger
from app.db.models import BrainKnowledgeItem
from app.schemas.api import BrainKnowledgeItemResponse
from app.services.embedding_utils import cosine_similarity, embed_text

logger = get_logger(__name__)

SOURCE_CHAT_TURN = "chat_turn"
SOURCE_VERDICT = "verdict"
SOURCE_MODEL_RESPONSE = "model_response"
SOURCE_SAVED_DOCUMENT = "saved_document"
SOURCE_PINNED_VERDICT = "pinned_verdict"
SOURCE_LESSON = "lesson"
SOURCE_FEEDBACK = "feedback"
SOURCE_SCRAPING_MISSION = "scraping_mission"
SOURCE_SCRAPING_FACILITY = "scraping_facility"


class BrainKnowledgeService:
    async def upsert_item(
        self,
        db: AsyncSession,
        *,
        org_id: str,
        user_id: str,
        source_type: str,
        source_id: str,
        title: str,
        content: str,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BrainKnowledgeItem | None:
        text = (content or "").strip()
        if not text:
            return None
        try:
            embedding = await embed_text(f"{title}\n{text}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("brain_embed_failed", error=str(exc))
            embedding = None

        result = await db.execute(
            select(BrainKnowledgeItem).where(
                BrainKnowledgeItem.org_id == org_id,
                BrainKnowledgeItem.user_id == user_id,
                BrainKnowledgeItem.source_type == source_type,
                BrainKnowledgeItem.source_id == source_id,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            item = BrainKnowledgeItem(
                org_id=org_id,
                user_id=user_id,
                project_id=project_id,
                source_type=source_type,
                source_id=source_id,
                title=(title or "")[:512],
                content=text[:12000],
                metadata_json=metadata or {},
                embedding=embedding,
            )
            db.add(item)
        else:
            item.project_id = project_id
            item.title = (title or "")[:512]
            item.content = text[:12000]
            item.metadata_json = metadata or {}
            item.embedding = embedding
            item.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return item

    async def delete_source(
        self,
        db: AsyncSession,
        *,
        org_id: str,
        user_id: str,
        source_type: str,
        source_id: str,
    ) -> None:
        await db.execute(
            delete(BrainKnowledgeItem).where(
                BrainKnowledgeItem.org_id == org_id,
                BrainKnowledgeItem.user_id == user_id,
                BrainKnowledgeItem.source_type == source_type,
                BrainKnowledgeItem.source_id == source_id,
            )
        )
        await db.flush()

    async def ingest_turn(
        self,
        db: AsyncSession,
        *,
        org_id: str,
        user_id: str,
        project_id: str | None,
        turn_id: str,
        chat_title: str,
        user_message: str,
        verdict_text: str | None,
        council_digest: str | None = None,
    ) -> None:
        try:
            parts = [f"Question: {user_message}"]
            if verdict_text:
                parts.append(f"Verdict: {verdict_text}")
            if council_digest:
                parts.append(f"Council: {council_digest}")
            await self.upsert_item(
                db,
                org_id=org_id,
                user_id=user_id,
                project_id=project_id,
                source_type=SOURCE_CHAT_TURN,
                source_id=turn_id,
                title=f"Chat turn — {chat_title}"[:512],
                content="\n\n".join(parts),
                metadata={"chat_title": chat_title},
            )
            if verdict_text:
                await self.upsert_item(
                    db,
                    org_id=org_id,
                    user_id=user_id,
                    project_id=project_id,
                    source_type=SOURCE_VERDICT,
                    source_id=turn_id,
                    title=f"Verdict — {chat_title}"[:512],
                    content=verdict_text,
                    metadata={"chat_title": chat_title},
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("brain_ingest_turn_failed", turn_id=turn_id, error=str(exc))

    async def retrieve(
        self,
        db: AsyncSession,
        *,
        org_id: str,
        user_id: str,
        query: str,
        project_id: str | None = None,
        limit: int = 6,
    ) -> list[BrainKnowledgeItem]:
        if not (query or "").strip():
            return []
        try:
            query_vec = await embed_text(query)
        except Exception:
            query_vec = None

        stmt = select(BrainKnowledgeItem).where(
            BrainKnowledgeItem.org_id == org_id,
            BrainKnowledgeItem.user_id == user_id,
        )
        if project_id:
            stmt = stmt.where(
                or_(
                    BrainKnowledgeItem.project_id == project_id,
                    BrainKnowledgeItem.project_id.is_(None),
                )
            )
        result = await db.execute(stmt.order_by(BrainKnowledgeItem.updated_at.desc()).limit(200))
        items = list(result.scalars().all())
        if not items:
            return []

        scored: list[tuple[float, BrainKnowledgeItem]] = []
        for item in items:
            score = 0.0
            if query_vec and item.embedding:
                score = cosine_similarity(query_vec, item.embedding)
            else:
                q = query.lower()
                blob = f"{item.title}\n{item.content}".lower()
                score = 1.0 if q in blob else sum(1 for tok in q.split() if tok in blob) / max(
                    1, len(q.split())
                )
            # light recency boost
            score += 0.02
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for score, item in scored[:limit] if score > 0.05]

    async def format_retrieval_block(
        self,
        db: AsyncSession,
        *,
        org_id: str,
        user_id: str,
        query: str,
        project_id: str | None = None,
        limit: int = 6,
    ) -> str:
        items = await self.retrieve(
            db,
            org_id=org_id,
            user_id=user_id,
            query=query,
            project_id=project_id,
            limit=limit,
        )
        if not items:
            return ""
        lines = [
            "**Relevant Brain knowledge (use only when it helps the current request):**",
            "",
        ]
        for i, item in enumerate(items, start=1):
            snippet = (item.content or "")[:600]
            lines.append(f"### [{item.source_type}] {item.title or item.source_id}")
            lines.append(snippet)
            lines.append("")
            if i >= limit:
                break
        return "\n".join(lines).strip()

    async def list_recent_for_user(
        self, db: AsyncSession, auth: AuthContext, *, limit: int = 20
    ) -> list[BrainKnowledgeItemResponse]:
        result = await db.execute(
            select(BrainKnowledgeItem)
            .where(
                BrainKnowledgeItem.org_id == auth.org_id,
                BrainKnowledgeItem.user_id == auth.user.id,
            )
            .order_by(BrainKnowledgeItem.updated_at.desc())
            .limit(limit)
        )
        return [
            BrainKnowledgeItemResponse(
                id=item.id,
                source_type=item.source_type,
                source_id=item.source_id,
                title=item.title,
                content=item.content[:400],
                project_id=item.project_id,
                created_at=item.created_at,
            )
            for item in result.scalars().all()
        ]

    async def count_for_user(self, db: AsyncSession, auth: AuthContext) -> int:
        from sqlalchemy import func

        result = await db.execute(
            select(func.count())
            .select_from(BrainKnowledgeItem)
            .where(
                BrainKnowledgeItem.org_id == auth.org_id,
                BrainKnowledgeItem.user_id == auth.user.id,
            )
        )
        return int(result.scalar() or 0)


brain_knowledge_service = BrainKnowledgeService()
