"""Glossary persistence backed by PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from m4d.db.tables import GlossaryTermRow
from m4d.domain.errors import ConflictError, ValidationError
from m4d.domain.glossary import GlossaryTerm, TermStatus, normalise_key

__all__ = ["SqlAlchemyGlossaryRepository"]

SLUG_INDEX = "uq_glossary_term_slug"


def _translate_integrity_error(exc: IntegrityError) -> Exception:
    """Map a driver-level constraint violation onto a domain error."""
    detail = str(exc.orig)
    if SLUG_INDEX in detail or "uq_glossary_term_slug" in detail:
        return ConflictError("A glossary term with this slug already exists.")
    if "ck_glossary_term_" in detail:
        return ValidationError("The term violates a database constraint.", detail=detail)
    return exc


def _to_domain(row: GlossaryTermRow) -> GlossaryTerm:
    """Translate a persistence row into a domain entity."""
    aliases = row.aliases if isinstance(row.aliases, list) else []
    return GlossaryTerm(
        id=row.id,
        slug=row.slug,
        name=row.name,
        definition=row.definition,
        aliases=tuple(str(alias) for alias in aliases),
        version=row.version,
        status=row.status,
        created_at=row.created_at,
        superseded_by=row.superseded_by,
    )


def _to_row(term: GlossaryTerm) -> GlossaryTermRow:
    """Translate a domain entity into a persistence row."""
    return GlossaryTermRow(
        id=term.id,
        slug=term.slug,
        name=term.name,
        definition=term.definition,
        aliases=list(term.aliases),
        version=term.version,
        status=term.status,
        created_at=term.created_at,
        superseded_by=term.superseded_by,
    )


class SqlAlchemyGlossaryRepository:
    """Implements :class:`~m4d.domain.ports.GlossaryRepository` over a session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, term: GlossaryTerm) -> GlossaryTerm:
        """Stage ``term`` for insertion."""
        row = _to_row(term)
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            raise _translate_integrity_error(exc) from exc
        return _to_domain(row)

    async def save(self, term: GlossaryTerm) -> GlossaryTerm:
        """Replace the stored row for ``term.id``."""
        row = await self._session.get(GlossaryTermRow, term.id)
        if row is None:
            return await self.add(term)
        row.slug = term.slug
        row.name = term.name
        row.definition = term.definition
        row.aliases = list(term.aliases)
        row.version = term.version
        row.status = term.status
        row.superseded_by = term.superseded_by
        await self._session.flush()
        return _to_domain(row)

    async def get(self, term_id: UUID) -> GlossaryTerm | None:
        """Return the term with ``term_id``, or ``None``."""
        row = await self._session.get(GlossaryTermRow, term_id)
        return None if row is None else _to_domain(row)

    async def get_by_slug(self, slug: str) -> GlossaryTerm | None:
        """Return the term whose canonical slug is ``slug``, or ``None``."""
        statement = select(GlossaryTermRow).where(GlossaryTermRow.slug == slug)
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return None if row is None else _to_domain(row)

    async def find_by_key(self, key: str) -> GlossaryTerm | None:
        """Return the term that owns ``key`` as a slug, name-key, or alias."""
        needle = normalise_key(key)
        if not needle:
            return None
        for term in await self.list_all():
            if needle in term.lookup_keys():
                return term
        return None

    async def list_all(self) -> Sequence[GlossaryTerm]:
        """Return every term, active first, then by slug."""
        statement = select(GlossaryTermRow).order_by(GlossaryTermRow.status, GlossaryTermRow.slug)
        rows = (await self._session.execute(statement)).scalars().all()
        # Active sorts before deprecated because 'active' < 'deprecated'.
        terms = [_to_domain(row) for row in rows]
        return sorted(terms, key=lambda term: (term.status is TermStatus.DEPRECATED, term.slug))
