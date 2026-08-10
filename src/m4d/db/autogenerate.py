"""Autogenerate policy shared by Alembic and the drift test.

Lives in the package rather than in ``migrations/env.py`` because ``env.py``
runs migrations at import time and therefore cannot be imported. Keeping the
policy here means the revision generator and the test that guards against drift
apply exactly the same rules, instead of drifting apart themselves.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint

__all__ = ["include_object"]


def include_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """Decide whether autogenerate should consider a reflected object.

    CHECK constraints are excluded. SQLAlchemy emits the CHECK backing a
    non-native ``Enum`` column at DDL time, so it exists in the database but
    never appears in the metadata. Autogenerate would therefore propose
    dropping it on every single run, and a reviewer who applied that suggestion
    would silently remove the guard on the column's allowed values.

    CHECK constraints are written by hand in revisions instead.
    """
    return not isinstance(obj, CheckConstraint)
