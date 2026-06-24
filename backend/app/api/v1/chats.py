import json
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import AsyncSessionLocal, get_db
from app.schemas.api import (
    ChatCreateRequest,
    ChatResponse,
    ChatUpdateRequest,
    MessageResponse,
    ShareLinkResponse,
    TurnCreateRequest,
    TurnResponse,
)
from app.services.chat_service import chat_service
from app.services.share_service import share_service

router = APIRouter()


@router.get("", response_model=list[ChatResponse])
async def list_chats(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.list_chats(db, auth)


@router.post("", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(
    data: ChatCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.create_chat(db, auth, data)


@router.patch("/{chat_id}", response_model=ChatResponse)
async def update_chat(
    chat_id: UUID,
    data: ChatUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.update_chat(db, auth, str(chat_id), data)


@router.delete("/{chat_id}", response_model=MessageResponse)
async def delete_chat(
    chat_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await chat_service.delete_chat(db, auth, str(chat_id))
    return MessageResponse(message="Chat deleted")


@router.post("/{chat_id}/share", response_model=ShareLinkResponse)
async def create_share_link(
    chat_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await share_service.create_link(db, auth, str(chat_id))


@router.get("/{chat_id}/turns", response_model=list[TurnResponse])
async def list_turns(
    chat_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.list_turns(db, auth, str(chat_id))


@router.post("/{chat_id}/turns", response_model=TurnResponse, status_code=status.HTTP_201_CREATED)
async def start_turn(
    chat_id: UUID,
    data: TurnCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.start_turn(db, auth, str(chat_id), data)


@router.get("/turns/{turn_id}", response_model=TurnResponse)
async def get_turn(
    turn_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.get_turn(db, auth, str(turn_id))


@router.get("/turns/{turn_id}/stream")
async def stream_turn(
    turn_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
):
    async def event_generator():
        async with AsyncSessionLocal() as db:
            try:
                async for payload in chat_service.execute_turn_stream(
                    db, auth, str(turn_id)
                ):
                    event_type = payload["type"]
                    data = json.dumps(payload["data"], default=str)
                    yield f"event: {event_type}\ndata: {data}\n\n"
                await db.commit()
            except Exception as exc:
                await db.rollback()
                yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
