"""Error responses in RFC 9457 Problem Details form.

Every failure, from a schema rejection to an unhandled crash, leaves this
service as the same JSON shape with the ``application/problem+json`` media type.
One shape means a client writes one error path instead of guessing per endpoint.

Each response carries the ``request_id``, which is the single most useful thing
a caller can quote in a bug report: it ties their failure to our logs exactly.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from m4d.config import Settings
from m4d.domain.errors import ConflictError, DomainError, NotFoundError, ValidationError
from m4d.observability.context import get_request_id

__all__ = ["PROBLEM_MEDIA_TYPE", "install_error_handlers", "problem_response"]

logger = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"

#: Where a domain error lands in HTTP. Kept as data so the mapping is auditable
#: in one place rather than scattered across `raise HTTPException` calls.
_STATUS_BY_ERROR: tuple[tuple[type[DomainError], int], ...] = (
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (ConflictError, status.HTTP_409_CONFLICT),
    (ValidationError, status.HTTP_422_UNPROCESSABLE_CONTENT),
)

_GENERIC_INTERNAL_DETAIL = (
    "The server encountered an unexpected condition. The request id can be used "
    "to correlate this failure with server logs."
)


def _status_for(error: DomainError) -> int:
    """Return the HTTP status that represents ``error``."""
    for error_type, http_status in _STATUS_BY_ERROR:
        if isinstance(error, error_type):
            return http_status
    return status.HTTP_400_BAD_REQUEST


def problem_response(
    *,
    status_code: int,
    title: str,
    detail: str,
    code: str,
    instance: str | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build a Problem Details response."""
    body: dict[str, Any] = {
        # "about:blank" is the RFC's signal that the status code alone
        # describes the semantics; `code` carries our finer-grained meaning.
        "type": "about:blank",
        "title": title,
        "status": status_code,
        "detail": detail,
        "code": code,
    }
    if instance is not None:
        body["instance"] = instance
    if (request_id := get_request_id()) is not None:
        body["request_id"] = request_id
    if extra:
        body.update(extra)

    return JSONResponse(status_code=status_code, content=body, media_type=PROBLEM_MEDIA_TYPE)


def install_error_handlers(app: FastAPI, settings: Settings) -> None:
    """Register the application-wide exception handlers on ``app``."""

    @app.exception_handler(DomainError)
    async def _handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, DomainError)  # noqa: S101 - narrowed by the registration
        status_code = _status_for(exc)
        logger.info(
            "domain error",
            extra={"code": exc.code, "status": status_code, "path": request.url.path},
        )
        return problem_response(
            status_code=status_code,
            title=exc.code.replace("_", " ").title(),
            detail=exc.message,
            code=exc.code,
            instance=request.url.path,
            extra={"context": exc.context} if exc.context else None,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)  # noqa: S101
        # Pydantic's raw errors can embed the offending input, which may be
        # large or sensitive. Project onto just location, message, and type.
        errors = [
            {
                "location": list(error.get("loc", ())),
                "message": error.get("msg", ""),
                "type": error.get("type", ""),
            }
            for error in exc.errors()
        ]
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            title="Validation Error",
            detail="The request body or parameters failed validation.",
            code="request_validation_error",
            instance=request.url.path,
            extra={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)  # noqa: S101
        return problem_response(
            status_code=exc.status_code,
            title=str(exc.detail),
            detail=str(exc.detail),
            code=f"http_{exc.status_code}",
            instance=request.url.path,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Anything reaching here is a bug. Log it with a traceback, and return
        # internals to the caller only outside production-like environments.
        logger.exception("unhandled exception", extra={"path": request.url.path})
        detail = (
            f"{type(exc).__name__}: {exc}"
            if not settings.environment.is_production_like
            else _GENERIC_INTERNAL_DETAIL
        )
        return problem_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal Server Error",
            detail=detail,
            code="internal_error",
            instance=request.url.path,
        )
