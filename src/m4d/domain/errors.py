"""Domain error hierarchy.

The domain raises errors in its own vocabulary and never in HTTP terms. The API
layer is solely responsible for mapping these onto status codes, which keeps the
domain reusable from a worker, a CLI, or a test with no HTTP in sight.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ConflictError",
    "DomainError",
    "GuardrailError",
    "NotFoundError",
    "ValidationError",
]


class DomainError(Exception):
    """Base class for every expected, business-meaningful failure.

    "Expected" is the operative word: a :class:`DomainError` describes an
    outcome the system knows how to talk about. Anything else escaping to the
    API layer is a bug and is reported as an internal error.
    """

    #: Stable, machine-readable identifier. Clients may branch on this; unlike
    #: the human-readable message, it is part of the contract.
    code: str = "domain_error"

    def __init__(self, message: str, /, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        #: Structured detail safe to return to the caller and to log.
        self.context: dict[str, Any] = context


class NotFoundError(DomainError):
    """A referenced entity does not exist."""

    code = "not_found"

    def __init__(self, entity: str, identifier: Any) -> None:
        # str(), not repr(): this message is returned to callers, and repr on a
        # UUID renders as "UUID('...')", leaking Python types into the contract.
        super().__init__(
            f"{entity} '{identifier}' was not found", entity=entity, id=str(identifier)
        )


class ConflictError(DomainError):
    """The request cannot be applied against the current state."""

    code = "conflict"


class GuardrailError(ConflictError):
    """An action that would take the machine off the linear tape.

    Distinct from a :class:`ValidationError` (the input is malformed) and from
    a generic conflict (the row already exists). A guardrail violation means
    the request was understood and refused: out-of-order commit, unbound
    language, a parent that has not yet been committed, a deprecated term.
    """

    code = "guardrail_violation"


class ValidationError(DomainError):
    """Input is well-formed but violates a business rule.

    Distinct from a schema violation, which the API layer rejects before the
    domain is ever reached.
    """

    code = "validation_error"
