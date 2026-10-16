"""ARQ worker for the documents queue.

Jobs carry primitive ids only. The worker reads chat and library files from the
same durable directories as the API. A restart continues pages that do not yet
have a stored section, and it does not OCR a page twice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar
from urllib.parse import urlparse

from arq.connections import RedisSettings
from arq.worker import func
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.models import ChatAttachment, LibraryItem, ProcessedDocument
from app.db.session import AsyncSessionLocal
from app.services.chat_attachment_storage import resolve_attachment_path
from app.services.document_processing import _as_utc, prepare_stored_file

logger = get_logger(__name__)

DOCUMENT_WORKER_NAME = "documents"


def document_redis_settings() -> RedisSettings:
    parsed = urlparse(get_settings().redis_url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or "0"),
        password=parsed.password,
    )


async def process_document_job(ctx: dict, kind: str, source_id: str, org_id: str) -> dict:
    if not get_settings().large_documents_enabled:
        return {"status": "skipped", "reason": "flag_off"}
    session_factory = ctx.get("session_factory") or AsyncSessionLocal
    async with session_factory() as db:
        if kind == "library":
            item = await db.get(LibraryItem, source_id)
            if item is None or item.org_id != org_id or not item.relative_path:
                return {"status": "missing"}
            path = resolve_attachment_path(item.relative_path, root=get_settings().library_file_dir)
            extension = "." + item.stored_name.rsplit(".", 1)[-1] if item.stored_name and "." in item.stored_name else ""
            await prepare_stored_file(
                db,
                org_id=org_id,
                path=path,
                extension=extension,
                filename=item.original_filename or item.title,
                library_item=item,
            )
            return {"status": "ready", "source_id": source_id}
        attachment = await db.get(ChatAttachment, source_id)
        if attachment is None or attachment.org_id != org_id:
            return {"status": "missing"}
        path = resolve_attachment_path(attachment.relative_path)
        extension = "." + attachment.stored_name.rsplit(".", 1)[-1] if "." in attachment.stored_name else ""
        await prepare_stored_file(
            db,
            org_id=org_id,
            path=path,
            extension=extension,
            filename=attachment.filename,
            chat_id=attachment.chat_id,
            attachment=attachment,
        )
        return {"status": "ready", "source_id": source_id}


async def recover_stale_documents(ctx: dict) -> int:
    """Re-queue attachments whose preparation heartbeat stopped.

    The job itself skips pages that already have a stored section, so a restart
    does not repeat OCR or embeddings for finished pages.
    """
    settings = get_settings()
    if not settings.large_documents_enabled:
        return 0
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.document_processing_stale_seconds)
    session_factory = ctx.get("session_factory") or AsyncSessionLocal
    recovered = 0
    async with session_factory() as db:
        documents = (
            await db.execute(
                select(ProcessedDocument).where(ProcessedDocument.status == "processing")
            )
        ).scalars().all()
        stale_hashes = {
            (row.org_id, row.content_sha256)
            for row in documents
            if row.heartbeat_at is None or _as_utc(row.heartbeat_at) < cutoff
        }
        if not stale_hashes:
            return 0
        attachments = (
            await db.execute(
                select(ChatAttachment).where(ChatAttachment.excerpt_status == "processing")
            )
        ).scalars().all()
        items = (
            await db.execute(
                select(LibraryItem).where(LibraryItem.excerpt_status == "processing")
            )
        ).scalars().all()
    from app.services.document_processing import enqueue_processing

    seen: set[tuple[str, str]] = set()
    for attachment in attachments:
        key = (attachment.org_id, attachment.content_sha256 or "")
        if key not in stale_hashes or key in seen:
            continue
        seen.add(key)
        await enqueue_processing(kind="chat", source_id=attachment.id, org_id=attachment.org_id)
        recovered += 1
    for item in items:
        key = (item.org_id, item.content_sha256 or "")
        if key not in stale_hashes or key in seen:
            continue
        seen.add(key)
        await enqueue_processing(kind="library", source_id=item.id, org_id=item.org_id)
        recovered += 1
    return recovered


async def startup(ctx: dict) -> None:
    setup_logging()
    redis = ctx.get("redis")
    if redis is not None:
        await redis.ping()
    ctx["session_factory"] = AsyncSessionLocal
    await recover_stale_documents(ctx)
    logger.info("document_worker_ready", queue=get_settings().document_worker_queue_name)


async def shutdown(ctx: dict) -> None:
    ctx.pop("session_factory", None)


class WorkerSettings:
    functions: ClassVar[list] = [func(process_document_job, max_tries=1)]
    queue_name = get_settings().document_worker_queue_name
    redis_settings = document_redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = get_settings().document_worker_concurrency
    job_timeout = get_settings().document_worker_job_timeout_seconds
    keep_result = get_settings().document_worker_keep_result_seconds
    health_check_interval = 30
    max_tries = 1
