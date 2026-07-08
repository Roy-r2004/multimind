from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)
from app.services.domain_service import project_service

router = APIRouter()


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await project_service.list(db, auth)


@router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(
    project_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await project_service.get_detail(db, auth, project_id)


@router.patch("/{project_id}", response_model=ProjectDetailResponse)
async def update_project(
    project_id: UUID,
    data: ProjectUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await project_service.update(db, auth, project_id, data)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    data: ProjectCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await project_service.create(db, auth, data)
