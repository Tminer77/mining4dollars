"""Log formatting and request correlation."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from m4d.observability.context import bind_request_id, get_request_id, reset_request_id
from m4d.observability.logging import ConsoleFormatter, JsonFormatter


def make_record(message: str = "hello", **extra: Any) -> logging.LogRecord:
    """Build a log record carrying ``extra`` fields."""
    record = logging.LogRecord(
        name="m4d.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@pytest.fixture
def request_id() -> Any:
    """Bind a request id for the duration of a test."""
    token = bind_request_id("req-abc-123")
    yield "req-abc-123"
    reset_request_id(token)


class TestJsonFormatter:
    def test_emits_a_single_line_of_valid_json(self) -> None:
        output = JsonFormatter().format(make_record())
        assert "\n" not in output
        assert json.loads(output)["message"] == "hello"

    def test_includes_the_standard_fields(self) -> None:
        payload = json.loads(JsonFormatter().format(make_record()))
        assert {"timestamp", "level", "logger", "message"} <= payload.keys()
        assert payload["level"] == "INFO"
        assert payload["logger"] == "m4d.test"

    def test_timestamp_is_utc(self) -> None:
        payload = json.loads(JsonFormatter().format(make_record()))
        assert payload["timestamp"].endswith("+00:00")

    def test_promotes_extras_to_top_level_fields(self) -> None:
        """Structured fields are the point; they must not be buried in the text."""
        payload = json.loads(JsonFormatter().format(make_record(event_id="e1", duration_ms=12.5)))
        assert payload["event_id"] == "e1"
        assert payload["duration_ms"] == 12.5

    def test_omits_request_id_when_unbound(self) -> None:
        assert "request_id" not in json.loads(JsonFormatter().format(make_record()))

    def test_attaches_the_bound_request_id(self, request_id: str) -> None:
        payload = json.loads(JsonFormatter().format(make_record()))
        assert payload["request_id"] == request_id

    def test_survives_a_non_serialisable_extra(self) -> None:
        """Losing type fidelity in a log line beats losing the log line."""

        class Opaque:
            def __repr__(self) -> str:
                return "<opaque>"

        payload = json.loads(JsonFormatter().format(make_record(thing=Opaque())))
        assert payload["thing"] == "<opaque>"

    def test_includes_an_exception_traceback(self) -> None:
        try:
            raise ValueError("kaboom")
        except ValueError:
            record = logging.LogRecord(
                name="m4d.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=(),
                exc_info=__import__("sys").exc_info(),
            )
        payload = json.loads(JsonFormatter().format(record))
        assert "ValueError: kaboom" in payload["exception"]

    def test_interpolates_message_arguments(self) -> None:
        record = logging.LogRecord(
            name="m4d.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="counted %d items",
            args=(7,),
            exc_info=None,
        )
        assert json.loads(JsonFormatter().format(record))["message"] == "counted 7 items"


class TestConsoleFormatter:
    def test_includes_the_message_and_level(self) -> None:
        output = ConsoleFormatter().format(make_record())
        assert "hello" in output
        assert "INFO" in output

    def test_renders_extras(self) -> None:
        assert "event_id='e1'" in ConsoleFormatter().format(make_record(event_id="e1"))

    def test_renders_the_request_id(self, request_id: str) -> None:
        assert request_id in ConsoleFormatter().format(make_record())


class TestRequestContext:
    def test_defaults_to_none(self) -> None:
        assert get_request_id() is None

    def test_binding_then_resetting_restores_the_previous_value(self) -> None:
        token = bind_request_id("outer")
        assert get_request_id() == "outer"
        reset_request_id(token)
        assert get_request_id() is None

    def test_nesting_restores_the_outer_value(self) -> None:
        outer = bind_request_id("outer")
        inner = bind_request_id("inner")
        assert get_request_id() == "inner"
        reset_request_id(inner)
        assert get_request_id() == "outer"
        reset_request_id(outer)
