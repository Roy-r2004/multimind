"""Share link API routes."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.api import SharedChatResponse
from app.services.share_service import share_service

router = APIRouter()


@router.get("/{token}", response_model=SharedChatResponse)
async def get_shared_chat(token: str, db: AsyncSession = Depends(get_db)):
    return await share_service.get_shared_chat(db, token)
