"""Translate driver-level integrity errors into domain errors.

Storage errors must not escape the repository layer as SQLAlchemy types; the
service layer only understands the domain vocabulary.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from m4d.domain.errors import ConflictError, ValidationError

__all__ = ["translate_integrity_error"]


def translate_integrity_error(
    exc: IntegrityError,
    *,
    unique_indexes: dict[str, str],
    check_prefix: str,
) -> Exception:
    """Map ``exc`` onto a domain error when the constraint is one we know.

    Args:
        unique_indexes: index name -> conflict message.
        check_prefix: substring that identifies this table's CHECK constraints,
            e.g. ``ck_endpoint_``.
    """
    detail = str(exc.orig)
    for index_name, message in unique_indexes.items():
        if index_name in detail:
            return ConflictError(message)
    if check_prefix in detail:
        return ValidationError("The row violates a database constraint.", detail=detail)
    return exc
