import json
import mimetypes
import re
import uuid
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext, get_auth_context, get_streaming_auth_context
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, get_db
from app.schemas.api import (
    AttachmentResponse,
    ChatCreateRequest,
    ChatResponse,
    ChatUpdateRequest,
    MessageResponse,
    PinVerdictRequest,
    ShareLinkResponse,
    TurnDeleteResponse,
    TurnCreateRequest,
    TurnResponse,
)
from app.services.chat_service import chat_service, turn_stream_internal_error_event
from app.services.share_service import share_service

_TEXT_CONTENT_TYPES = frozenset({
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/html",
    "text/xml",
    "application/json",
    "application/xml",
})
_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".yaml", ".yml"})
_ATTACHMENT_TEXT_EXCERPT_MAX = 20_000

router = APIRouter()
logger = get_logger(__name__)


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


@router.put("/{chat_id}/pinned-verdict", response_model=ChatResponse)
async def pin_verdict(
    chat_id: UUID,
    data: PinVerdictRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.pin_verdict(db, auth, str(chat_id), data.verdict_id)


@router.delete("/{chat_id}/pinned-verdict", response_model=ChatResponse)
async def unpin_verdict(
    chat_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.unpin_verdict(db, auth, str(chat_id))


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


@router.delete("/{chat_id}/turns/{turn_id}", response_model=TurnDeleteResponse)
async def delete_turn(
    chat_id: UUID,
    turn_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.delete_turn(db, auth, str(chat_id), str(turn_id))


@router.post("/{chat_id}/turns/{turn_id}/restore", response_model=TurnResponse)
async def restore_turn(
    chat_id: UUID,
    turn_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.restore_turn(db, auth, str(chat_id), str(turn_id))


@router.get("/turns/{turn_id}", response_model=TurnResponse)
async def get_turn(
    turn_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.get_turn(db, auth, str(turn_id))


@router.post(
    "/{chat_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    chat_id: UUID,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    await chat_service.get_chat(db, auth, str(chat_id))

    content = await file.read()
    if len(content) > settings.chat_attachment_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {settings.chat_attachment_max_bytes // (1024 * 1024)} MB.",
        )

    original_filename = file.filename or "upload"
    safe_filename = re.sub(r"[^\w.\-]", "_", original_filename)[:200]
    attachment_id = str(uuid.uuid4())
    content_type = file.content_type or (mimetypes.guess_type(original_filename)[0] or "application/octet-stream")

    dest_dir = Path.cwd() / "data" / "chat_attachments" / auth.org_id / str(chat_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{attachment_id}_{safe_filename}"
    dest_path.write_bytes(content)

    text_excerpt: str | None = None
    ext = Path(original_filename).suffix.lower()
    base_ct = content_type.split(";")[0].strip().lower()
    is_text = base_ct in _TEXT_CONTENT_TYPES or ext in _TEXT_EXTENSIONS
    if is_text:
        try:
            raw_text = content.decode("utf-8", errors="replace")
            text_excerpt = raw_text[:_ATTACHMENT_TEXT_EXCERPT_MAX]
        except Exception:
            text_excerpt = None

    return AttachmentResponse(
        id=attachment_id,
        filename=original_filename,
        content_type=content_type,
        size_bytes=len(content),
        text_excerpt=text_excerpt,
    )


@router.get("/turns/{turn_id}/stream")
async def stream_turn(
    turn_id: UUID,
    auth: AuthContext = Depends(get_streaming_auth_context),
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
            except Exception:
                await db.rollback()
                logger.exception("turn_stream_response_failed", turn_id=str(turn_id))
                payload = turn_stream_internal_error_event()
                yield f"event: error\ndata: {json.dumps(payload['data'])}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
