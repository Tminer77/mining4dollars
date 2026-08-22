"""Keyset pagination clause shared by Shield listings.

Every listing here is ``(sort_at DESC, id DESC)``. Wrapping the bounds in typed
literals is what lets asyncpg send ``timestamptz`` and ``uuid`` rather than
inferring them from an untyped placeholder, which is what keeps the composite
index usable as a single range scan.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, DateTime, literal, tuple_
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped

from m4d.domain.pagination import Cursor

__all__ = ["after_cursor"]


def after_cursor(
    sort_at: Mapped[object],
    identifier: Mapped[object],
    after: Cursor,
) -> ColumnElement[bool]:
    """Return a WHERE clause that resumes strictly after ``after``."""
    return tuple_(sort_at, identifier) < tuple_(
        literal(after.occurred_at, DateTime(timezone=True)),
        literal(after.id, PgUUID(as_uuid=True)),
    )
