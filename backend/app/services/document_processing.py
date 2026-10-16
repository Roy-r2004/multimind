"""Background preparation for large documents.

Flag off: callers keep the synchronous PR #98 extract. Flag on: uploads are
hashed, deduplicated inside the organization, and prepared on the documents
queue. A Send that arrives while preparation is still running is accepted and
waits here; the user does not send again.

The design note that would block Send is intentionally not implemented.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import (
    ChatAttachment,
    CostRecord,
    LibraryItem,
    ProcessedChunk,
    ProcessedDocument,
    ProcessedSection,
    UsageKind,
)
from app.services.attachment_types import AUDIO_EXTENSIONS, is_image_extension
from app.services.chat_attachment_text import (
    _UNREADABLE_PAGE,
    extract_attachment_text_from_path,
    pdf_page_texts,
)
from app.services.document_context import (
    DocumentSource,
    requests_complete_coverage,
    sources_from_attachment_rows,
)

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_EMBED_DIMS = 64
PROCESSING_STATUS = "processing"


def upload_max_bytes(*, library: bool = False) -> int:
    settings = get_settings()
    configured = settings.library_file_max_bytes if library else settings.chat_attachment_max_bytes
    if not settings.large_documents_enabled:
        return configured
    return max(configured, settings.large_document_max_bytes)


def should_defer_extraction(extension: str) -> bool:
    if not get_settings().large_documents_enabled:
        return False
    ext = extension.lower()
    return ext not in AUDIO_EXTENSIONS and not is_image_extension(ext)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_embedding(text: str) -> list[float]:
    """Stable lexical vector. No provider call and no spend."""
    vector = [0.0] * _EMBED_DIMS
    for token in _TOKEN_RE.findall(text.lower()):
        slot = int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:8], 16) % _EMBED_DIMS
        vector[slot] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


def _keyword_score(question: str, text: str) -> float:
    needles = set(_TOKEN_RE.findall(question.lower()))
    if not needles:
        return 0.0
    haystack = set(_TOKEN_RE.findall(text.lower()))
    return len(needles & haystack) / len(needles)


def hybrid_select(question: str, chunks: list[ProcessedChunk], *, limit_chars: int) -> list[ProcessedChunk]:
    """Keyword hits stay even when they sit in the middle or the end."""
    if not chunks:
        return []
    query_vector = local_embedding(question)
    ranked = sorted(
        chunks,
        key=lambda chunk: (
            0.5 * _cosine(query_vector, list(chunk.embedding or local_embedding(chunk.text)))
            + 0.5 * _keyword_score(question, chunk.text)
        ),
        reverse=True,
    )
    selected: list[ProcessedChunk] = []
    used = 0
    keywords = set(_TOKEN_RE.findall(question.lower()))
    for chunk in ranked:
        lexical_hit = bool(keywords and keywords & set(_TOKEN_RE.findall(chunk.text.lower())))
        if selected and used >= limit_chars and not lexical_hit:
            continue
        selected.append(chunk)
        used += len(chunk.text or "")
        if used >= limit_chars and not any(
            keywords & set(_TOKEN_RE.findall(item.text.lower()))
            for item in ranked
            if item not in selected
        ):
            break
    selected.sort(key=lambda chunk: chunk.chunk_index)
    return selected


def render_page(text: str, page_number: int | None, *, origin: str) -> str:
    body = (text or "").strip()
    if page_number is None:
        return body
    label = f"[p. {page_number}]"
    if origin == "ocr":
        return f"{label}\n{body}"
    return f"{label}\n{body}"


def _chunks_for(text: str, *, page_number: int | None) -> list[tuple[int | None, str]]:
    width = get_settings().document_chunk_chars
    body = (text or "").strip()
    if not body:
        return []
    return [(page_number, body[start : start + width]) for start in range(0, len(body), width)]


def render_pdf_page_png(path: Path, page_number: int) -> bytes:
    """Rasterize one PDF page with pypdfium2. PyMuPDF is not used."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[page_number - 1]
        bitmap = page.render(scale=1.5)
        image = bitmap.to_pil()
        from io import BytesIO

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        pdf.close()


async def default_ocr_pages(images: list[tuple[int, bytes]]) -> list[tuple[int, str, dict[str, Any]]]:
    """OCR scanned pages through OpenRouter vision.

    Each rendered PNG is sent as an image_url part, the same structure chat
    image analysis uses. A page that fails stays empty so the caller can mark
    it unread instead of inventing text. Native-text pages are not included
    by the caller.
    """
    settings = get_settings()
    model_name = (settings.document_ocr_model or "").strip()
    if not settings.large_documents_enabled or not model_name or not images:
        return []
    import base64

    from app.llm.providers import get_provider_registry
    from app.services.chat_vision import VisionImage

    provider = get_provider_registry().get_provider("openrouter")
    results: list[tuple[int, str, dict[str, Any]]] = []
    for page_number, png in images:
        if not png:
            results.append((page_number, "", {"model_id": model_name, "failed": True}))
            continue
        vision = VisionImage(
            filename=f"page-{page_number}.png",
            media_type="image/png",
            data_base64=base64.b64encode(png).decode("ascii"),
        )
        try:
            response = await provider.complete(
                system=(
                    "Transcribe the scanned document page. Return only the visible "
                    "text, preserving reading order. Do not describe the page."
                ),
                user=f"Transcribe page {page_number}.",
                model=model_name,
                max_tokens=1200,
                temperature=0,
                images=[vision],
            )
        except Exception:  # noqa: BLE001 — one failed page must not drop the rest
            logger.warning("document_ocr_page_failed", page=page_number, model=model_name)
            results.append((page_number, "", {"model_id": model_name, "failed": True}))
            continue
        results.append(
            (
                page_number,
                (response.text or "").strip(),
                {
                    "tokens_input": response.tokens_input,
                    "tokens_output": response.tokens_output,
                    "cost_usd": response.cost_usd or 0,
                    "model_id": model_name,
                },
            )
        )
    return results


def _native_units(path: Path, extension: str) -> tuple[list[dict[str, Any]], bool]:
    ext = extension.lower()
    if ext == ".pdf":
        pages, total, truncated = pdf_page_texts(path)
        units = [
            {
                "page_number": number,
                "worksheet": None,
                "text": text,
                "origin": "native" if text else "empty",
            }
            for number, text in pages
        ]
        if total > len(pages):
            units.append(
                {
                    "page_number": None,
                    "worksheet": None,
                    "text": f"{total - len(pages)} later pages were not extracted.",
                    "origin": "native",
                }
            )
        return units, truncated
    text, status = extract_attachment_text_from_path(path, ext)
    truncated = status == "partial"
    return [
        {
            "page_number": None,
            "worksheet": None,
            "text": text or "",
            "origin": "native" if text else "empty",
        }
    ], truncated


async def _record_cost(
    db: AsyncSession,
    *,
    org_id: str,
    chat_id: str | None,
    model_id: str,
    tokens_input: int,
    tokens_output: int,
    cost_usd: float,
    stage: str,
) -> None:
    db.add(
        CostRecord(
            org_id=org_id,
            chat_id=chat_id,
            turn_id=None,
            model_id=model_id[:64],
            kind=UsageKind.DOCUMENT_PROCESSING,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_usd=cost_usd,
            metadata_={"stage": stage},
        )
    )


async def _load_document(db: AsyncSession, org_id: str, digest: str) -> ProcessedDocument | None:
    return (
        await db.execute(
            select(ProcessedDocument).where(
                ProcessedDocument.org_id == org_id,
                ProcessedDocument.content_sha256 == digest,
            )
        )
    ).scalar_one_or_none()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _heartbeat_fresh(document: ProcessedDocument) -> bool:
    if document.heartbeat_at is None:
        return False
    age = datetime.now(UTC) - _as_utc(document.heartbeat_at)
    return age < timedelta(seconds=get_settings().document_processing_stale_seconds)


async def process_content_hash(
    db: AsyncSession,
    *,
    org_id: str,
    digest: str,
    path: Path,
    extension: str,
    filename: str,
    chat_id: str | None = None,
    ocr_pages=default_ocr_pages,
) -> ProcessedDocument:
    """Extract, OCR blank PDF pages, and store sections and chunks.

    Pages that already have a section are not sent to OCR again. A fresh
    heartbeat means another worker owns the job.
    """
    existing = await _load_document(db, org_id, digest)
    if existing is not None and existing.status in {"ready", "partial"}:
        return existing
    if existing is not None and existing.status == PROCESSING_STATUS and _heartbeat_fresh(existing):
        return existing

    document = existing or ProcessedDocument(
        org_id=org_id,
        content_sha256=digest,
        filename=filename[:255],
        status=PROCESSING_STATUS,
    )
    document.status = PROCESSING_STATUS
    document.heartbeat_at = datetime.now(UTC)
    document.error = None
    if existing is None:
        db.add(document)
    await db.flush()

    stored_sections = (
        await db.execute(
            select(ProcessedSection)
            .where(ProcessedSection.document_id == document.id, ProcessedSection.org_id == org_id)
            .order_by(ProcessedSection.section_index)
        )
    ).scalars().all()
    done_indexes = {section.section_index for section in stored_sections}
    units, truncated = _native_units(path, extension)
    document.pages_total = sum(1 for unit in units if unit["page_number"] is not None) or len(units)

    pending_ocr: list[dict[str, Any]] = []
    for index, unit in enumerate(units):
        if index in done_indexes:
            continue
        if unit["origin"] == "empty" and extension.lower() == ".pdf" and unit["page_number"] is not None:
            pending_ocr.append({**unit, "section_index": index})
            continue
        db.add(
            ProcessedSection(
                document_id=document.id,
                org_id=org_id,
                section_index=index,
                page_number=unit["page_number"],
                worksheet=unit["worksheet"],
                origin="native",
                text=unit["text"],
            )
        )
        document.pages_done = index + 1
        document.heartbeat_at = datetime.now(UTC)
        await db.flush()

    unread: list[int] = []
    batch_size = get_settings().document_ocr_batch_pages
    for start in range(0, len(pending_ocr), batch_size):
        batch = pending_ocr[start : start + batch_size]
        images: list[tuple[int, bytes]] = []
        for unit in batch:
            try:
                images.append((unit["page_number"], render_pdf_page_png(path, unit["page_number"])))
            except Exception:  # noqa: BLE001 — renderer failures must leave the page unread
                logger.warning("document_ocr_render_failed", page=unit["page_number"])
                images.append((unit["page_number"], b""))
        recognized = {page: (text, usage) for page, text, usage in await ocr_pages(images)}
        spend_in = spend_out = 0
        spend_cost = 0.0
        model_id = get_settings().document_ocr_model or "ocr"
        for unit in batch:
            text, usage = recognized.get(unit["page_number"], ("", {}))
            origin = "ocr" if text else "unread"
            if not text:
                unread.append(unit["page_number"])
                text = _UNREADABLE_PAGE
            db.add(
                ProcessedSection(
                    document_id=document.id,
                    org_id=org_id,
                    section_index=unit["section_index"],
                    page_number=unit["page_number"],
                    worksheet=None,
                    origin=origin,
                    text=text,
                )
            )
            spend_in += int(usage.get("tokens_input") or 0)
            spend_out += int(usage.get("tokens_output") or 0)
            spend_cost += float(usage.get("cost_usd") or 0)
            model_id = str(usage.get("model_id") or model_id)
        if spend_in or spend_out or spend_cost:
            await _record_cost(
                db,
                org_id=org_id,
                chat_id=chat_id,
                model_id=model_id,
                tokens_input=spend_in,
                tokens_output=spend_out,
                cost_usd=spend_cost,
                stage="ocr",
            )
        document.pages_done = min(document.pages_total, document.pages_done + len(batch))
        document.heartbeat_at = datetime.now(UTC)
        await db.flush()

    sections = (
        await db.execute(
            select(ProcessedSection)
            .where(ProcessedSection.document_id == document.id, ProcessedSection.org_id == org_id)
            .order_by(ProcessedSection.section_index)
        )
    ).scalars().all()
    existing_chunks = (
        await db.execute(
            select(ProcessedChunk.chunk_index).where(
                ProcessedChunk.document_id == document.id,
                ProcessedChunk.org_id == org_id,
            )
        )
    ).scalars().all()
    if not existing_chunks:
        chunk_index = 0
        for section in sections:
            if section.origin == "unread":
                continue
            for page_number, piece in _chunks_for(section.text, page_number=section.page_number):
                db.add(
                    ProcessedChunk(
                        document_id=document.id,
                        org_id=org_id,
                        chunk_index=chunk_index,
                        page_number=page_number,
                        text=piece,
                        embedding=local_embedding(piece),
                    )
                )
                chunk_index += 1
        if chunk_index:
            await _record_cost(
                db,
                org_id=org_id,
                chat_id=chat_id,
                model_id=get_settings().document_embedding_model or "local-hash",
                tokens_input=0,
                tokens_output=0,
                cost_usd=0,
                stage="embedding",
            )

    document.unread_pages = unread
    document.pages_done = document.pages_total
    document.heartbeat_at = datetime.now(UTC)
    document.status = "partial" if truncated or unread else "ready"
    await db.flush()
    return document


def _excerpt_from_sections(sections: list[ProcessedSection], *, truncated: bool) -> tuple[str, str]:
    parts = [
        render_page(section.text, section.page_number, origin=section.origin)
        for section in sections
        if section.origin != "unread" or section.text
    ]
    text = "\n\n".join(part for part in parts if part).strip()
    if truncated:
        text = f"{text}\n[Content truncated]".strip()
    status = "partial" if truncated or any(section.origin == "unread" for section in sections) else "ready"
    if not text:
        return "", "empty"
    return text, status


async def publish_processed_text(
    db: AsyncSession,
    document: ProcessedDocument,
    *,
    attachment: ChatAttachment | None = None,
    library_item: LibraryItem | None = None,
) -> None:
    sections = (
        await db.execute(
            select(ProcessedSection)
            .where(
                ProcessedSection.document_id == document.id,
                ProcessedSection.org_id == document.org_id,
            )
            .order_by(ProcessedSection.section_index)
        )
    ).scalars().all()
    text, status = _excerpt_from_sections(sections, truncated=document.status == "partial")
    if document.unread_pages:
        status = "partial"
    targets: list[Any] = []
    if attachment is not None:
        targets.append(attachment)
        siblings = (
            await db.execute(
                select(ChatAttachment).where(
                    ChatAttachment.org_id == attachment.org_id,
                    ChatAttachment.relative_path == attachment.relative_path,
                )
            )
        ).scalars().all()
        targets.extend(siblings)
    if library_item is not None:
        targets.append(library_item)
    for row in targets:
        row.content_sha256 = document.content_sha256
        row.text_excerpt = text or None
        row.excerpt_status = status if text or status == "empty" else document.status


async def prepare_stored_file(
    db: AsyncSession,
    *,
    org_id: str,
    path: Path,
    extension: str,
    filename: str,
    chat_id: str | None = None,
    attachment: ChatAttachment | None = None,
    library_item: LibraryItem | None = None,
    ocr_pages=default_ocr_pages,
) -> ProcessedDocument:
    digest = hash_file(path)
    document = await process_content_hash(
        db,
        org_id=org_id,
        digest=digest,
        path=path,
        extension=extension,
        filename=filename,
        chat_id=chat_id,
        ocr_pages=ocr_pages,
    )
    await publish_processed_text(
        db, document, attachment=attachment, library_item=library_item
    )
    await db.commit()
    return document


async def enqueue_processing(
    *,
    kind: str,
    source_id: str,
    org_id: str,
) -> None:
    """Queue one documents-worker job. Redis failures leave the row processing."""
    settings = get_settings()
    if not settings.large_documents_enabled:
        return
    try:
        from urllib.parse import urlparse

        from arq import create_pool
        from arq.connections import RedisSettings

        parsed = urlparse(settings.redis_url)
        redis = RedisSettings(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            database=int((parsed.path or "/0").lstrip("/") or "0"),
            password=parsed.password,
        )
        pool = await create_pool(redis, default_queue_name=settings.document_worker_queue_name)
        await pool.enqueue_job(
            "process_document_job",
            kind,
            source_id,
            org_id,
            _queue_name=settings.document_worker_queue_name,
        )
        await pool.close()
    except Exception:
        logger.exception("document_enqueue_failed", kind=kind, source_id=source_id)


async def wait_for_document_processing(db: AsyncSession, rows: list) -> None:
    """Block the in-flight turn until required attachments leave processing.

    This is the one-Send behavior. The turn is already accepted.
    """
    settings = get_settings()
    if not settings.large_documents_enabled:
        return
    deadline = asyncio.get_running_loop().time() + settings.document_processing_wait_seconds
    while True:
        pending = [row for row in rows if (getattr(row, "excerpt_status", None) or "") == PROCESSING_STATUS]
        if not pending:
            return
        if asyncio.get_running_loop().time() >= deadline:
            for row in pending:
                row.excerpt_status = "partial"
                note = "Document preparation did not finish before the turn wait elapsed."
                row.text_excerpt = ((row.text_excerpt or "").rstrip() + "\n" + note).strip()
            return
        await asyncio.sleep(settings.document_processing_poll_seconds)
        for row in pending:
            await db.refresh(row)


async def resolve_document_sources(
    db: AsyncSession,
    rows: list,
    recovery_notes: dict | None,
    question: str,
) -> list[DocumentSource]:
    """Flag off returns the stored excerpts unchanged.

    Flag on uses hybrid retrieval for a targeted question and every stored
    section for a whole-document request. Unread pages stay marked unread.
    """
    base = sources_from_attachment_rows(rows, recovery_notes)
    if not get_settings().large_documents_enabled:
        return base
    complete = requests_complete_coverage(question)
    resolved: list[DocumentSource] = []
    for row, source in zip(rows, base, strict=False):
        digest = getattr(row, "content_sha256", None)
        org_id = getattr(row, "org_id", None)
        if not digest or not org_id:
            resolved.append(source)
            continue
        document = await _load_document(db, org_id, digest)
        if document is None:
            resolved.append(source)
            continue
        sections = (
            await db.execute(
                select(ProcessedSection)
                .where(
                    ProcessedSection.document_id == document.id,
                    ProcessedSection.org_id == org_id,
                )
                .order_by(ProcessedSection.section_index)
            )
        ).scalars().all()
        chunks = (
            await db.execute(
                select(ProcessedChunk)
                .where(ProcessedChunk.document_id == document.id, ProcessedChunk.org_id == org_id)
                .order_by(ProcessedChunk.chunk_index)
            )
        ).scalars().all()
        unread = document.unread_pages or []
        coverage = ""
        if unread:
            coverage = " Unread pages were not analyzed: " + ", ".join(str(page) for page in unread) + "."
        if complete or not chunks:
            text, status = _excerpt_from_sections(sections, truncated=document.status == "partial")
        else:
            chosen = hybrid_select(question, list(chunks), limit_chars=120_000)
            text = "\n\n".join(
                render_page(chunk.text, chunk.page_number, origin="native") for chunk in chosen
            ).strip()
            status = source.status
        resolved.append(
            DocumentSource(
                filename=source.filename,
                text=text or source.text,
                status=(status or source.status) + coverage,
                recovery_note=source.recovery_note,
            )
        )
    return resolved
