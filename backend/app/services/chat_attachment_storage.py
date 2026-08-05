"""Safe filesystem helpers for chat attachment storage."""

from __future__ import annotations

import secrets
from contextlib import suppress
from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.exceptions import AttachmentTooLargeError, InvalidAttachmentError
from app.core.logging import get_logger

logger = get_logger(__name__)

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB


class UnsafeAttachmentPathError(ValueError):
    """Raised when a stored relative_path would escape the attachment root."""


def attachment_root(configured: str | Path | None = None) -> Path:
    root = Path(configured if configured is not None else get_settings().chat_attachment_dir)
    return root.resolve()


def resolve_attachment_path(
    relative_path: str,
    *,
    root: str | Path | None = None,
) -> Path:
    """Resolve a stored relative_path under the attachment root.

    Rejects empty paths, absolute paths, and any ``..`` traversal.
    """
    root_path = attachment_root(root)
    raw = (relative_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith(("/", "~")):
        raise UnsafeAttachmentPathError("Invalid attachment path")

    parts = tuple(part for part in raw.split("/") if part not in ("", "."))
    if not parts or any(part == ".." for part in parts):
        raise UnsafeAttachmentPathError("Invalid attachment path")

    # Windows drive / UNC style absolute segments
    if Path(raw).is_absolute() or (parts and ":" in parts[0]):
        raise UnsafeAttachmentPathError("Invalid attachment path")

    candidate = root_path.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root_path):
        raise UnsafeAttachmentPathError("Attachment path escapes storage root")
    return candidate


def attachment_tmp_dir(*, root: str | Path | None = None) -> Path:
    """Temporary upload directory under the attachment root."""
    root_path = attachment_root(root)
    tmp_dir = (root_path / ".tmp").resolve()
    if not tmp_dir.is_relative_to(root_path):
        raise UnsafeAttachmentPathError("Invalid attachment temp directory")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir


async def stream_upload_to_temp_file(
    upload: UploadFile,
    *,
    max_bytes: int,
    extension: str = "",
    root: str | Path | None = None,
) -> tuple[Path, int]:
    """Stream an upload to a unique temp file under the attachment root.

    Aborts with AttachmentTooLargeError when the size limit is exceeded and
    removes any partial temp file. Never buffers the full upload in memory.

    ``extension`` is included in the temp filename so Office parsers that key
    off suffixes (``.docx`` / ``.xlsx``) still work before the final rename.
    """
    tmp_dir = attachment_tmp_dir(root=root)
    suffix = extension.lower() if extension.startswith(".") else ""
    tmp_path = tmp_dir / f"{secrets.token_urlsafe(24)}.upload{suffix}"
    total_bytes = 0

    try:
        with tmp_path.open("xb") as out_file:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    max_mb = max(1, max_bytes // (1024 * 1024))
                    logger.warning(
                        "chat_attachment_upload_too_large",
                        size_bytes=total_bytes,
                        max_bytes=max_bytes,
                        filename=(upload.filename or "")[:255] or None,
                    )
                    raise AttachmentTooLargeError(
                        f"File exceeds maximum size of {max_mb} MB."
                    )
                out_file.write(chunk)
        if total_bytes == 0:
            raise InvalidAttachmentError("Attachment is empty")
        return tmp_path, total_bytes
    except Exception:
        with suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def promote_temp_attachment_file(tmp_path: Path, dest_path: Path) -> None:
    """Move a validated temp upload into its final storage path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp_path.replace(dest_path)
    except OSError as exc:
        logger.warning(
            "chat_attachment_promote_failed",
            exc_info=True,
        )
        raise InvalidAttachmentError("Could not store attachment") from exc


def cleanup_path(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("chat_attachment_cleanup_failed", exc_info=True)


def safe_delete_attachment_file(
    relative_path: str,
    *,
    root: str | Path | None = None,
) -> bool:
    """Delete an attachment file under the root.

    Missing files are treated as success. Suspicious paths are never deleted;
    the error is logged and re-raised. OS errors on a valid path are logged and
    return False (caller may still treat DB cleanup as successful).
    """
    try:
        path = resolve_attachment_path(relative_path, root=root)
    except UnsafeAttachmentPathError:
        logger.error(
            "chat_attachment_unsafe_path_skipped",
            relative_path=relative_path,
        )
        raise

    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        logger.warning(
            "chat_attachment_file_delete_failed",
            relative_path=relative_path,
            exc_info=True,
        )
        return False


def safe_delete_attachment_files(
    relative_paths: list[str],
    *,
    root: str | Path | None = None,
) -> None:
    """Best-effort cleanup for many attachments; never raises for missing files."""
    for relative_path in relative_paths:
        try:
            safe_delete_attachment_file(relative_path, root=root)
        except UnsafeAttachmentPathError:
            continue
        except Exception:
            logger.warning(
                "chat_attachment_cleanup_unexpected_error",
                relative_path=relative_path,
                exc_info=True,
            )
