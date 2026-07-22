"""Authenticated voice transcription endpoint."""

from __future__ import annotations

import os
import secrets
import time
from contextlib import suppress
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core.config import get_settings
from app.core.dependencies import AuthContext, get_auth_context
from app.core.exceptions import (
    AppError,
    AudioTooLargeError,
    AudioTooLongError,
    InternalServerError,
    InvalidAudioError,
    UnsupportedAudioTypeError,
)
from app.core.logging import get_logger
from app.schemas.api import TranscriptionResponse
from app.services.transcription_service import (
    TranscriptionService,
    transcription_service,
)

router = APIRouter()
logger = get_logger(__name__)

CHUNK_SIZE = 1024 * 1024
ALLOWED_AUDIO_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
}
TranscriptionLanguage = Literal["auto", "en", "fr", "ar"]


def get_transcription_service() -> TranscriptionService:
    return transcription_service


def normalize_media_type(content_type: str | None) -> str:
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def language_to_service_value(language: TranscriptionLanguage) -> str | None:
    return None if language == "auto" else language


async def save_upload_to_temp_file(upload: UploadFile) -> tuple[Path, int]:
    settings = get_settings()
    tmp_dir = Path(settings.transcription_tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"{secrets.token_urlsafe(24)}.audio"
    total_bytes = 0

    try:
        with tmp_path.open("xb") as out_file:
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > settings.transcription_max_upload_bytes:
                    raise AudioTooLargeError()
                out_file.write(chunk)
        if total_bytes == 0:
            raise InvalidAudioError("Audio upload is empty")
        return tmp_path, total_bytes
    except Exception:
        with suppress(OSError):
            tmp_path.unlink()
        raise


def inspect_audio_duration(file_path: Path) -> float | None:
    try:
        import av
    except ImportError as exc:
        raise InvalidAudioError() from exc

    settings = get_settings()
    try:
        with av.open(str(file_path)) as container:
            audio_streams = [stream for stream in container.streams if stream.type == "audio"]
            if not audio_streams:
                raise InvalidAudioError()

            duration_seconds = _container_duration_seconds(container, audio_streams)
            if (
                duration_seconds is not None
                and duration_seconds > settings.transcription_max_duration_seconds
            ):
                raise AudioTooLongError()

            decoded_any = False
            last_timestamp: float | None = None
            for packet in container.demux(audio_streams):
                for frame in packet.decode():
                    decoded_any = True
                    if frame.pts is not None and frame.time_base is not None:
                        last_timestamp = float(frame.pts * frame.time_base)
                    if duration_seconds is not None:
                        return duration_seconds
            if not decoded_any:
                raise InvalidAudioError()
            return last_timestamp
    except AppError:
        raise
    except Exception as exc:
        raise InvalidAudioError() from exc


def _container_duration_seconds(container, audio_streams) -> float | None:
    if container.duration:
        return float(container.duration / 1_000_000)
    durations: list[float] = []
    for stream in audio_streams:
        if stream.duration is not None and stream.time_base is not None:
            durations.append(float(stream.duration * stream.time_base))
    return max(durations) if durations else None


@router.post("", response_model=TranscriptionResponse)
async def create_transcription(
    file: UploadFile = File(...),
    language: TranscriptionLanguage = Form(default="auto"),
    auth: AuthContext = Depends(get_auth_context),
    service: TranscriptionService = Depends(get_transcription_service),
) -> TranscriptionResponse:
    settings = get_settings()
    normalized_mime = normalize_media_type(file.content_type)
    tmp_path: Path | None = None
    upload_size = 0
    started = time.perf_counter()
    try:
        if normalized_mime not in ALLOWED_AUDIO_TYPES:
            raise UnsupportedAudioTypeError()

        tmp_path, upload_size = await save_upload_to_temp_file(file)
        duration_seconds = inspect_audio_duration(tmp_path)
        result = await service.transcribe_nowait(
            tmp_path,
            language=language_to_service_value(language),
        )
        logger.info(
            "transcription_request_completed",
            org_id=auth.org_id,
            user_id=auth.user.id,
            normalized_mime=normalized_mime,
            upload_size=upload_size,
            duration_seconds=result.duration_seconds or duration_seconds,
            processing_seconds=result.processing_seconds,
            detected_language=result.language,
            success=True,
        )
        return TranscriptionResponse(
            text=result.text.strip(),
            language=result.language,
            language_probability=result.language_probability,
            duration_seconds=result.duration_seconds,
            processing_seconds=result.processing_seconds,
        )
    except AppError as exc:
        logger.warning(
            "transcription_request_failed",
            org_id=auth.org_id,
            user_id=auth.user.id,
            normalized_mime=normalized_mime,
            upload_size=upload_size or None,
            failure_category=exc.code,
        )
        raise
    except Exception as exc:
        logger.warning(
            "transcription_request_unexpected_error",
            org_id=auth.org_id,
            user_id=auth.user.id,
            normalized_mime=normalized_mime,
            upload_size=upload_size or None,
            failure_category="unexpected",
        )
        raise InternalServerError("Transcription failed") from exc
    finally:
        await file.close()
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
        elapsed = time.perf_counter() - started
        if elapsed > settings.transcription_timeout_seconds:
            logger.warning(
                "transcription_request_cleanup_after_timeout",
                org_id=auth.org_id,
                user_id=auth.user.id,
                normalized_mime=normalized_mime,
                processing_seconds=elapsed,
            )
