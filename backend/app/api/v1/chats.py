import json
import mimetypes
import uuid
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext, get_auth_context, get_streaming_auth_context
from app.core.exceptions import (
    ConflictError,
    InvalidAttachmentError,
    NotFoundError,
    SilentAudioError,
    UnsupportedAttachmentTypeError,
    ValidationError,
)
from app.core.logging import get_logger
from app.db.models import ChatAttachment
from app.db.session import AsyncSessionLocal, get_db
from app.schemas.api import (
    AttachmentListResponse,
    AttachmentResponse,
    ChatCreateRequest,
    ChatResponse,
    ChatUpdateRequest,
    MessageResponse,
    PinVerdictRequest,
    ShareLinkResponse,
    TurnCreateRequest,
    TurnDeleteResponse,
    TurnRegenerateRequest,
    TurnRegenerateResponse,
    TurnResponse,
)
from app.services.chat_attachment_storage import (
    UnsafeAttachmentPathError,
    cleanup_path,
    promote_temp_attachment_file,
    resolve_attachment_path,
    safe_delete_attachment_file,
    stream_upload_to_temp_file,
)
from app.services.chat_attachment_text import (
    excerpt_from_transcript,
    extract_attachment_text_from_path,
)
from app.services.chat_service import chat_service, turn_stream_internal_error_event
from app.services.share_service import share_service
from app.services.transcription_service import transcription_service

_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".json",
        ".xml",
        ".yaml",
        ".yml",
        ".html",
        ".htm",
    }
)
_OFFICE_EXTENSIONS = frozenset({".docx", ".xlsx"})
_PDF_EXTENSIONS = frozenset({".pdf"})
_AUDIO_EXTENSIONS = frozenset({".webm"})
_ALLOWED_EXTENSIONS = _TEXT_EXTENSIONS | _OFFICE_EXTENSIONS | _PDF_EXTENSIONS | _AUDIO_EXTENSIONS
_LEGACY_OFFICE_EXTENSIONS = frozenset({".doc", ".xls", ".docm", ".xlsm"})

_TEXT_CONTENT_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "text/html",
        "text/xml",
        "text/yaml",
        "text/x-yaml",
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
    }
)
_DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PDF_CONTENT_TYPE = "application/pdf"
_GENERIC_BINARY_TYPE = "application/octet-stream"
_WEBM_CONTENT_TYPE = "audio/webm"
# Browsers/OS (and Python mimetypes.guess_type) often label .webm as video/webm
# even for audio-only MediaRecorder output.
_WEBM_CONTENT_TYPES = frozenset(
    {
        _WEBM_CONTENT_TYPE,
        "video/webm",
        _GENERIC_BINARY_TYPE,
    }
)

_UNSUPPORTED_TYPE_MESSAGE = (
    "Unsupported file type. Upload a text file, .docx, .xlsx, .pdf, or .webm."
)
_MAX_PENDING_ATTACHMENTS = 10

router = APIRouter()
logger = get_logger(__name__)


def _normalize_media_type(content_type: str | None) -> str:
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _attachment_response(row: ChatAttachment) -> AttachmentResponse:
    return AttachmentResponse(
        id=row.id,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        text_excerpt=row.text_excerpt,
        excerpt_status=row.excerpt_status,
    )


def _validate_attachment_filename(filename: str | None) -> tuple[str, str]:
    if filename is None:
        raise InvalidAttachmentError("A valid filename is required")
    if "\x00" in filename:
        raise InvalidAttachmentError("A valid filename is required")
    original = filename.strip()
    if not original or original in {".", ".."}:
        raise InvalidAttachmentError("A valid filename is required")
    basename = Path(original).name.strip()
    if not basename or basename in {".", ".."}:
        raise InvalidAttachmentError("A valid filename is required")
    if "\x00" in basename or "/" in basename or "\\" in basename:
        raise InvalidAttachmentError("A valid filename is required")
    ext = Path(basename).suffix.lower()
    if not ext:
        raise UnsupportedAttachmentTypeError(_UNSUPPORTED_TYPE_MESSAGE)
    if ext in _LEGACY_OFFICE_EXTENSIONS:
        raise UnsupportedAttachmentTypeError(
            "Legacy Word/Excel formats (.doc, .xls) are not supported. "
            "Upload .docx or .xlsx instead."
        )
    if ext not in _ALLOWED_EXTENSIONS:
        raise UnsupportedAttachmentTypeError(_UNSUPPORTED_TYPE_MESSAGE)
    return basename, ext


def _validate_attachment_content_type(content_type: str | None, filename: str, ext: str) -> str:
    # Strip optional MIME parameters (e.g. audio/webm;codecs=opus → audio/webm).
    normalized = _normalize_media_type(content_type)
    if not normalized:
        # Browsers sometimes omit MIME; fall back to extension-based guess or generic.
        # For .webm, prefer audio/webm: mimetypes.guess_type(".webm") returns video/webm.
        if ext == ".webm":
            normalized = _WEBM_CONTENT_TYPE
        else:
            guessed = mimetypes.guess_type(filename)[0]
            normalized = (guessed or _GENERIC_BINARY_TYPE).lower()

    if ext in _TEXT_EXTENSIONS:
        if normalized in _TEXT_CONTENT_TYPES or normalized == _GENERIC_BINARY_TYPE:
            return normalized
        raise UnsupportedAttachmentTypeError(_UNSUPPORTED_TYPE_MESSAGE)

    if ext == ".docx":
        if normalized in {_DOCX_CONTENT_TYPE, _GENERIC_BINARY_TYPE}:
            return normalized
        raise UnsupportedAttachmentTypeError(_UNSUPPORTED_TYPE_MESSAGE)

    if ext == ".xlsx":
        if normalized in {_XLSX_CONTENT_TYPE, _GENERIC_BINARY_TYPE}:
            return normalized
        raise UnsupportedAttachmentTypeError(_UNSUPPORTED_TYPE_MESSAGE)

    if ext == ".pdf":
        if normalized in {_PDF_CONTENT_TYPE, _GENERIC_BINARY_TYPE}:
            return normalized
        raise UnsupportedAttachmentTypeError(_UNSUPPORTED_TYPE_MESSAGE)

    if ext == ".webm":
        if normalized in _WEBM_CONTENT_TYPES:
            return normalized
        raise UnsupportedAttachmentTypeError(_UNSUPPORTED_TYPE_MESSAGE)

    raise UnsupportedAttachmentTypeError(_UNSUPPORTED_TYPE_MESSAGE)


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
    only_if_unused: bool = Query(
        False,
        description="When true, delete only if the chat has no turns and no attachments.",
    ),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await chat_service.delete_chat(
        db, auth, str(chat_id), only_if_unused=only_if_unused
    )
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


@router.post(
    "/{chat_id}/turns/{turn_id}/regenerate",
    response_model=TurnRegenerateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def regenerate_turn(
    chat_id: UUID,
    turn_id: UUID,
    data: TurnRegenerateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.regenerate_turn(db, auth, str(chat_id), str(turn_id), data)


@router.get("/turns/{turn_id}", response_model=TurnResponse)
async def get_turn(
    turn_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await chat_service.get_turn(db, auth, str(turn_id))


@router.get("/{chat_id}/attachments", response_model=AttachmentListResponse)
async def list_pending_attachments(
    chat_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await chat_service.get_chat(db, auth, str(chat_id))
    result = await db.execute(
        select(ChatAttachment)
        .where(
            ChatAttachment.org_id == auth.org_id,
            ChatAttachment.chat_id == str(chat_id),
            ChatAttachment.turn_id.is_(None),
        )
        .order_by(ChatAttachment.created_at.asc(), ChatAttachment.id.asc())
    )
    rows = result.scalars().all()
    return AttachmentListResponse(items=[_attachment_response(row) for row in rows])


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

    pending_count = (
        await db.execute(
            select(ChatAttachment.id).where(
                ChatAttachment.org_id == auth.org_id,
                ChatAttachment.chat_id == str(chat_id),
                ChatAttachment.turn_id.is_(None),
            )
        )
    ).scalars().all()
    if len(pending_count) >= _MAX_PENDING_ATTACHMENTS:
        raise ValidationError(
            f"A chat can have at most {_MAX_PENDING_ATTACHMENTS} pending attachments."
        )

    # Extension/MIME checks happen before streaming so unsupported types never hit disk.
    original_filename, ext = _validate_attachment_filename(file.filename)
    content_type = _validate_attachment_content_type(
        file.content_type, original_filename, ext
    )

    tmp_path = None
    dest_path = None
    try:
        tmp_path, size_bytes = await stream_upload_to_temp_file(
            file,
            max_bytes=settings.chat_attachment_max_bytes,
            extension=ext,
        )

        try:
            if ext in _AUDIO_EXTENSIONS:
                try:
                    result = await transcription_service.transcribe(tmp_path, language="en")
                    text_excerpt, excerpt_status = excerpt_from_transcript(result.text)
                except SilentAudioError:
                    # Match empty document extraction: keep the file, mark excerpt empty.
                    text_excerpt, excerpt_status = None, "empty"
            else:
                text_excerpt, excerpt_status = extract_attachment_text_from_path(tmp_path, ext)
        except InvalidAttachmentError:
            cleanup_path(tmp_path)
            tmp_path = None
            raise

        attachment_id = str(uuid.uuid4())
        stored_name = f"{attachment_id}{ext}"
        relative_path = f"{auth.org_id}/{chat_id}/{stored_name}"
        try:
            dest_path = resolve_attachment_path(relative_path)
        except UnsafeAttachmentPathError as exc:
            logger.error("chat_attachment_unsafe_final_path")
            cleanup_path(tmp_path)
            tmp_path = None
            raise InvalidAttachmentError("Invalid attachment storage path") from exc

        promote_temp_attachment_file(tmp_path, dest_path)
        tmp_path = None  # ownership transferred

        row = ChatAttachment(
            id=attachment_id,
            org_id=auth.org_id,
            chat_id=str(chat_id),
            uploaded_by_user_id=auth.user.id,
            turn_id=None,
            filename=original_filename[:255],
            stored_name=stored_name,
            content_type=content_type,
            size_bytes=size_bytes,
            relative_path=relative_path,
            text_excerpt=text_excerpt,
            excerpt_status=excerpt_status,
        )
        try:
            db.add(row)
            await db.flush()
        except Exception:
            cleanup_path(dest_path)
            raise

        return _attachment_response(row)
    finally:
        cleanup_path(tmp_path)


@router.delete("/{chat_id}/attachments/{attachment_id}", response_model=MessageResponse)
async def delete_attachment(
    chat_id: UUID,
    attachment_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await chat_service.get_chat(db, auth, str(chat_id))

    result = await db.execute(
        select(ChatAttachment).where(
            ChatAttachment.id == str(attachment_id),
            ChatAttachment.org_id == auth.org_id,
            ChatAttachment.chat_id == str(chat_id),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("Attachment", str(attachment_id))

    if row.turn_id is not None:
        raise ConflictError(
            "Attachment is linked to a turn and cannot be removed from the composer."
        )

    relative_path = row.relative_path
    await db.delete(row)
    await db.flush()

    # Prefer DB removal first to avoid orphaned references; missing files are OK.
    try:
        safe_delete_attachment_file(relative_path)
    except UnsafeAttachmentPathError:
        logger.error(
            "chat_attachment_delete_unsafe_path",
            attachment_id=str(attachment_id),
            relative_path=relative_path,
        )

    return MessageResponse(message="Attachment deleted")


@router.post("/{chat_id}/share", response_model=ShareLinkResponse)
async def create_share_link(
    chat_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await share_service.create_link(db, auth, str(chat_id))


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
