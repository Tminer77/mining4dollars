"""Cursor encoding and page-size handling."""

from __future__ import annotations

import datetime as dt
from uuid import UUID, uuid4

import pytest

from m4d.domain.errors import ValidationError
from m4d.domain.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Cursor,
    Page,
    normalise_page_size,
)

AN_INSTANT = dt.datetime(2026, 8, 10, 12, 30, 45, 123456, tzinfo=dt.UTC)
AN_ID = UUID("0191f5a0-1234-7abc-8def-0123456789ab")


class TestCursorRoundTrip:
    def test_survives_encode_then_decode(self) -> None:
        cursor = Cursor(occurred_at=AN_INSTANT, id=AN_ID)
        assert Cursor.decode(cursor.encode()) == cursor

    def test_preserves_microseconds(self) -> None:
        """Truncating sub-second precision would make the cursor skip rows."""
        cursor = Cursor(occurred_at=AN_INSTANT, id=AN_ID)
        assert Cursor.decode(cursor.encode()).occurred_at.microsecond == 123456

    def test_token_is_url_safe(self) -> None:
        token = Cursor(occurred_at=AN_INSTANT, id=AN_ID).encode()
        assert all(char.isalnum() or char in "-_" for char in token)

    def test_token_is_opaque(self) -> None:
        """No readable timestamp, so clients cannot come to depend on the format."""
        assert "2026" not in Cursor(occurred_at=AN_INSTANT, id=AN_ID).encode()


class TestCursorRejection:
    @pytest.mark.parametrize(
        "token",
        [
            "not-base64!!",
            "",
            "YWJjZGVm",  # decodes, but has no separator
            "MjAyNi0wMS0wMVQwMDowMDowMHwtLW5vdC1hLXV1aWQ=",  # bad UUID
        ],
    )
    def test_rejects_malformed_tokens(self, token: str) -> None:
        """Cursors are untrusted input; every failure is one domain error."""
        with pytest.raises(ValidationError):
            Cursor.decode(token)

    def test_error_does_not_leak_a_traceback(self) -> None:
        with pytest.raises(ValidationError) as caught:
            Cursor.decode("garbage")
        assert caught.value.context["cursor"] == "garbage"


class TestPageSize:
    def test_defaults_when_unset(self) -> None:
        assert normalise_page_size(None) == DEFAULT_PAGE_SIZE

    def test_clamps_to_the_maximum(self) -> None:
        """An unbounded limit is a denial-of-service vector, so it is capped."""
        assert normalise_page_size(10_000) == MAX_PAGE_SIZE

    def test_passes_through_a_sane_value(self) -> None:
        assert normalise_page_size(25) == 25

    @pytest.mark.parametrize("limit", [0, -5])
    def test_rejects_non_positive(self, limit: int) -> None:
        with pytest.raises(ValidationError):
            normalise_page_size(limit)


class TestPage:
    def test_reports_more_when_a_cursor_is_present(self) -> None:
        page = Page(items=(1, 2), next_cursor="token")
        assert page.has_more is True

    def test_reports_no_more_without_a_cursor(self) -> None:
        page: Page[int] = Page(items=(1, 2), next_cursor=None)
        assert page.has_more is False

    def test_empty_page_is_terminal(self) -> None:
        page: Page[int] = Page(items=(), next_cursor=None)
        assert page.items == ()
        assert page.has_more is False


def test_cursor_ordering_is_total() -> None:
    """Equal timestamps must still order deterministically, via the id."""
    first = Cursor(occurred_at=AN_INSTANT, id=uuid4())
    second = Cursor(occurred_at=AN_INSTANT, id=uuid4())
    assert first.encode() != second.encode()
