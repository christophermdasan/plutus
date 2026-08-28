"""Maps domain exceptions to HTTP responses in one place.

Route handlers raise (or let services raise) domain errors and never think
about status codes; this decides how each kind surfaces. Adding a new error
type means one line here rather than a try/except in every route.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.exceptions import (
    AppError,
    AuthenticationError,
    ConflictError,
    FilingNotReadyError,
    IngestionError,
    LLMError,
    LLMNotConfiguredError,
    LLMRateLimitedError,
    NotFoundError,
    PermissionError_,
    ValidationError,
)

logger = logging.getLogger(__name__)

_STATUS_BY_TYPE: list[tuple[type[AppError], int]] = [
    (FilingNotReadyError, 409),
    (NotFoundError, 404),
    (ConflictError, 409),
    (ValidationError, 422),
    (AuthenticationError, 401),
    (PermissionError_, 403),
    (LLMNotConfiguredError, 503),
    # 429 so the client can tell 'out of quota' from 'provider broken'
    # and show a specific, actionable message.
    (LLMRateLimitedError, 429),
    (LLMError, 502),
    (IngestionError, 422),
]


def _status_for(error: AppError) -> int:
    for error_type, status in _STATUS_BY_TYPE:
        if isinstance(error, error_type):
            return status
    return 500


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        status = _status_for(exc)
        if status >= 500:
            logger.exception("Unhandled application error", exc_info=exc)
        return JSONResponse(
            status_code=status,
            content={"detail": exc.message, "hint": exc.detail},
        )
