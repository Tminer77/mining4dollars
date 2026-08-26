"""Translate driver integrity errors into the domain vocabulary."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.exc import IntegrityError

from m4d.domain.errors import DomainError, ValidationError

__all__ = ["translate_integrity_error"]


def translate_integrity_error(
    exc: IntegrityError,
    mapping: Mapping[str, DomainError] | None = None,
) -> Exception:
    """Map a constraint name found in ``exc`` onto a domain error.

    Storage errors must not escape the repository layer as SQLAlchemy types.
    """
    detail = str(exc.orig)
    for needle, error in (mapping or {}).items():
        if needle in detail:
            return error
    if "ck_" in detail:
        return ValidationError("The row violates a database constraint.", detail=detail)
    return exc
