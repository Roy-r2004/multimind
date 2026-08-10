"""Shared attachment file-type validation (chat + library uploads)."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from app.core.exceptions import InvalidAttachmentError, UnsupportedAttachmentTypeError

TEXT_EXTENSIONS = frozenset(
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
OFFICE_EXTENSIONS = frozenset({".docx", ".xlsx"})
PDF_EXTENSIONS = frozenset({".pdf"})
ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | OFFICE_EXTENSIONS | PDF_EXTENSIONS
LEGACY_OFFICE_EXTENSIONS = frozenset({".doc", ".xls", ".docm", ".xlsm"})

TEXT_CONTENT_TYPES = frozenset(
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
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_CONTENT_TYPE = "application/pdf"
GENERIC_BINARY_TYPE = "application/octet-stream"

UNSUPPORTED_TYPE_MESSAGE = (
    "Unsupported file type. Upload a text file, .docx, .xlsx, or .pdf."
)

# Pending chat attachment rows that reference a Library item use this path prefix
# under the chat attachment root. No physical chat-owned file exists for them.
LIBRARY_REF_PATH_PREFIX = "library-ref/"


def normalize_media_type(content_type: str | None) -> str:
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def validate_attachment_filename(filename: str | None) -> tuple[str, str]:
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
        raise UnsupportedAttachmentTypeError(UNSUPPORTED_TYPE_MESSAGE)
    if ext in LEGACY_OFFICE_EXTENSIONS:
        raise UnsupportedAttachmentTypeError(
            "Legacy Word/Excel formats (.doc, .xls) are not supported. "
            "Upload .docx or .xlsx instead."
        )
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedAttachmentTypeError(UNSUPPORTED_TYPE_MESSAGE)
    return basename, ext


def validate_attachment_content_type(
    content_type: str | None, filename: str, ext: str
) -> str:
    normalized = normalize_media_type(content_type)
    if not normalized:
        guessed = mimetypes.guess_type(filename)[0]
        normalized = (guessed or GENERIC_BINARY_TYPE).lower()

    if ext in TEXT_EXTENSIONS:
        if normalized in TEXT_CONTENT_TYPES or normalized == GENERIC_BINARY_TYPE:
            return normalized
        raise UnsupportedAttachmentTypeError(UNSUPPORTED_TYPE_MESSAGE)

    if ext == ".docx":
        if normalized in {DOCX_CONTENT_TYPE, GENERIC_BINARY_TYPE}:
            return normalized
        raise UnsupportedAttachmentTypeError(UNSUPPORTED_TYPE_MESSAGE)

    if ext == ".xlsx":
        if normalized in {XLSX_CONTENT_TYPE, GENERIC_BINARY_TYPE}:
            return normalized
        raise UnsupportedAttachmentTypeError(UNSUPPORTED_TYPE_MESSAGE)

    if ext == ".pdf":
        if normalized in {PDF_CONTENT_TYPE, GENERIC_BINARY_TYPE}:
            return normalized
        raise UnsupportedAttachmentTypeError(UNSUPPORTED_TYPE_MESSAGE)

    raise UnsupportedAttachmentTypeError(UNSUPPORTED_TYPE_MESSAGE)


def library_ref_relative_path(library_item_id: str) -> str:
    return f"{LIBRARY_REF_PATH_PREFIX}{library_item_id}"


def is_library_ref_relative_path(relative_path: str) -> bool:
    return (relative_path or "").startswith(LIBRARY_REF_PATH_PREFIX)
