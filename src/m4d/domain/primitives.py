"""Shared validation for identifying strings and timestamps.

Kept here so each entity module does not reimplement the same two checks, which
is how "must be timezone-aware" quietly forks into three slightly different
errors. Callers still pass the field name, so the message names the offending
input rather than a generic "value".
"""

from __future__ import annotations

import datetime as dt

from m4d.domain.errors import ValidationError

__all__ = ["require_aware", "require_text", "require_unit_interval"]


def require_text(value: str, *, name: str, max_length: int) -> str:
    """Validate and normalise a short identifying string."""
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError(f"{name} must not be blank.", field=name)
    if len(cleaned) > max_length:
        raise ValidationError(
            f"{name} must be at most {max_length} characters.",
            field=name,
            length=len(cleaned),
            max_length=max_length,
        )
    return cleaned


def require_aware(value: dt.datetime, *, field: str) -> dt.datetime:
    """Reject naive datetimes and normalise to UTC.

    Naive timestamps are the classic source of off-by-hours bugs once a second
    region or a daylight-saving boundary is involved. The domain only ever holds
    timezone-aware UTC values.
    """
    if value.tzinfo is None:
        raise ValidationError(
            "Timestamps must include a timezone offset.",
            field=field,
            value=value.isoformat(),
        )
    return value.astimezone(dt.UTC)


def require_unit_interval(value: float, *, name: str) -> float:
    """Require ``value`` to lie in ``[0, 1]`` inclusive."""
    if not 0.0 <= value <= 1.0:
        raise ValidationError(
            f"{name} must be between 0 and 1 inclusive.",
            field=name,
            value=value,
        )
    return value
