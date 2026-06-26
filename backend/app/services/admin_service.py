"""Admin insights — org-wide user, chat, brain, and lesson queries for the admin console."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
from app.db.models import (
    Chat,
    OrgMembership,
    Project,
    Turn,
    User,
    UserBrain,
    VerdictLesson,
)
from app.services.chat_service import chat_service


class AdminService:
    async def list_users(self, db: AsyncSession, auth: AuthContext) -> list[dict]:
        chat_counts = (
            select(Chat.created_by.label("user_id"), func.count().label("chat_count"))
            .where(Chat.org_id == auth.org_id)
            .group_by(Chat.created_by)
            .subquery()
        )
        turn_counts = (
            select(Chat.created_by.label("user_id"), func.count().label("turn_count"))
            .join(Turn, Turn.chat_id == Chat.id)
            .where(Chat.org_id == auth.org_id)
            .group_by(Chat.created_by)
            .subquery()
        )

        result = await db.execute(
            select(User, OrgMembership, chat_counts.c.chat_count, turn_counts.c.turn_count)
            .join(OrgMembership, OrgMembership.user_id == User.id)
            .outerjoin(chat_counts, chat_counts.c.user_id == User.id)
            .outerjoin(turn_counts, turn_counts.c.user_id == User.id)
            .where(OrgMembership.org_id == auth.org_id)
            .order_by(User.full_name.asc())
        )

        rows = []
        for user, membership, chat_count, turn_count in result.all():
            brain = await db.execute(select(UserBrain).where(UserBrain.user_id == user.id))
            brain_row = brain.scalar_one_or_none()
            rows.append(
                {
                    "user_id": user.id,
                    "email": user.email,
                    "full_name": user.full_name,
                    "role": membership.role.value,
                    "is_active": user.is_active,
                    "joined_at": membership.created_at,
                    "chat_count": int(chat_count or 0),
                    "turn_count": int(turn_count or 0),
                    "has_brain": brain_row is not None,
                    "brain_lesson_count": brain_row.lesson_count if brain_row else 0,
                    "last_active_at": user.updated_at,
                }
            )
        return rows

    async def get_user(self, db: AsyncSession, auth: AuthContext, user_id: str) -> dict:
        result = await db.execute(
            select(User, OrgMembership)
            .join(OrgMembership, OrgMembership.user_id == User.id)
            .where(User.id == user_id, OrgMembership.org_id == auth.org_id)
        )
        row = result.first()
        if row is None:
            raise NotFoundError("User", user_id)
        user, membership = row

        chat_count = await db.scalar(
            select(func.count()).select_from(Chat).where(
                Chat.org_id == auth.org_id, Chat.created_by == user.id
            )
        )
        turn_count = await db.scalar(
            select(func.count())
            .select_from(Turn)
            .join(Chat, Chat.id == Turn.chat_id)
            .where(Chat.org_id == auth.org_id, Chat.created_by == user.id)
        )
        lesson_count = await db.scalar(
            select(func.count())
            .select_from(VerdictLesson)
            .where(VerdictLesson.org_id == auth.org_id, VerdictLesson.user_id == user.id)
        )
        brain = await db.execute(select(UserBrain).where(UserBrain.user_id == user.id))
        brain_row = brain.scalar_one_or_none()

        return {
            "user_id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": membership.role.value,
            "is_active": user.is_active,
            "joined_at": membership.created_at,
            "created_at": user.created_at,
            "chat_count": int(chat_count or 0),
            "turn_count": int(turn_count or 0),
            "lesson_count": int(lesson_count or 0),
            "brain": {
                "summary": brain_row.summary if brain_row else "",
                "thinking_style": brain_row.thinking_style if brain_row else "",
                "likes": brain_row.likes if brain_row else [],
                "dislikes": brain_row.dislikes if brain_row else [],
                "memories": brain_row.memories if brain_row else [],
                "lesson_count": brain_row.lesson_count if brain_row else 0,
                "updated_at": brain_row.updated_at if brain_row else None,
            },
        }

    async def list_user_chats(self, db: AsyncSession, auth: AuthContext, user_id: str) -> list[dict]:
        await self.get_user(db, auth, user_id)
        result = await db.execute(
            select(Chat, func.count(Turn.id).label("turn_count"))
            .outerjoin(Turn, Turn.chat_id == Chat.id)
            .where(Chat.org_id == auth.org_id, Chat.created_by == user_id)
            .group_by(Chat.id)
            .order_by(Chat.updated_at.desc())
        )
        return [
            {
                "id": chat.id,
                "title": chat.title,
                "project_id": chat.project_id,
                "turn_count": int(turn_count or 0),
                "created_at": chat.created_at,
                "updated_at": chat.updated_at,
            }
            for chat, turn_count in result.all()
        ]

    async def list_org_chats(
        self, db: AsyncSession, auth: AuthContext, *, user_id: str | None = None, q: str | None = None
    ) -> list[dict]:
        statement = (
            select(Chat, User, func.count(Turn.id).label("turn_count"))
            .join(User, User.id == Chat.created_by)
            .outerjoin(Turn, Turn.chat_id == Chat.id)
            .where(Chat.org_id == auth.org_id)
            .group_by(Chat.id, User.id)
        )
        if user_id:
            statement = statement.where(Chat.created_by == user_id)
        if q:
            pattern = f"%{q.strip()}%"
            statement = statement.where(
                (Chat.title.ilike(pattern)) | (User.email.ilike(pattern)) | (User.full_name.ilike(pattern))
            )
        statement = statement.order_by(Chat.updated_at.desc()).limit(200)
        result = await db.execute(statement)
        return [
            {
                "id": chat.id,
                "title": chat.title,
                "project_id": chat.project_id,
                "created_by": chat.created_by,
                "creator_name": user.full_name,
                "creator_email": user.email,
                "turn_count": int(turn_count or 0),
                "created_at": chat.created_at,
                "updated_at": chat.updated_at,
            }
            for chat, user, turn_count in result.all()
        ]

    async def get_chat_detail(self, db: AsyncSession, auth: AuthContext, chat_id: str) -> dict:
        result = await db.execute(
            select(Chat, User)
            .join(User, User.id == Chat.created_by)
            .where(Chat.id == chat_id, Chat.org_id == auth.org_id)
        )
        row = result.first()
        if row is None:
            raise NotFoundError("Chat", chat_id)
        chat, user = row

        turns_result = await db.execute(
            select(Turn)
            .where(Turn.chat_id == chat.id)
            .options(
                selectinload(Turn.model_answers),
                selectinload(Turn.verdict),
                selectinload(Turn.decision_insurance),
                selectinload(Turn.lesson),
            )
            .order_by(Turn.created_at.asc())
        )
        turns = [chat_service._turn_response(t) for t in turns_result.scalars().all()]

        return {
            "id": chat.id,
            "title": chat.title,
            "project_id": chat.project_id,
            "created_by": chat.created_by,
            "creator_name": user.full_name,
            "creator_email": user.email,
            "created_at": chat.created_at,
            "updated_at": chat.updated_at,
            "turns": [t.model_dump() for t in turns],
        }

    async def list_brains(self, db: AsyncSession, auth: AuthContext) -> list[dict]:
        result = await db.execute(
            select(UserBrain, User)
            .join(User, User.id == UserBrain.user_id)
            .where(UserBrain.org_id == auth.org_id)
            .order_by(UserBrain.updated_at.desc())
        )
        return [
            {
                "user_id": brain.user_id,
                "user_name": brain.user_name,
                "email": user.email,
                "summary": brain.summary,
                "thinking_style": brain.thinking_style,
                "likes": brain.likes,
                "dislikes": brain.dislikes,
                "memories_count": len(brain.memories or []),
                "lesson_count": brain.lesson_count,
                "updated_at": brain.updated_at,
            }
            for brain, user in result.all()
        ]

    async def get_brain(self, db: AsyncSession, auth: AuthContext, user_id: str) -> dict:
        await self.get_user(db, auth, user_id)
        result = await db.execute(
            select(UserBrain, User)
            .join(User, User.id == UserBrain.user_id)
            .where(UserBrain.user_id == user_id, UserBrain.org_id == auth.org_id)
        )
        row = result.first()
        if row is None:
            return {
                "user_id": user_id,
                "user_name": "",
                "email": "",
                "summary": "",
                "thinking_style": "",
                "likes": [],
                "dislikes": [],
                "memories": [],
                "lesson_count": 0,
                "updated_at": None,
            }
        brain, user = row
        return {
            "user_id": brain.user_id,
            "user_name": brain.user_name,
            "email": user.email,
            "summary": brain.summary,
            "thinking_style": brain.thinking_style,
            "likes": brain.likes,
            "dislikes": brain.dislikes,
            "memories": brain.memories,
            "lesson_count": brain.lesson_count,
            "updated_at": brain.updated_at,
        }

    async def list_lessons(self, db: AsyncSession, auth: AuthContext) -> list[dict]:
        result = await db.execute(
            select(VerdictLesson)
            .where(VerdictLesson.org_id == auth.org_id)
            .order_by(VerdictLesson.created_at.desc())
            .limit(200)
        )
        return [
            {
                "id": lesson.id,
                "title": lesson.title,
                "summary": lesson.summary,
                "user_id": lesson.user_id,
                "user_name": lesson.user_name,
                "status": lesson.status.value,
                "chat_id": lesson.chat_id,
                "turn_id": lesson.turn_id,
                "created_at": lesson.created_at,
            }
            for lesson in result.scalars().all()
        ]

    async def list_projects(self, db: AsyncSession, auth: AuthContext) -> list[dict]:
        chat_counts = (
            select(Chat.project_id.label("project_id"), func.count().label("chat_count"))
            .where(Chat.org_id == auth.org_id, Chat.project_id.isnot(None))
            .group_by(Chat.project_id)
            .subquery()
        )
        result = await db.execute(
            select(Project, chat_counts.c.chat_count)
            .outerjoin(chat_counts, chat_counts.c.project_id == Project.id)
            .where(Project.org_id == auth.org_id)
            .order_by(Project.name.asc())
        )
        return [
            {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "chat_count": int(chat_count or 0),
                "created_at": project.created_at,
            }
            for project, chat_count in result.all()
        ]


admin_service = AdminService()
