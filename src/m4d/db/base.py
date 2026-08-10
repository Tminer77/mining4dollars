"""Declarative base and metadata conventions."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

__all__ = ["Base", "metadata"]

# Without an explicit convention, PostgreSQL invents constraint names and
# Alembic autogenerate produces migrations that cannot reliably drop them later.
# Naming every constraint deterministically is what makes migrations reversible.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Base class for every ORM mapping."""

    metadata = metadata
