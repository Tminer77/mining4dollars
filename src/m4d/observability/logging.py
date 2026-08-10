"""Structured logging.

Logs are the primary operational signal, so they are treated as structured data
rather than prose. In deployed environments every record is a single line of
JSON that a log pipeline can index; locally a flatter human-readable format is
easier to scan.

Both formatters automatically attach the ambient request id, so any log emitted
while handling a request can be correlated without the call site cooperating.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from m4d.config import Settings
from m4d.observability.context import get_request_id

__all__ = ["ConsoleFormatter", "JsonFormatter", "setup_logging"]

# Attributes the stdlib puts on every LogRecord. Anything *not* in this set was
# supplied by the caller via `extra=` and is worth emitting as structured data.
_RESERVED_ATTRS: frozenset[str] = frozenset(
    vars(logging.LogRecord(name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None))
) | {"asctime", "message", "taskName"}


def _timestamp(created: float) -> str:
    """Render a record's creation time as a UTC RFC 3339 timestamp."""
    return dt.datetime.fromtimestamp(created, tz=dt.UTC).isoformat(timespec="milliseconds")


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """Return the caller-supplied ``extra`` fields on ``record``."""
    return {key: value for key, value in vars(record).items() if key not in _RESERVED_ATTRS}


class JsonFormatter(logging.Formatter):
    """Render a record as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if (request_id := get_request_id()) is not None:
            payload["request_id"] = request_id

        payload.update(_extras(record))

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            payload["stack"] = self.formatStack(record.stack_info)

        # `default=str` keeps a stray UUID or datetime in `extra` from turning a
        # log call into a crash. Losing type fidelity in a log line beats losing
        # the log line.
        return json.dumps(payload, default=str, separators=(",", ":"))


class ConsoleFormatter(logging.Formatter):
    """Render a record as a compact, human-readable line."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [
            _timestamp(record.created),
            f"{record.levelname:<8}",
            record.name,
            record.getMessage(),
        ]

        context = _extras(record)
        if (request_id := get_request_id()) is not None:
            context["request_id"] = request_id
        if context:
            parts.append(" ".join(f"{key}={value!r}" for key, value in sorted(context.items())))

        line = " | ".join(parts)

        if record.exc_info is not None:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def setup_logging(settings: Settings) -> None:
    """Install the process-wide logging configuration.

    Safe to call more than once: existing root handlers are replaced rather than
    appended to, so repeated calls (notably under a test suite) cannot produce
    duplicate output.
    """
    formatter: logging.Formatter = (
        JsonFormatter() if settings.log_format == "json" else ConsoleFormatter()
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # uvicorn installs its own handlers; strip them so its records propagate to
    # our root handler and come out in the same format as everything else.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # uvicorn.access duplicates the middleware's own access log, and SQLAlchemy
    # echo is controlled by `db_echo` rather than by the root level.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.db_echo else logging.WARNING
    )
