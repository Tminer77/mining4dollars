"""Shared value-object helpers used across the domain.

Kept free of any specific entity so that event timestamps and mining tickers
apply the same rules: non-blank identifiers, timezone-aware UTC instants.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from m4d.domain.errors import ValidationError

__all__ = ["parse_decimal", "require_aware", "require_identifier", "require_text", "require_ticker"]

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_TICKER = re.compile(r"^[A-Z0-9]{2,10}$")


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


def require_identifier(value: str, *, name: str, max_length: int = 32) -> str:
    """A lowercase token used as an algorithm or source name."""
    cleaned = require_text(value, name=name, max_length=max_length).lower()
    if not _IDENTIFIER.match(cleaned):
        raise ValidationError(
            f"{name} must be a lowercase identifier (start with a letter, then "
            "letters, digits, or underscores).",
            field=name,
            value=cleaned,
        )
    return cleaned


def require_ticker(value: str) -> str:
    """A coin ticker: 2-10 uppercase letters or digits."""
    cleaned = value.strip().upper()
    if not _TICKER.match(cleaned):
        raise ValidationError(
            "ticker must be 2-10 uppercase letters or digits.",
            field="ticker",
            value=value,
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


def parse_decimal(value: Decimal | int | str, *, name: str) -> Decimal:
    """Parse ``value`` as a finite Decimal without going through float."""
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int):
        parsed = Decimal(value)
    else:
        try:
            parsed = Decimal(value)
        except Exception as exc:
            raise ValidationError(
                f"{name} is not a valid number.", field=name, value=value
            ) from exc
    if not parsed.is_finite():
        raise ValidationError(f"{name} must be finite.", field=name, value=str(value))
    return parsed
