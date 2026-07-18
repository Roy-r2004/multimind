"""faster-whisper transcription service foundation."""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    InvalidAudioError,
    SilentAudioError,
    TranscriptionDisabledError,
    TranscriptionModelUnavailableError,
    TranscriptionTimeoutError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)
SUPPORTED_LANGUAGES = {"ar", "en", "fr"}
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None
    language_probability: float | None
    duration_seconds: float | None
    processing_seconds: float


@dataclass(frozen=True)
class ResolvedTranscriptionModel:
    model: Any
    model_name: str
    device: str
    compute_type: str


class TranscriptionService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        model_cls: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._model_cls = model_cls
        self._model: ResolvedTranscriptionModel | None = None
        self._init_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self.settings.transcription_concurrency)

    async def initialize(self) -> None:
        if not self.settings.transcription_enabled:
            raise TranscriptionDisabledError()
        await self._ensure_tmp_dir()
        await self._get_model()

    async def shutdown(self) -> None:
        self._model = None

    async def transcribe(
        self,
        file_path: Path,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        if not self.settings.transcription_enabled:
            raise TranscriptionDisabledError()
        if language is not None and language not in SUPPORTED_LANGUAGES:
            raise InvalidAudioError("Unsupported transcription language")
        self._validate_input_file(file_path)

        async with self._semaphore:
            model = await self._get_model()
            started = time.perf_counter()
            inference_task = asyncio.create_task(
                asyncio.to_thread(
                    self._run_transcription,
                    model,
                    file_path,
                    language,
                )
            )
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(inference_task),
                    timeout=self.settings.transcription_timeout_seconds,
                )
            except TimeoutError as exc:
                with suppress(Exception):
                    await inference_task
                logger.warning(
                    "transcription_timeout",
                    configured_model=self.settings.transcription_model,
                    resolved_model=model.model_name,
                    configured_device=self.settings.transcription_device,
                    resolved_device=model.device,
                    compute_type=model.compute_type,
                    failure_category="timeout",
                )
                raise TranscriptionTimeoutError() from exc
            except SilentAudioError:
                raise
            except InvalidAudioError:
                raise
            except Exception as exc:
                logger.warning(
                    "transcription_invalid_audio",
                    configured_model=self.settings.transcription_model,
                    resolved_model=model.model_name,
                    configured_device=self.settings.transcription_device,
                    resolved_device=model.device,
                    compute_type=model.compute_type,
                    failure_category="invalid_audio",
                )
                raise InvalidAudioError() from exc

            processing_seconds = time.perf_counter() - started
            if result.duration_seconds is not None and (
                result.duration_seconds > self.settings.transcription_max_duration_seconds
            ):
                raise InvalidAudioError("Audio duration exceeds transcription limit")

            final = TranscriptionResult(
                text=result.text,
                language=result.language,
                language_probability=result.language_probability,
                duration_seconds=result.duration_seconds,
                processing_seconds=processing_seconds,
            )
            logger.info(
                "transcription_completed",
                configured_model=self.settings.transcription_model,
                resolved_model=model.model_name,
                configured_device=self.settings.transcription_device,
                resolved_device=model.device,
                compute_type=model.compute_type,
                duration_seconds=final.duration_seconds,
                processing_seconds=final.processing_seconds,
                detected_language=final.language,
                success=True,
            )
            return final

    async def _get_model(self) -> ResolvedTranscriptionModel:
        if self._model is not None:
            return self._model

        async with self._init_lock:
            if self._model is not None:
                return self._model

            await self._ensure_tmp_dir()
            model = await self._load_configured_model()
            self._model = model
            return model

    async def _ensure_tmp_dir(self) -> None:
        try:
            await asyncio.to_thread(
                Path(self.settings.transcription_tmp_dir).mkdir,
                parents=True,
                exist_ok=True,
            )
        except OSError as exc:
            raise TranscriptionModelUnavailableError(
                "Transcription temporary storage is unavailable"
            ) from exc

    async def _load_configured_model(self) -> ResolvedTranscriptionModel:
        device = self.settings.transcription_device
        if device == "cpu":
            return await self._load_model(
                model_name=self.settings.transcription_cpu_model,
                device="cpu",
                compute_type=self.settings.transcription_cpu_compute_type,
            )

        try:
            return await self._load_model(
                model_name=self.settings.transcription_model,
                device="cuda",
                compute_type=self.settings.transcription_compute_type,
            )
        except Exception as exc:
            if self.settings.transcription_strict_device:
                logger.warning(
                    "transcription_cuda_initialization_failed",
                    configured_model=self.settings.transcription_model,
                    configured_device=device,
                    failure_category="model_initialization",
                )
                raise TranscriptionModelUnavailableError() from exc

            logger.warning(
                "transcription_cuda_fallback",
                configured_model=self.settings.transcription_model,
                resolved_model=self.settings.transcription_cpu_model,
                configured_device=device,
                resolved_device="cpu",
                compute_type=self.settings.transcription_cpu_compute_type,
                failure_category="cuda_unavailable",
            )
            return await self._load_model(
                model_name=self.settings.transcription_cpu_model,
                device="cpu",
                compute_type=self.settings.transcription_cpu_compute_type,
            )

    async def _load_model(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
    ) -> ResolvedTranscriptionModel:
        try:
            model = await asyncio.to_thread(
                self._instantiate_model,
                model_name,
                device,
                compute_type,
            )
        except Exception as exc:
            raise TranscriptionModelUnavailableError() from exc

        logger.info(
            "transcription_model_loaded",
            configured_model=self.settings.transcription_model,
            resolved_model=model_name,
            configured_device=self.settings.transcription_device,
            resolved_device=device,
            compute_type=compute_type,
        )
        return ResolvedTranscriptionModel(
            model=model,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
        )

    def _instantiate_model(self, model_name: str, device: str, compute_type: str) -> Any:
        model_cls = self._model_cls or self._resolve_model_class()
        return model_cls(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=self.settings.transcription_model_cache_dir,
        )

    def _resolve_model_class(self) -> Callable[..., Any]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionModelUnavailableError() from exc
        return WhisperModel

    def _run_transcription(
        self,
        resolved_model: ResolvedTranscriptionModel,
        file_path: Path,
        language: str | None,
    ) -> TranscriptionResult:
        kwargs = {
            "language": language,
            "beam_size": self.settings.transcription_beam_size,
            "vad_filter": self.settings.transcription_vad_filter,
            "temperature": 0,
            "condition_on_previous_text": False,
            "initial_prompt": self.settings.transcription_initial_prompt,
        }
        supported_kwargs = self._supported_transcribe_kwargs(
            resolved_model.model.transcribe,
            kwargs,
        )
        segments, info = resolved_model.model.transcribe(str(file_path), **supported_kwargs)
        text = self._normalize_text(" ".join(segment.text for segment in segments))
        if not text:
            raise SilentAudioError()
        return TranscriptionResult(
            text=text,
            language=getattr(info, "language", None) or language,
            language_probability=getattr(info, "language_probability", None),
            duration_seconds=getattr(info, "duration", None),
            processing_seconds=0.0,
        )

    def _supported_transcribe_kwargs(
        self,
        transcribe: Callable[..., Any],
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        signature = inspect.signature(transcribe)
        if any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        ):
            return kwargs
        return {key: value for key, value in kwargs.items() if key in signature.parameters}

    def _validate_input_file(self, file_path: Path) -> None:
        try:
            stat = file_path.stat()
        except OSError as exc:
            raise InvalidAudioError() from exc
        if not file_path.is_file() or stat.st_size <= 0:
            raise InvalidAudioError()
        if stat.st_size > self.settings.transcription_max_upload_bytes:
            raise InvalidAudioError("Audio upload exceeds transcription limit")

    def _normalize_text(self, text: str) -> str:
        return WHITESPACE_RE.sub(" ", text).strip()


transcription_service = TranscriptionService()
