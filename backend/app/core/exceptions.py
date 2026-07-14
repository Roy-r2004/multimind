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
