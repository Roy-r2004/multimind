"""Domain and HTTP exception hierarchy."""

from typing import Any


class AppError(Exception):
    """Base application error."""

    def __init__(self, message: str, *, code: str = "APP_ERROR", details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details


class NotFoundError(AppError):
    def __init__(self, resource: str, identifier: str | None = None) -> None:
        msg = f"{resource} not found" + (f": {identifier}" if identifier else "")
        super().__init__(msg, code="NOT_FOUND")


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(message, code="UNAUTHORIZED")


class ForbiddenError(AppError):
    def __init__(self, message: str = "Insufficient permissions") -> None:
        super().__init__(message, code="FORBIDDEN")


class ValidationError(AppError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message, code="VALIDATION_ERROR", details=details)


class ConflictError(AppError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message, code="CONFLICT", details=details)


class InternalServerError(AppError):
    def __init__(self, message: str = "Internal server error") -> None:
        super().__init__(message, code="INTERNAL_ERROR")


class TranscriptionDisabledError(AppError):
    def __init__(self, message: str = "Transcription is disabled") -> None:
        super().__init__(message, code="TRANSCRIPTION_DISABLED")


class TranscriptionModelUnavailableError(AppError):
    def __init__(self, message: str = "Transcription model is unavailable") -> None:
        super().__init__(message, code="TRANSCRIPTION_MODEL_UNAVAILABLE")


class TranscriptionBusyError(AppError):
    def __init__(self, message: str = "Transcription service is busy") -> None:
        super().__init__(message, code="TRANSCRIPTION_BUSY")


class TranscriptionTimeoutError(AppError):
    def __init__(self, message: str = "Transcription timed out") -> None:
        super().__init__(message, code="TRANSCRIPTION_TIMEOUT")


class UnsupportedAudioTypeError(AppError):
    def __init__(self, message: str = "Unsupported audio media type") -> None:
        super().__init__(message, code="UNSUPPORTED_AUDIO_TYPE")


class AudioTooLargeError(AppError):
    def __init__(self, message: str = "Audio upload exceeds transcription limit") -> None:
        super().__init__(message, code="AUDIO_TOO_LARGE")


class AudioTooLongError(ValidationError):
    def __init__(self, message: str = "Audio duration exceeds transcription limit") -> None:
        super().__init__(message)
        self.code = "AUDIO_TOO_LONG"


class InvalidAudioError(ValidationError):
    def __init__(self, message: str = "Invalid audio file", details: Any = None) -> None:
        super().__init__(message, details=details)
        self.code = "INVALID_AUDIO"


class SilentAudioError(ValidationError):
    def __init__(self, message: str = "Audio contains no meaningful speech") -> None:
        super().__init__(message)
        self.code = "SILENT_AUDIO"
