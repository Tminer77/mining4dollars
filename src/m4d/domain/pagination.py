"""Keyset (cursor) pagination primitives.

Offset pagination degrades badly on the append-heavy tables this platform is
built around: ``OFFSET n`` makes the database walk and discard ``n`` rows, and a
concurrent insert shifts every subsequent page, so clients silently skip or
repeat records.

Keyset pagination instead carries the sort key of the last row seen and resumes
strictly after it. Cost is constant per page and the sequence is stable under
concurrent writes.

The cursor is opaque by construction: clients receive base64 text and must treat
it as a token, which leaves us free to change the sort key later without
breaking them.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from m4d.domain.errors import ValidationError

__all__ = ["DEFAULT_PAGE_SIZE", "MAX_PAGE_SIZE", "Cursor", "Page", "take_page"]

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500

_SEPARATOR = "|"


@dataclass(frozen=True, slots=True)
class Cursor:
    """A position in a result set ordered by ``(occurred_at DESC, id DESC)``.

    ``id`` is the tiebreaker. Timestamps collide constantly under bulk ingest,
    and without a unique tiebreaker the ordering is not total, which makes rows
    at a page boundary skippable.
    """

    occurred_at: dt.datetime
    id: UUID

    def encode(self) -> str:
        """Serialise to an opaque, URL-safe token."""
        raw = f"{self.occurred_at.isoformat()}{_SEPARATOR}{self.id}"
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> Cursor:
        """Parse a token produced by :meth:`encode`.

        Raises:
            ValidationError: if the token is not one we issued. Cursors come
                from untrusted input, so every failure mode collapses into one
                domain error rather than leaking a codec traceback.
        """
        try:
            padding = "=" * (-len(token) % 4)
            raw = base64.urlsafe_b64decode(token + padding).decode()
            timestamp, _, identifier = raw.partition(_SEPARATOR)
            if not identifier:
                raise ValueError("cursor is missing its tiebreaker")
            return cls(occurred_at=dt.datetime.fromisoformat(timestamp), id=UUID(identifier))
        except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
            raise ValidationError("The supplied cursor is not valid.", cursor=token) from exc


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of results plus the token that continues the sequence."""

    items: tuple[T, ...]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        """Whether a further page exists."""
        return self.next_cursor is not None


def normalise_page_size(requested: int | None) -> int:
    """Clamp a caller-supplied page size into the supported range."""
    if requested is None:
        return DEFAULT_PAGE_SIZE
    if requested < 1:
        raise ValidationError("Page size must be at least 1.", limit=requested)
    return min(requested, MAX_PAGE_SIZE)


def take_page[T](
    rows: Sequence[T],
    page_size: int,
    *,
    position: Callable[[T], Cursor],
) -> Page[T]:
    """Cut an over-fetched result into a page and the cursor that continues it.

    Callers fetch ``page_size + 1`` rows. The extra row, if present, proves a
    further page exists without a ``COUNT(*)``.
    """
    items = tuple(rows[:page_size])
    next_cursor = position(items[-1]).encode() if len(rows) > page_size and items else None
    return Page(items=items, next_cursor=next_cursor)
