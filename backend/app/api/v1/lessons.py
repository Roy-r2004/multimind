from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    LessonDetailResponse,
    LessonListItemResponse,
    MessageResponse,
    VerdictDisagreeRequest,
)
from app.services.lesson_service import lesson_service

router = APIRouter()


@router.get("", response_model=list[LessonListItemResponse])
async def list_lessons(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await lesson_service.list_lessons(db, auth)


@router.get("/{lesson_id}", response_model=LessonDetailResponse)
async def get_lesson(
    lesson_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await lesson_service.get_lesson(db, auth, str(lesson_id))


@router.delete("/{lesson_id}", response_model=MessageResponse)
async def delete_lesson(
    lesson_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await lesson_service.delete_lesson(db, auth, str(lesson_id))
    return MessageResponse(message="Lesson deleted")


@router.post(
    "/turns/{turn_id}/disagree",
    response_model=LessonDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def disagree_with_verdict(
    turn_id: UUID,
    data: VerdictDisagreeRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await lesson_service.disagree_with_verdict(db, auth, str(turn_id), data)
