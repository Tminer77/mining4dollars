"""HTTP middleware: request correlation and access logging."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from m4d.observability.context import bind_request_id, reset_request_id

__all__ = ["REQUEST_ID_HEADER", "RequestContextMiddleware"]

logger = logging.getLogger("m4d.access")

REQUEST_ID_HEADER = "X-Request-ID"

# Inbound ids are echoed into every log line, so they are bounded and character
# restricted. An unbounded header would otherwise be a cheap way to bloat logs
# or smuggle newlines into them.
_MAX_REQUEST_ID_LENGTH = 128


def _sanitise(candidate: str) -> str | None:
    """Return ``candidate`` if it is safe to use as a request id."""
    cleaned = candidate.strip()
    if not cleaned or len(cleaned) > _MAX_REQUEST_ID_LENGTH:
        return None
    if not all(char.isalnum() or char in "-_." for char in cleaned):
        return None
    return cleaned


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign every request an id, log its outcome, and echo the id back.

    Accepting a caller-supplied ``X-Request-ID`` lets a trace span several
    services; generating one when absent guarantees every request is
    identifiable regardless.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        inbound = request.headers.get(REQUEST_ID_HEADER)
        request_id = (_sanitise(inbound) if inbound else None) or uuid.uuid4().hex

        token = bind_request_id(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers turn this into a response, but the access
            # log entry would otherwise be lost. Emit it, then re-raise.
            logger.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        else:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "client": request.client.host if request.client else None,
                },
            )
            return response
        finally:
            reset_request_id(token)
