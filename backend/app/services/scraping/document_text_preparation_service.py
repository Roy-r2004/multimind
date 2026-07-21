"""Deterministic readable-text preparation for persisted source documents."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.db.models import (
    ScrapingExecution,
    ScrapingSourceCandidate,
    ScrapingSourceDocument,
    ScrapingSourceDocumentChunk,
    ScrapingSourceDocumentText,
    SourceDocumentTextPreparationStatus,
)

PARSER_VERSION = "readable-text-v1"
SUPPORTED_TEXT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/json",
    "application/xml",
    "text/xml",
}
BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "p",
    "section",
    "td",
    "th",
    "tr",
}
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas"}
BOILERPLATE_TAGS = {"nav", "aside"}
WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
PARAGRAPH_RE = re.compile(r"\n{3,}")


class PreparedDocumentSummary(BaseModel):
    id: str | None = None
    source_document_id: str
    preparation_status: str
    failure_classification: str | None = None
    character_count: int = 0
    original_character_count: int = 0
    truncated: bool = False
    prepared_text_hash: str | None = None
    chunk_count: int = 0


class SourceDocumentPreparationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    execution_id: str
    source_document_id: str
    language_hint: str | None = None


@dataclass(frozen=True)
class PreparedTextResult:
    text: str
    original_character_count: int
    truncated: bool
    title: str | None
    detected_language: str | None
    failure_classification: str | None = None


class DocumentTextPreparationService:
    async def prepare(
        self,
        db: AsyncSession,
        context: SourceDocumentPreparationContext,
    ) -> PreparedDocumentSummary:
        document = await self._load_document(db, context)
        existing = await self._existing_prepared_text(db, context, document.content_sha256)
        if existing is not None:
            chunks = await self.ensure_chunks(db, existing)
            return _prepared_summary(existing, len(chunks))

        try:
            result = self.prepare_text_from_document(document, language_hint=context.language_hint)
            status = SourceDocumentTextPreparationStatus.PREPARED
        except PreparationError as exc:
            result = PreparedTextResult(
                text="",
                original_character_count=len(document.content_text or ""),
                truncated=False,
                title=None,
                detected_language=context.language_hint,
                failure_classification=exc.classification,
            )
            status = SourceDocumentTextPreparationStatus.FAILED

        prepared_hash = _sha256_text(result.text)
        row = ScrapingSourceDocumentText(
            organization_id=document.organization_id,
            execution_id=document.execution_id,
            source_document_id=document.id,
            source_candidate_id=document.source_candidate_id,
            coverage_cell_id=await self._coverage_cell_id(db, document),
            parser_version=PARSER_VERSION,
            source_content_hash=document.content_sha256,
            prepared_text_hash=prepared_hash,
            detected_language=result.detected_language,
            title=(result.title or "")[:300] or None,
            prepared_text=result.text,
            character_count=len(result.text),
            original_character_count=result.original_character_count,
            truncated=result.truncated,
            preparation_status=status,
            failure_classification=result.failure_classification,
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            existing = await self._existing_prepared_text(db, context, document.content_sha256)
            if existing is None:
                raise
            chunks = await self.ensure_chunks(db, existing)
            return _prepared_summary(existing, len(chunks))

        chunks = await self.ensure_chunks(db, row) if status == SourceDocumentTextPreparationStatus.PREPARED else []
        await db.commit()
        await db.refresh(row)
        return _prepared_summary(row, len(chunks))

    async def ensure_chunks(
        self,
        db: AsyncSession,
        prepared: ScrapingSourceDocumentText,
    ) -> list[ScrapingSourceDocumentChunk]:
        result = await db.execute(
            select(ScrapingSourceDocumentChunk)
            .where(ScrapingSourceDocumentChunk.prepared_text_id == prepared.id)
            .order_by(ScrapingSourceDocumentChunk.chunk_index)
        )
        existing = list(result.scalars().all())
        if existing:
            return existing
        rows = [
            ScrapingSourceDocumentChunk(
                organization_id=prepared.organization_id,
                execution_id=prepared.execution_id,
                source_document_id=prepared.source_document_id,
                prepared_text_id=prepared.id,
                coverage_cell_id=prepared.coverage_cell_id,
                chunk_index=chunk.index,
                character_start=chunk.start,
                character_end=chunk.end,
                chunk_text=chunk.text,
                chunk_hash=chunk.hash,
            )
            for chunk in chunk_text(prepared.prepared_text)
        ]
        db.add_all(rows)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            result = await db.execute(
                select(ScrapingSourceDocumentChunk)
                .where(ScrapingSourceDocumentChunk.prepared_text_id == prepared.id)
                .order_by(ScrapingSourceDocumentChunk.chunk_index)
            )
            return list(result.scalars().all())
        return rows

    async def recreate_chunks(
        self, db: AsyncSession, prepared: ScrapingSourceDocumentText
    ) -> list[ScrapingSourceDocumentChunk]:
        await db.execute(
            delete(ScrapingSourceDocumentChunk).where(
                ScrapingSourceDocumentChunk.prepared_text_id == prepared.id
            )
        )
        await db.flush()
        return await self.ensure_chunks(db, prepared)

    def prepare_text_from_document(
        self,
        document: ScrapingSourceDocument,
        *,
        language_hint: str | None = None,
    ) -> PreparedTextResult:
        media_type = _media_type(document.content_type)
        if not media_type or not _is_supported(media_type):
            raise PreparationError("unsupported_content_type")
        source = document.content_text
        if not source:
            raise PreparationError("missing_document_content")
        if media_type in {"text/html", "application/xhtml+xml"}:
            text, title = _prepare_html(source)
        elif media_type == "text/plain":
            text, title = _normalize_text(source), None
        elif media_type == "application/json" or media_type.endswith("+json"):
            text, title = _prepare_json(source), None
        elif media_type in {"application/xml", "text/xml"} or media_type.endswith("+xml"):
            text, title = _prepare_xml(source), None
        else:
            raise PreparationError("unsupported_content_type")
        original_count = len(text)
        max_chars = get_settings().facility_extraction_max_document_characters
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars].rstrip()
        if not text.strip():
            raise PreparationError("empty_prepared_text")
        return PreparedTextResult(
            text=text,
            original_character_count=original_count,
            truncated=truncated,
            title=title,
            detected_language=language_hint,
        )

    async def _load_document(
        self, db: AsyncSession, context: SourceDocumentPreparationContext
    ) -> ScrapingSourceDocument:
        result = await db.execute(
            select(ScrapingSourceDocument).where(
                ScrapingSourceDocument.id == context.source_document_id,
                ScrapingSourceDocument.organization_id == context.organization_id,
                ScrapingSourceDocument.execution_id == context.execution_id,
            )
        )
        document = result.scalar_one_or_none()
        if document is None:
            exists = await db.scalar(
                select(ScrapingExecution.id).where(
                    ScrapingExecution.id == context.execution_id,
                    ScrapingExecution.organization_id == context.organization_id,
                )
            )
            if exists is None:
                raise NotFoundError("ScrapingExecution", context.execution_id)
            raise NotFoundError("ScrapingSourceDocument", context.source_document_id)
        return document

    async def _coverage_cell_id(
        self, db: AsyncSession, document: ScrapingSourceDocument
    ) -> str | None:
        return await db.scalar(
            select(ScrapingSourceCandidate.coverage_cell_id).where(
                ScrapingSourceCandidate.id == document.source_candidate_id,
                ScrapingSourceCandidate.organization_id == document.organization_id,
            )
        )

    async def _existing_prepared_text(
        self,
        db: AsyncSession,
        context: SourceDocumentPreparationContext,
        content_hash: str,
    ) -> ScrapingSourceDocumentText | None:
        result = await db.execute(
            select(ScrapingSourceDocumentText).where(
                ScrapingSourceDocumentText.organization_id == context.organization_id,
                ScrapingSourceDocumentText.source_document_id == context.source_document_id,
                ScrapingSourceDocumentText.parser_version == PARSER_VERSION,
                ScrapingSourceDocumentText.source_content_hash == content_hash,
            )
        )
        return result.scalar_one_or_none()


class PreparationError(Exception):
    def __init__(self, classification: str) -> None:
        super().__init__(classification)
        self.classification = classification


@dataclass(frozen=True)
class TextChunk:
    index: int
    start: int
    end: int
    text: str
    hash: str


def chunk_text(text: str) -> list[TextChunk]:
    settings = get_settings()
    size = settings.facility_extraction_chunk_characters
    overlap = settings.facility_extraction_chunk_overlap_characters
    max_chunks = settings.facility_extraction_max_chunks_per_document
    chunks: list[TextChunk] = []
    start = 0
    while start < len(text) and len(chunks) < max_chunks:
        hard_end = min(len(text), start + size)
        end = _preferred_boundary(text, start, hard_end)
        if end <= start:
            end = hard_end
        chunk = text[start:end]
        if chunk:
            chunks.append(TextChunk(len(chunks), start, end, chunk, _sha256_text(chunk)))
        if end >= len(text):
            break
        start = max(0, end - overlap)
        if chunks and start <= chunks[-1].start:
            start = chunks[-1].end
    return chunks


def _preferred_boundary(text: str, start: int, hard_end: int) -> int:
    if hard_end >= len(text):
        return hard_end
    window_start = max(start + 1, hard_end - 1200)
    for marker in ("\n\n", ". ", "! ", "? ", "\n"):
        idx = text.rfind(marker, window_start, hard_end)
        if idx != -1:
            return idx + len(marker)
    return hard_end


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip_depth = 0
        self._boilerplate_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {key.lower(): (value or "").lower() for key, value in attrs}
        if tag in SKIP_TAGS or _hidden(attrs_dict):
            self._skip_depth += 1
            return
        if tag in BOILERPLATE_TAGS:
            self._boilerplate_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in BOILERPLATE_TAGS and self._boilerplate_depth:
            self._boilerplate_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        if self._boilerplate_depth:
            return
        self.parts.append(data)


def _prepare_html(source: str) -> tuple[str, str | None]:
    parser = _ReadableHTMLParser()
    try:
        parser.feed(source)
    except Exception as exc:
        raise PreparationError("malformed_html") from exc
    title = _normalize_text(" ".join(parser.title_parts))[:300] or None
    return _normalize_text(html.unescape(" ".join(parser.parts))), title


def _prepare_json(source: str) -> str:
    try:
        data = json.loads(source)
    except json.JSONDecodeError as exc:
        raise PreparationError("malformed_json") from exc
    lines: list[str] = []
    _flatten_json(data, lines, "", depth=0)
    return _normalize_text("\n".join(lines))


def _flatten_json(value: Any, lines: list[str], path: str, *, depth: int) -> None:
    if len(lines) >= 5000 or depth > 8:
        return
    if isinstance(value, dict):
        for key in sorted(value):
            _flatten_json(value[key], lines, f"{path}.{key}" if path else str(key), depth=depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value[:200]):
            _flatten_json(item, lines, f"{path}[{index}]", depth=depth + 1)
    elif value is not None:
        text = str(value).strip()
        if text:
            lines.append(f"{path}: {text}"[:2000] if path else text[:2000])


def _prepare_xml(source: str) -> str:
    if "<!ENTITY" in source.upper() or "<!DOCTYPE" in source.upper():
        raise PreparationError("malformed_xml")
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError as exc:
        raise PreparationError("malformed_xml") from exc
    return _normalize_text(" ".join(text for text in root.itertext() if text and text.strip()))


def _normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [WHITESPACE_RE.sub(" ", line).strip() for line in value.split("\n")]
    text = "\n".join(line for line in lines if line)
    return PARAGRAPH_RE.sub("\n\n", html.unescape(text)).strip()


def _hidden(attrs: dict[str, str]) -> bool:
    if "hidden" in attrs:
        return True
    aria = attrs.get("aria-hidden")
    style = attrs.get("style", "")
    return aria == "true" or "display:none" in style.replace(" ", "") or "visibility:hidden" in style.replace(" ", "")


def _media_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower()


def _is_supported(media_type: str) -> bool:
    return media_type in SUPPORTED_TEXT_TYPES or media_type.endswith("+json") or media_type.endswith("+xml")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _prepared_summary(row: ScrapingSourceDocumentText, chunk_count: int) -> PreparedDocumentSummary:
    return PreparedDocumentSummary(
        id=row.id,
        source_document_id=row.source_document_id,
        preparation_status=row.preparation_status.value,
        failure_classification=row.failure_classification,
        character_count=row.character_count,
        original_character_count=row.original_character_count,
        truncated=row.truncated,
        prepared_text_hash=row.prepared_text_hash,
        chunk_count=chunk_count,
    )


document_text_preparation_service = DocumentTextPreparationService()
