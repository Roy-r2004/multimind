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
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | OFFICE_EXTENSIONS | PDF_EXTENSIONS | IMAGE_EXTENSIONS
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
IMAGE_CONTENT_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/webp",
    }
)
GENERIC_BINARY_TYPE = "application/octet-stream"

UNSUPPORTED_TYPE_MESSAGE = (
    "Unsupported file type. Upload a text file, .docx, .xlsx, .pdf, or image "
    "(.png, .jpg, .jpeg, .webp)."
)

# Pending chat attachment rows that reference a Library item use this path prefix
# under the chat attachment root. No physical chat-owned file exists for them.
LIBRARY_REF_PATH_PREFIX = "library-ref/"

IMAGE_EXCERPT_STATUS = "image"

# Extension → canonical OpenRouter/media type for data URLs.
IMAGE_MEDIA_TYPE_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def is_image_extension(ext: str) -> bool:
    return ext.lower() in IMAGE_EXTENSIONS


def image_media_type_for_extension(ext: str) -> str:
    return IMAGE_MEDIA_TYPE_BY_EXT.get(ext.lower(), "application/octet-stream")


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

    if ext in IMAGE_EXTENSIONS:
        if normalized in IMAGE_CONTENT_TYPES or normalized == GENERIC_BINARY_TYPE:
            # Canonicalize jpg → jpeg for data URLs / OpenRouter.
            if normalized in {"image/jpg", GENERIC_BINARY_TYPE}:
                return image_media_type_for_extension(ext)
            return normalized
        raise UnsupportedAttachmentTypeError(UNSUPPORTED_TYPE_MESSAGE)

    raise UnsupportedAttachmentTypeError(UNSUPPORTED_TYPE_MESSAGE)


def validate_image_magic_bytes(content: bytes, ext: str) -> None:
    """Reject files whose magic bytes do not match the claimed image extension."""
    ext = ext.lower()
    if ext not in IMAGE_EXTENSIONS:
        return
    if len(content) < 12:
        raise InvalidAttachmentError("Image file is empty or truncated")

    if ext == ".png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise InvalidAttachmentError("File content does not match a PNG image")
        return
    if ext in {".jpg", ".jpeg"}:
        if not content.startswith(b"\xff\xd8\xff"):
            raise InvalidAttachmentError("File content does not match a JPEG image")
        return
    if ext == ".webp":
        if content[:4] != b"RIFF" or content[8:12] != b"WEBP":
            raise InvalidAttachmentError("File content does not match a WebP image")
        return


def library_ref_relative_path(library_item_id: str) -> str:
    return f"{LIBRARY_REF_PATH_PREFIX}{library_item_id}"


def is_library_ref_relative_path(relative_path: str) -> bool:
    return (relative_path or "").startswith(LIBRARY_REF_PATH_PREFIX)
