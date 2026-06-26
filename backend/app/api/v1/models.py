from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import MessageResponse, ModelAddRequest, ModelResponse, ModelSearchResult
from app.services.model_service import model_catalog_service

router = APIRouter()


@router.get("", response_model=list[ModelResponse])
async def get_models(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await model_catalog_service.list_for_org(db, auth)


@router.get("/search", response_model=list[ModelSearchResult])
async def search_models(
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=30, ge=1, le=100),
    _auth: AuthContext = Depends(get_auth_context),
):
    return await model_catalog_service.search(q, limit=limit)


@router.post("", response_model=ModelResponse, status_code=201)
async def add_model(
    body: ModelAddRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await model_catalog_service.add(db, auth, body)


@router.delete("/{model_id}", response_model=MessageResponse)
async def remove_model(
    model_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await model_catalog_service.remove(db, auth, model_id)
    return MessageResponse(message="Model removed")
