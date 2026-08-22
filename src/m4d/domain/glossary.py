"""Canonical vocabulary and the interpreter that binds language to it.

An LLM that is allowed to invent its own words will eventually invent its own
reality. The glossary is the closed set of terms this system is willing to act
on. The interpreter is the function that takes an utterance and either binds
every content word to a term, or names the words it cannot bind.

Interpretation is pure: it does not read a clock, it does not write, and the
same utterance against the same glossary always produces the same bindings.
Commit is a different step; it consults the interpretation and refuses if
anything was left unbound. That split is load-bearing. Drafting may be messy.
History may not.
"""

from __future__ import annotations

import datetime as dt
import enum
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from m4d.domain.errors import ValidationError

__all__ = [
    "CORE_GLOSSARY",
    "MAX_ALIAS_LENGTH",
    "MAX_DEFINITION_LENGTH",
    "MAX_NAME_LENGTH",
    "MAX_SLUG_LENGTH",
    "MAX_UTTERANCE_LENGTH",
    "Binding",
    "GlossaryTerm",
    "Interpretation",
    "NewTerm",
    "TermStatus",
    "interpret",
    "normalise_key",
]

MAX_SLUG_LENGTH = 64
MAX_NAME_LENGTH = 128
MAX_DEFINITION_LENGTH = 2000
MAX_ALIAS_LENGTH = 64
MAX_ALIASES = 16
MAX_UTTERANCE_LENGTH = 4000

_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_WORD_PATTERN = re.compile(r"[a-z0-9]+")
_NON_KEY = re.compile(r"[^a-z0-9]+")

#: Words that carry grammar, not meaning. Dropped before matching so that
#: "commit the parent onto the tape" and "commit parent tape" bind identically.
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "her",
        "his",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "let",
        "lets",
        "may",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "onto",
        "or",
        "our",
        "over",
        "so",
        "than",
        "that",
        "the",
        "their",
        "then",
        "these",
        "this",
        "those",
        "to",
        "under",
        "via",
        "was",
        "we",
        "will",
        "with",
        "would",
        "yes",
        "you",
        "your",
    }
)

# Longest n-gram the interpreter will try to join into a slug. Three is enough
# for "linear-time-protocol" without turning the matcher into a search engine.
_MAX_NGRAM = 3


class TermStatus(enum.StrEnum):
    """Whether a term is legal to bind against."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"


def normalise_key(value: str) -> str:
    """Turn free text into a hyphenated lookup key.

    ``"Linear Time"``, ``"linear_time"`` and ``"linear-time"`` all become
    ``"linear-time"``. The interpreter and the slug validator share this so a
    term is reachable by any of the ways a person might type it.
    """
    return _NON_KEY.sub("-", value.strip().lower()).strip("-")


def _require_text(value: str, *, name: str, max_length: int) -> str:
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


def _require_slug(value: str) -> str:
    slug = normalise_key(value)
    if not slug or not _SLUG_PATTERN.fullmatch(slug):
        raise ValidationError(
            "slug must be kebab-case, starting with a letter.",
            field="slug",
            value=value,
        )
    if len(slug) > MAX_SLUG_LENGTH:
        raise ValidationError(
            f"slug must be at most {MAX_SLUG_LENGTH} characters.",
            field="slug",
            length=len(slug),
            max_length=MAX_SLUG_LENGTH,
        )
    return slug


def _require_aliases(aliases: Sequence[str]) -> tuple[str, ...]:
    if len(aliases) > MAX_ALIASES:
        raise ValidationError(
            f"a term may have at most {MAX_ALIASES} aliases.",
            field="aliases",
            count=len(aliases),
            max_length=MAX_ALIASES,
        )
    seen: set[str] = set()
    cleaned: list[str] = []
    for alias in aliases:
        key = normalise_key(_require_text(alias, name="alias", max_length=MAX_ALIAS_LENGTH))
        if not key:
            raise ValidationError("alias must not be blank.", field="alias")
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(key)
    return tuple(cleaned)


@dataclass(frozen=True, slots=True)
class NewTerm:
    """A request to add a term to the glossary."""

    slug: str
    name: str
    definition: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "slug", _require_slug(self.slug))
        object.__setattr__(
            self, "name", _require_text(self.name, name="name", max_length=MAX_NAME_LENGTH)
        )
        object.__setattr__(
            self,
            "definition",
            _require_text(self.definition, name="definition", max_length=MAX_DEFINITION_LENGTH),
        )
        object.__setattr__(self, "aliases", _require_aliases(self.aliases))
        if self.slug in self.aliases:
            raise ValidationError(
                "an alias must not duplicate the term's own slug.",
                field="aliases",
                slug=self.slug,
            )

    def materialise(self, *, now: dt.datetime, term_id: UUID | None = None) -> GlossaryTerm:
        """Give this request an identity and a creation time."""
        return GlossaryTerm(
            id=term_id or uuid4(),
            slug=self.slug,
            name=self.name,
            definition=self.definition,
            aliases=self.aliases,
            version=1,
            status=TermStatus.ACTIVE,
            created_at=now,
            superseded_by=None,
        )


@dataclass(frozen=True, slots=True)
class GlossaryTerm:
    """One canonical word the machine is allowed to act on."""

    id: UUID
    slug: str
    name: str
    definition: str
    aliases: tuple[str, ...]
    version: int
    status: TermStatus
    created_at: dt.datetime
    superseded_by: UUID | None = None

    @property
    def is_active(self) -> bool:
        """Whether the interpreter may treat this as a live binding."""
        return self.status is TermStatus.ACTIVE

    def lookup_keys(self) -> tuple[str, ...]:
        """Every lookup key that resolves to this term, slug first."""
        keys = [self.slug, normalise_key(self.name), *self.aliases]
        # Preserve order, drop empties and duplicates.
        seen: set[str] = set()
        unique: list[str] = []
        for key in keys:
            if key and key not in seen:
                seen.add(key)
                unique.append(key)
        return tuple(unique)

    def deprecate(self, *, successor_id: UUID | None = None) -> GlossaryTerm:
        """Return a copy marked deprecated, optionally pointing at its replacement."""
        return GlossaryTerm(
            id=self.id,
            slug=self.slug,
            name=self.name,
            definition=self.definition,
            aliases=self.aliases,
            version=self.version + 1,
            status=TermStatus.DEPRECATED,
            created_at=self.created_at,
            superseded_by=successor_id,
        )


@dataclass(frozen=True, slots=True)
class Binding:
    """One content span resolved to a glossary term.

    The definition is snapshotted so a later edit of the term cannot rewrite
    what this interpretation meant.
    """

    span: str
    slug: str
    definition: str
    version: int
    status: TermStatus


@dataclass(frozen=True, slots=True)
class Interpretation:
    """The glossary's reading of an utterance.

    ``is_complete`` is the gate the protocol consults. An incomplete
    interpretation is still a useful artefact — it names the unbound words —
    but it is not a license to act.
    """

    id: UUID
    utterance: str
    tokens: tuple[str, ...]
    bindings: tuple[Binding, ...]
    unbound: tuple[str, ...]
    interpreted_at: dt.datetime

    @property
    def is_complete(self) -> bool:
        """Whether every content token bound to an active term."""
        return not self.unbound and not self.deprecated_slugs and bool(self.tokens)

    @property
    def deprecated_slugs(self) -> tuple[str, ...]:
        """Slugs that bound, but to a term that is no longer live."""
        return tuple(
            binding.slug for binding in self.bindings if binding.status is TermStatus.DEPRECATED
        )

    @property
    def bound_slugs(self) -> tuple[str, ...]:
        """Canonical slugs, in the order they appeared."""
        return tuple(binding.slug for binding in self.bindings)

    def to_payload(self) -> dict[str, object]:
        """JSON-ready snapshot for persistence and the event log."""
        return {
            "id": str(self.id),
            "utterance": self.utterance,
            "tokens": list(self.tokens),
            "bindings": [
                {
                    "span": binding.span,
                    "slug": binding.slug,
                    "definition": binding.definition,
                    "version": binding.version,
                    "status": binding.status.value,
                }
                for binding in self.bindings
            ],
            "unbound": list(self.unbound),
            "interpreted_at": self.interpreted_at.isoformat(),
            "complete": self.is_complete,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Interpretation:
        """Rehydrate a snapshot written by :meth:`to_payload`."""
        raw_bindings = payload.get("bindings", [])
        if not isinstance(raw_bindings, list):
            raise ValidationError("interpretation bindings must be a list.", field="bindings")
        bindings: list[Binding] = []
        for item in raw_bindings:
            if not isinstance(item, Mapping):
                raise ValidationError("each binding must be an object.", field="bindings")
            bindings.append(
                Binding(
                    span=str(item["span"]),
                    slug=str(item["slug"]),
                    definition=str(item["definition"]),
                    version=int(item["version"]),
                    status=TermStatus(str(item["status"])),
                )
            )
        raw_tokens = payload.get("tokens", [])
        raw_unbound = payload.get("unbound", [])
        if not isinstance(raw_tokens, list) or not isinstance(raw_unbound, list):
            raise ValidationError("interpretation token lists must be lists.")
        interpreted_at = dt.datetime.fromisoformat(str(payload["interpreted_at"]))
        if interpreted_at.tzinfo is None:
            interpreted_at = interpreted_at.replace(tzinfo=dt.UTC)
        return cls(
            id=UUID(str(payload["id"])),
            utterance=str(payload["utterance"]),
            tokens=tuple(str(token) for token in raw_tokens),
            bindings=tuple(bindings),
            unbound=tuple(str(token) for token in raw_unbound),
            interpreted_at=interpreted_at.astimezone(dt.UTC),
        )


def _content_tokens(utterance: str) -> tuple[str, ...]:
    """Lowercased words with stopwords and single-character noise removed."""
    words = _WORD_PATTERN.findall(utterance.lower())
    return tuple(word for word in words if word not in STOPWORDS and len(word) > 1)


def _lexicon(terms: Sequence[GlossaryTerm]) -> dict[str, GlossaryTerm]:
    """Map every lookup key onto its term.

    First writer wins. Core terms are seeded first, so a later alias cannot
    steal ``tick`` out from under the protocol.
    """
    index: dict[str, GlossaryTerm] = {}
    for term in terms:
        for key in term.lookup_keys():
            index.setdefault(key, term)
    return index


def interpret(
    utterance: str,
    terms: Sequence[GlossaryTerm],
    *,
    now: dt.datetime,
) -> Interpretation:
    """Bind ``utterance`` against ``terms``.

    Matching is greedy and left-to-right: the longest n-gram that is a known
    key wins, then the next span is considered. Tokens that match nothing are
    collected as ``unbound``. An empty utterance after stopword stripping is
    incomplete — silence is not a command.
    """
    cleaned = _require_text(utterance, name="utterance", max_length=MAX_UTTERANCE_LENGTH)
    tokens = _content_tokens(cleaned)
    lexicon = _lexicon(terms)

    bindings: list[Binding] = []
    unbound: list[str] = []
    index = 0
    while index < len(tokens):
        matched: GlossaryTerm | None = None
        width = 0
        for n in range(min(_MAX_NGRAM, len(tokens) - index), 0, -1):
            span_tokens = tokens[index : index + n]
            key = "-".join(span_tokens)
            term = lexicon.get(key)
            if term is not None:
                matched = term
                width = n
                break
        if matched is None:
            unbound.append(tokens[index])
            index += 1
            continue
        span = "-".join(tokens[index : index + width])
        bindings.append(
            Binding(
                span=span,
                slug=matched.slug,
                definition=matched.definition,
                version=matched.version,
                status=matched.status,
            )
        )
        index += width

    return Interpretation(
        id=uuid4(),
        utterance=cleaned,
        tokens=tokens,
        bindings=tuple(bindings),
        unbound=tuple(unbound),
        interpreted_at=now,
    )


def _core(
    slug: str,
    name: str,
    definition: str,
    *aliases: str,
) -> NewTerm:
    return NewTerm(slug=slug, name=name, definition=definition, aliases=aliases)


CORE_GLOSSARY: tuple[NewTerm, ...] = (
    _core(
        "linear-time",
        "Linear Time",
        "The only legal order of events is the protocol tape. Wall clocks may "
        "skew; ticks cannot. Time here is a control, not a suggestion.",
        "ltp",
        "linear-timestamp",
        "time-protocol",
    ),
    _core(
        "tick",
        "Tick",
        "A committed, strictly increasing logical instant on the tape. The "
        "server assigns it. A client that claims a tick is refused.",
        "instant",
    ),
    _core(
        "tape",
        "Tape",
        "The append-only sequence of ticks. History that can be rewritten is "
        "not evidence. Corrections are a further tick, never an edit.",
        "timeline",
        "log",
    ),
    _core(
        "tree",
        "Tree of Claude",
        "The DAG of proposed and committed nodes. Branches may be proposed in "
        "parallel; they become real only when committed onto the tape.",
        "tree-of-claude",
        "dag",
    ),
    _core(
        "node",
        "Node",
        "One atomic job or interpreted utterance. If describing it needs the "
        "word 'and', it is two nodes.",
        "job",
    ),
    _core(
        "parent",
        "Parent",
        "A node this node cannot commit before. Edges are real data "
        "dependencies only, never aesthetic ordering.",
        "dependency",
    ),
    _core(
        "commit",
        "Commit",
        "Serialise a node onto the tape and assign the next tick. Until this "
        "happens, the node is a draft and did not occur.",
        "serialise",
        "serialize",
    ),
    _core(
        "interpret",
        "Interpret",
        "Bind an utterance to glossary terms. Unbound language cannot commit. "
        "The interpreter does not guess: it names what it cannot bind.",
        "bind",
        "glossary-interpreter",
    ),
    _core(
        "glossary",
        "Glossary",
        "The canonical vocabulary. Meaning that is not in the glossary is not "
        "meaning the machine is allowed to act on.",
        "vocabulary",
        "lexicon",
    ),
    _core(
        "guardrail",
        "Guardrail",
        "A rule that rejects action which would take the machine off the tape: "
        "out-of-order commit, unbound language, a parent not yet committed.",
        "rail",
        "constraint",
    ),
    _core(
        "utterance",
        "Utterance",
        "Raw language before interpretation. Drafts may be messy; the tape may not.",
        "prompt",
        "speech",
    ),
    _core(
        "alias",
        "Alias",
        "An informal name that maps to exactly one canonical term. Aliases "
        "cannot collide across terms.",
        "synonym",
    ),
    _core(
        "genesis",
        "Genesis",
        "Tick zero. The origin of the tape. Nothing precedes it, and every "
        "later node is a descendant of it.",
        "origin",
        "root",
    ),
    _core(
        "verify",
        "Verify",
        "An independent check of a committed node. The producer never grades "
        "itself: that bias is exactly what the check exists to catch.",
        "audit",
        "check",
    ),
    _core(
        "protocol",
        "Protocol",
        "The Linear Timestamp Protocol: glossary, tree, and tape acting as "
        "one control system for machine action.",
        "lt-protocol",
    ),
)
