"""
Registers FastAPI exception handlers that render every error — domain,
request-validation, HTTP, or unexpected — through the one standard
envelope:

{
  "error": {
    "code": "...",
    "message": "...",
    "details": null
  }
}

No business-specific error handling lives here; this module only wires
the generic categories from app.core.exceptions plus FastAPI/Starlette's
own built-in exception types into that shared shape.

`details` is always passed through `jsonable_encoder` before being
handed to `JSONResponse` (which uses plain `json.dumps` and does NOT do
this on its own). This matters in practice, not just in theory: when a
Pydantic `field_validator` on a REQUEST BODY raises a plain `ValueError`
(e.g. app.schemas.user's email-format check), Pydantic v2 embeds the raw
`ValueError` instance itself inside `ctx.error` in `exc.errors()` — and
`json.dumps` cannot serialize an exception object. This bug existed
since Part 4 but was invisible until Part 10, since every prior
validation failure was on a query/path parameter (FastAPI's own
`pattern=`/`Literal` checks), never a body-level custom validator.
Discovered and fixed here via a real failing test
(test_create_user_invalid_email_rejected), not by inspection.
"""
import logging
from typing import Any, Optional

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError

logger = logging.getLogger("pinelab.errors")


def _error_response(code: str, message: str, details: Optional[Any], status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": jsonable_encoder(details)}},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return _error_response(exc.code, exc.message, exc.details, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            details=exc.errors(),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_response(
            code="HTTP_ERROR",
            message=str(exc.detail),
            details=None,
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception while processing %s %s", request.method, request.url.path)
        return _error_response(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected error occurred.",
            details=None,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
