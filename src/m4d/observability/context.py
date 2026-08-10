"""Ambient request context.

A request identifier needs to reach every log line without being threaded
through every function signature. A :class:`~contextvars.ContextVar` gives us
that: it is set once per request by the HTTP middleware and read by the log
formatter, and it is isolated per asyncio task, so concurrent requests never
observe each other's values.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

__all__ = ["bind_request_id", "get_request_id", "reset_request_id"]

_request_id: ContextVar[str | None] = ContextVar("m4d_request_id", default=None)


def bind_request_id(request_id: str) -> Token[str | None]:
    """Bind ``request_id`` to the current context.

    Returns the token needed to restore the previous value; callers should pass
    it to :func:`reset_request_id` when the scope ends.
    """
    return _request_id.set(request_id)


def get_request_id() -> str | None:
    """Return the request id bound to the current context, if any."""
    return _request_id.get()


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the request id that was in effect before ``token`` was issued."""
    _request_id.reset(token)
