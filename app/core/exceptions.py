"""
Domain-level exceptions.

Intentionally a small, flat set of generic error *categories* — not a
large hierarchy. Business-specific exceptions (e.g. a future
MerchantNotFoundError) should subclass one of these once the
corresponding service actually exists, rather than being pre-declared
here speculatively.
"""
from typing import Any, Optional


class AppError(Exception):
    """Base class for all domain errors the API layer knows how to render."""

    code: str = "APP_ERROR"
    status_code: int = 500

    def __init__(self, message: str, *, details: Optional[Any] = None, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code is not None:
            self.code = code


class NotFoundError(AppError):
    """Requested resource does not exist."""

    code = "NOT_FOUND"
    status_code = 404


class ConflictError(AppError):
    """Request conflicts with the current state (e.g. a uniqueness violation)."""

    code = "CONFLICT"
    status_code = 409


class ForbiddenError(AppError):
    """Caller is authenticated but not allowed to perform this action."""

    code = "FORBIDDEN"
    status_code = 403


class UnauthorizedError(AppError):
    """Caller is not authenticated."""

    code = "UNAUTHORIZED"
    status_code = 401


class ValidationError(AppError):
    """Request failed a business-rule validation (distinct from Pydantic's
    own request-shape validation, which FastAPI handles separately)."""

    code = "VALIDATION_ERROR"
    status_code = 422
