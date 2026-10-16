"""Extract attachment text. Successful extraction is stored in full up to an explicit safety cap."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from app.core.exceptions import InvalidAttachmentError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Explicit processing cap. Longer extracts are kept up to this size and marked partial.
ATTACHMENT_TEXT_EXCERPT_MAX = 1_500_000
_XLSX_MAX_SHEETS = 50
_XLSX_MAX_ROWS = 5_000
_XLSX_MAX_COLS = 64
_PDF_MAX_PAGES = 500
_TRUNCATION_MARKER = "\n[Content truncated]"
_UNREADABLE_PAGE = "[Unreadable page: no text extracted. This page may be scanned or image-based.]"
_BLANK_LINE_RE = re.compile(r"\n{3,}")


def extract_attachment_text(content: bytes, extension: str) -> tuple[str | None, str]:
    """Return (excerpt, excerpt_status) for an allowed attachment extension."""
    ext = extension.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp"}:
        # Images are vision inputs — never decode as text / OCR.
        return None, "image"
    try:
        if ext in {".docx"}:
            return _finalize(_extract_docx_text(content))
        if ext in {".xlsx"}:
            text, truncated = _extract_xlsx_text(content)
            return _finalize(text, truncated=truncated)
        if ext in {".pdf"}:
            text, truncated = _extract_pdf_text(content)
            return _finalize(text, truncated=truncated)
        return _finalize(_extract_plain_text(content))
    except InvalidAttachmentError:
        logger.warning(
            "chat_attachment_extraction_failed",
            extension=ext,
        )
        raise


def extract_attachment_text_from_path(path: Path, extension: str) -> tuple[str | None, str]:
    """Extract from a temp/final file path without requiring a full in-memory upload buffer.

    Office and PDF parsers open the path directly. Plain text is read up to the safety cap.
    Images skip text extraction. There is no OCR pass.
    """
    ext = extension.lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp"}:
        return None, "image"
    try:
        if ext in {".docx"}:
            return _finalize(_extract_docx_from_path(path))
        if ext in {".xlsx"}:
            text, truncated = _extract_xlsx_from_path(path)
            return _finalize(text, truncated=truncated)
        if ext in {".pdf"}:
            text, truncated = _extract_pdf_from_path(path)
            return _finalize(text, truncated=truncated)
        text, truncated = _extract_plain_text_from_path(path)
        return _finalize(text, truncated=truncated)
    except InvalidAttachmentError:
        logger.warning(
            "chat_attachment_extraction_failed",
            extension=ext,
        )
        raise


def _finalize(text: str, *, truncated: bool = False) -> tuple[str | None, str]:
    """Keep the extracted text. A parser limit is status ``partial``, never a silent prefix."""
    body = text or ""
    if truncated and body and not body.rstrip().endswith("[Content truncated]"):
        body = body.rstrip() + _TRUNCATION_MARKER
    if not body.strip():
        return None, "partial" if truncated else "empty"
    return body, "partial" if truncated else "ready"


def excerpt_from_transcript(text: str) -> tuple[str | None, str]:
    """Map a transcription string into attachment text_excerpt / excerpt_status."""
    return _finalize(text.strip())


def _looks_like_utf16(raw: bytes, *, high_byte_index: int) -> bool:
    """ASCII-range UTF-16 has a NUL in every other byte. Ordinary UTF-8 does not."""
    if len(raw) < 8 or len(raw) % 2 != 0:
        return False
    sample = raw[:200]
    pairs = len(sample) // 2
    nulls = sum(1 for index in range(high_byte_index, len(sample), 2) if sample[index] == 0)
    return pairs >= 4 and nulls / pairs >= 0.8


def decode_plain_text_bytes(raw: bytes) -> str:
    """Decode TXT bytes. UTF-8 stays UTF-8. BOM and NUL-paired UTF-16 are not misread."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    if _looks_like_utf16(raw, high_byte_index=1):
        return raw.decode("utf-16-le", errors="replace")
    if _looks_like_utf16(raw, high_byte_index=0):
        return raw.decode("utf-16-be", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _extract_plain_text(content: bytes) -> str:
    return decode_plain_text_bytes(content)


def _extract_plain_text_from_path(path: Path) -> tuple[str, bool]:
    """Read an accepted upload in full. Upload size is already bounded."""
    return decode_plain_text_bytes(path.read_bytes()), False


def _extract_docx_text(content: bytes) -> str:
    return _docx_document_to_text(_open_docx(BytesIO(content)))


def _extract_docx_from_path(path: Path) -> str:
    return _docx_document_to_text(_open_docx(str(path)))


def _open_docx(source: BytesIO | str):
    try:
        from docx import Document
        from docx.opc.exceptions import PackageNotFoundError
    except ImportError as exc:  # pragma: no cover - dependency should be installed
        raise InvalidAttachmentError("DOCX support is unavailable on this server") from exc

    try:
        return Document(source)
    except PackageNotFoundError as exc:
        raise InvalidAttachmentError("Invalid or corrupt DOCX file") from exc
    except Exception as exc:
        raise InvalidAttachmentError("Invalid or corrupt DOCX file") from exc


def _docx_document_to_text(document) -> str:
    parts: list[str] = []
    for paragraph in document.paragraphs:
        text = (paragraph.text or "").strip()
        if text:
            parts.append(text)

    for table in document.tables:
        for row in table.rows:
            cells = [(cell.text or "").strip() for cell in row.cells]
            if any(cells):
                parts.append("\t".join(cells))

    return "\n".join(parts)


def _extract_xlsx_text(content: bytes) -> tuple[str, bool]:
    return _xlsx_workbook_to_text(_open_xlsx(BytesIO(content)))


def _extract_xlsx_from_path(path: Path) -> tuple[str, bool]:
    return _xlsx_workbook_to_text(_open_xlsx(str(path)))


def _open_xlsx(source: BytesIO | str):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise InvalidAttachmentError("XLSX support is unavailable on this server") from exc

    try:
        return load_workbook(
            source,
            read_only=True,
            data_only=True,
        )
    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "encrypted" in message:
            raise InvalidAttachmentError(
                "Password-protected or encrypted Excel files are not supported"
            ) from exc
        raise InvalidAttachmentError("Invalid or corrupt XLSX file") from exc


def _xlsx_workbook_to_text(workbook) -> tuple[str, bool]:
    parts: list[str] = []
    truncated = False
    try:
        sheet_names = list(workbook.sheetnames)
        if len(sheet_names) > _XLSX_MAX_SHEETS:
            truncated = True
            sheet_names = sheet_names[:_XLSX_MAX_SHEETS]

        for sheet_name in sheet_names:
            worksheet = workbook[sheet_name]
            parts.append(f"Worksheet: {sheet_name}")
            row_count = 0
            for row in worksheet.iter_rows(max_col=_XLSX_MAX_COLS, values_only=True):
                values = [_cell_to_text(cell) for cell in row]
                if not any(value.strip() for value in values):
                    continue
                row_count += 1
                if row_count > _XLSX_MAX_ROWS:
                    truncated = True
                    break
                parts.append("\t".join(values).rstrip("\t"))
    finally:
        workbook.close()

    return "\n".join(parts).strip(), truncated


def _cell_to_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\t", " ").replace("\n", " ").strip()


def _extract_pdf_text(content: bytes) -> tuple[str, bool]:
    return _pdf_reader_to_text(_open_pdf(BytesIO(content)))


def _extract_pdf_from_path(path: Path) -> tuple[str, bool]:
    return _pdf_reader_to_text(_open_pdf(str(path)))


def _open_pdf(source: BytesIO | str):
    try:
        from pypdf import PdfReader
        from pypdf.errors import FileNotDecryptedError, PdfReadError
    except ImportError as exc:  # pragma: no cover
        raise InvalidAttachmentError("PDF support is unavailable on this server") from exc

    try:
        reader = PdfReader(source)
    except PdfReadError as exc:
        raise InvalidAttachmentError("Invalid or corrupt PDF file") from exc
    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "encrypt" in message or "decrypt" in message:
            raise InvalidAttachmentError(
                "Password-protected or encrypted PDFs are not supported"
            ) from exc
        raise InvalidAttachmentError("Invalid or corrupt PDF file") from exc

    if getattr(reader, "is_encrypted", False):
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:
            raise InvalidAttachmentError(
                "Password-protected or encrypted PDFs are not supported"
            ) from exc
        # pypdf returns 0 when the password fails; non-zero / True when unlocked.
        if unlocked in (0, False):
            raise InvalidAttachmentError(
                "Password-protected or encrypted PDFs are not supported"
            )

    try:
        # Touch page count early so encrypted/corrupt structures fail before extraction.
        _ = len(reader.pages)
    except FileNotDecryptedError as exc:
        raise InvalidAttachmentError(
            "Password-protected or encrypted PDFs are not supported"
        ) from exc
    except Exception as exc:
        raise InvalidAttachmentError("Invalid or corrupt PDF file") from exc

    return reader


def _pdf_reader_to_text(reader) -> tuple[str, bool]:
    truncated = False
    page_count = len(reader.pages)
    pages_to_scan = min(page_count, _PDF_MAX_PAGES)
    if page_count > _PDF_MAX_PAGES:
        truncated = True

    extracted: list[tuple[int, str]] = []
    any_text = False
    for index in range(pages_to_scan):
        try:
            raw = reader.pages[index].extract_text() or ""
        except Exception as exc:
            message = str(exc).lower()
            if "password" in message or "encrypt" in message or "decrypt" in message:
                raise InvalidAttachmentError(
                    "Password-protected or encrypted PDFs are not supported"
                ) from exc
            raise InvalidAttachmentError("Invalid or corrupt PDF file") from exc

        page_text = _BLANK_LINE_RE.sub("\n\n", raw).strip()
        if page_text:
            any_text = True
        extracted.append((index, page_text))

    if not any_text:
        return "", truncated

    parts: list[str] = []
    for index, page_text in extracted:
        if page_text:
            parts.append(f"[Page {index + 1}]\n{page_text}")
        else:
            parts.append(f"[Page {index + 1}]\n{_UNREADABLE_PAGE}")
    if page_count > len(extracted):
        skipped = page_count - len(extracted)
        parts.append(f"{skipped} later pages were not extracted.")
    return "\n\n".join(parts).strip(), truncated
