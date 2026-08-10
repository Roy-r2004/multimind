"""Chat image loading and one-shot vision analysis for council context.

Upload/storage stays in the attachment pipeline. Before council generation we run
exactly one vision-capable OpenRouter call, persist structured text into each
image attachment's ``text_excerpt``, and supply that same text to every council
model (including text-only models). Raw images are not sent to council models.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import ChatAttachment, LibraryItem, Turn
from app.services.attachment_types import (
    IMAGE_EXCERPT_STATUS,
    IMAGE_EXTENSIONS,
    image_media_type_for_extension,
    is_library_ref_relative_path,
    normalize_media_type,
)
from app.services.chat_attachment_storage import (
    UnsafeAttachmentPathError,
    resolve_attachment_path,
)
from app.services.chat_attachment_text import ATTACHMENT_TEXT_EXCERPT_MAX

logger = get_logger(__name__)

IMAGE_CONTEXT_START = "### IMAGE CONTEXT"
IMAGE_CONTEXT_END = "### END IMAGE CONTEXT"
IMAGE_ANALYSIS_FAILED_CODE = "IMAGE_ANALYSIS_FAILED"
IMAGE_ANALYSIS_FAILED_MESSAGE = (
    "Image analysis failed. The attached image(s) were preserved — send again to retry."
)

_IMAGE_CONTEXT_BLOCK_RE = re.compile(
    rf"{re.escape(IMAGE_CONTEXT_START)}.*?{re.escape(IMAGE_CONTEXT_END)}",
    re.DOTALL | re.IGNORECASE,
)

_VISION_ANALYSIS_SYSTEM = """You are MultiMind's image analyst.
Analyze the attached image(s) for downstream AI council models that cannot see pixels.

Produce a detailed, structured textual representation. Cover when present:
- overall description of what is shown
- visible text (UI labels, handwriting, captions, signs) transcribed faithfully
- screenshots / UI layout and controls
- objects and people
- tables / structured data (preserve rows/columns when readable)
- charts / graphs (axes, series, trends, approximate values)
- diagrams and relationships between elements
- anything needed to answer the user's question accurately

Do NOT invent details you cannot see. If something is unreadable, say so.
Do NOT use OCR-only dumps — interpret the image for reasoning.

Output format (plain text, no markdown fences):

IMAGE CONTEXT
Image 1 (<filename>):
Description:
...
Visible text:
...
Tables / structured data:
...
Important visual details:
...
Question-relevant observations:
...

Repeat "Image N (...):" for each attached image in order. Separate images clearly.
"""


@dataclass(frozen=True)
class VisionImage:
    """In-memory image payload for OpenRouter (never exposes filesystem paths)."""

    filename: str
    media_type: str
    data_base64: str
    attachment_id: str | None = None


class ImageAnalysisError(Exception):
    """Raised when dedicated vision preprocessing fails."""

    def __init__(self, message: str = IMAGE_ANALYSIS_FAILED_MESSAGE) -> None:
        super().__init__(message)
        self.code = IMAGE_ANALYSIS_FAILED_CODE


def attachment_is_image(attachment: ChatAttachment) -> bool:
    if (attachment.excerpt_status or "").lower() == IMAGE_EXCERPT_STATUS:
        return True
    ext = Path(attachment.filename or "").suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return True
    media = normalize_media_type(attachment.content_type)
    return media.startswith("image/")


def build_openrouter_user_content(
    user_text: str,
    images: list[VisionImage] | None,
) -> str | list[dict]:
    """Return a string (text-only) or OpenRouter multimodal content list."""
    if not images:
        return user_text
    parts: list[dict] = [{"type": "text", "text": user_text}]
    for image in images:
        parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image.media_type};base64,{image.data_base64}",
                },
            }
        )
    return parts


def strip_image_context_block(text: str | None) -> str:
    if not text:
        return ""
    return _IMAGE_CONTEXT_BLOCK_RE.sub("", text).strip()


def extract_image_context_block(text: str | None) -> str | None:
    if not text:
        return None
    match = _IMAGE_CONTEXT_BLOCK_RE.search(text)
    if not match:
        return None
    inner = match.group(0)
    inner = re.sub(
        rf"^{re.escape(IMAGE_CONTEXT_START)}\s*",
        "",
        inner,
        flags=re.IGNORECASE,
    )
    inner = re.sub(
        rf"\s*{re.escape(IMAGE_CONTEXT_END)}$",
        "",
        inner,
        flags=re.IGNORECASE,
    )
    cleaned = inner.strip()
    return cleaned or None


def merge_image_context_into_instructions(
    existing: str | None,
    image_context: str,
) -> str:
    base = strip_image_context_block(existing)
    body = image_context.strip()
    if not body.upper().startswith("IMAGE CONTEXT"):
        body = f"IMAGE CONTEXT\n{body}"
    block = f"{IMAGE_CONTEXT_START}\n{body}\n{IMAGE_CONTEXT_END}"
    if not base:
        return block
    return f"{base}\n\n{block}"


def cached_image_context_from_attachments(
    attachments: list[ChatAttachment],
) -> str | None:
    """Reuse prior analysis stored on image attachment excerpts."""
    image_rows = [row for row in attachments if attachment_is_image(row)]
    if not image_rows:
        return None
    ready = [
        row
        for row in image_rows
        if (row.excerpt_status or "").lower() == "ready"
        and (row.text_excerpt or "").strip()
    ]
    if len(ready) != len(image_rows):
        return None
    # Prefer a full combined document if any excerpt already contains all images.
    for row in ready:
        text = (row.text_excerpt or "").strip()
        if text.upper().startswith("IMAGE CONTEXT") and "Image 1" in text:
            return text
    parts: list[str] = ["IMAGE CONTEXT"]
    for index, row in enumerate(ready, start=1):
        excerpt = (row.text_excerpt or "").strip()
        if excerpt.upper().startswith("IMAGE CONTEXT"):
            parts.append(excerpt.split("\n", 1)[-1].strip() if "\n" in excerpt else excerpt)
        else:
            parts.append(f"Image {index} ({row.filename}):\n{excerpt}")
    return "\n\n".join(parts).strip() or None


async def load_vision_images_for_turn(
    db: AsyncSession,
    *,
    turn_id: str,
    org_id: str,
) -> list[VisionImage]:
    """Read stored image bytes for attachments linked to a turn."""
    rows = await _image_attachments_for_turn(db, turn_id=turn_id, org_id=org_id)
    return await _attachments_to_vision_images(db, rows)


async def _image_attachments_for_turn(
    db: AsyncSession,
    *,
    turn_id: str,
    org_id: str,
) -> list[ChatAttachment]:
    result = await db.execute(
        select(ChatAttachment)
        .where(
            ChatAttachment.turn_id == turn_id,
            ChatAttachment.org_id == org_id,
        )
        .order_by(ChatAttachment.created_at.asc(), ChatAttachment.id.asc())
    )
    return [row for row in result.scalars().all() if attachment_is_image(row)]


async def _attachments_to_vision_images(
    db: AsyncSession,
    rows: list[ChatAttachment],
) -> list[VisionImage]:
    images: list[VisionImage] = []
    for row in rows:
        data = await _read_attachment_bytes(db, row)
        if not data:
            continue
        ext = Path(row.filename or row.stored_name or "").suffix.lower()
        media = normalize_media_type(row.content_type)
        if media not in {"image/png", "image/jpeg", "image/webp"}:
            media = image_media_type_for_extension(ext)
        images.append(
            VisionImage(
                filename=row.filename,
                media_type=media,
                data_base64=base64.b64encode(data).decode("ascii"),
                attachment_id=row.id,
            )
        )
    return images


async def ensure_image_context_for_turn(
    db: AsyncSession,
    *,
    turn: Turn,
    org_id: str,
) -> str | None:
    """Return structured image context for the turn, analyzing at most once.

    Persistence:
    - Prefer an IMAGE CONTEXT block already on ``turn.custom_instructions``.
    - Else reuse ``text_excerpt`` on image attachments when all are ``ready``.
    - Else run one dedicated vision call, store the result on each image
      attachment's ``text_excerpt`` (``excerpt_status=ready``), and return it.

    Raises ``ImageAnalysisError`` when images are present but analysis cannot run.
    """
    image_rows = await _image_attachments_for_turn(db, turn_id=turn.id, org_id=org_id)
    if not image_rows:
        return None

    cached_turn = extract_image_context_block(turn.custom_instructions)
    if cached_turn:
        return cached_turn

    cached_attachments = cached_image_context_from_attachments(image_rows)
    if cached_attachments:
        return cached_attachments

    images = await _attachments_to_vision_images(db, image_rows)
    if not images:
        raise ImageAnalysisError(
            "Image analysis failed: attached image file(s) could not be read. "
            "The attachment(s) were preserved — send again to retry."
        )
    if len(images) != len(image_rows):
        raise ImageAnalysisError(
            "Image analysis failed: one or more attached images could not be read. "
            "The attachment(s) were preserved — send again to retry."
        )

    analysis = await analyze_images_once(
        images=images,
        user_message=turn.user_message,
    )
    capped = analysis[:ATTACHMENT_TEXT_EXCERPT_MAX].rstrip()
    if not capped.strip():
        raise ImageAnalysisError(IMAGE_ANALYSIS_FAILED_MESSAGE)

    for row in image_rows:
        row.text_excerpt = capped
        row.excerpt_status = "ready"
    await db.flush()
    return capped


async def analyze_images_once(
    *,
    images: list[VisionImage],
    user_message: str,
) -> str:
    """Single dedicated vision-model call for all images on the turn."""
    # Lazy imports avoid circular dependency with providers ↔ chat_vision.
    from app.llm.catalog import get_model
    from app.llm.providers import get_provider_registry

    settings = get_settings()
    catalog_id = (settings.chat_image_analysis_model or "gemini").strip()
    try:
        model = get_model(catalog_id)
    except Exception as exc:  # noqa: BLE001
        raise ImageAnalysisError(
            f"Image analysis failed: configured vision model '{catalog_id}' is unavailable."
        ) from exc

    filenames = ", ".join(image.filename for image in images)
    user_prompt = (
        f"User question:\n{user_message.strip() or '(no question text)'}\n\n"
        f"Attached image file name(s) in order: {filenames}\n\n"
        "Analyze the image(s) now and return the structured IMAGE CONTEXT."
    )

    try:
        provider = get_provider_registry().get_provider(model.provider)
        response = await provider.complete(
            system=_VISION_ANALYSIS_SYSTEM,
            user=user_prompt,
            model=model.provider_model,
            max_tokens=4096,
            temperature=0.2,
            images=images,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_image_analysis_failed", error=str(exc), model=model.provider_model)
        raise ImageAnalysisError(
            f"{IMAGE_ANALYSIS_FAILED_MESSAGE} ({exc})"
        ) from exc

    text = (response.text or "").strip()
    if not text:
        raise ImageAnalysisError(IMAGE_ANALYSIS_FAILED_MESSAGE)
    if not text.upper().lstrip().startswith("IMAGE CONTEXT"):
        text = f"IMAGE CONTEXT\n{text}"
    return text


async def _read_attachment_bytes(db: AsyncSession, row: ChatAttachment) -> bytes | None:
    try:
        if row.library_item_id or is_library_ref_relative_path(row.relative_path):
            return await _read_library_bytes(db, row)
        path = resolve_attachment_path(row.relative_path)
        if not path.is_file():
            logger.warning(
                "chat_vision_missing_file",
                attachment_id=row.id,
                filename=row.filename,
            )
            return None
        return path.read_bytes()
    except UnsafeAttachmentPathError:
        logger.warning("chat_vision_unsafe_path", attachment_id=row.id)
        return None
    except OSError as exc:
        logger.warning(
            "chat_vision_read_failed",
            attachment_id=row.id,
            error=str(exc),
        )
        return None


async def _read_library_bytes(db: AsyncSession, row: ChatAttachment) -> bytes | None:
    library_id = row.library_item_id
    if not library_id and is_library_ref_relative_path(row.relative_path):
        library_id = row.relative_path.split("/", 1)[-1]
    if not library_id:
        return None
    item = (
        await db.execute(
            select(LibraryItem).where(
                LibraryItem.id == library_id,
                LibraryItem.org_id == row.org_id,
            )
        )
    ).scalar_one_or_none()
    if item is None or not item.relative_path:
        logger.warning("chat_vision_library_missing", attachment_id=row.id)
        return None
    try:
        path = resolve_attachment_path(
            item.relative_path,
            root=get_settings().library_file_dir,
        )
    except UnsafeAttachmentPathError:
        logger.warning("chat_vision_library_unsafe_path", attachment_id=row.id)
        return None
    if not path.is_file():
        return None
    return path.read_bytes()
