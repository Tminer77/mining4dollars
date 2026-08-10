# 0001 — Layered architecture with ports and adapters

**Status:** Accepted · 2026-08-10

## Context

This repository is the base a larger platform will be built on. The specific
domain entities are not yet settled, so the structure has to absorb entities and
subsystems that do not exist yet without being rewritten.

The failure mode to avoid is the one most services drift into: business rules
spread across route handlers and ORM models, so that nothing can be tested
without a live database and every storage change ripples into logic.

## Decision

Four layers, with dependencies pointing strictly inward.

- `domain/` — entities, value objects, errors, and ports. Imports nothing from
  the rest of the application, and no third-party framework.
- `services/` — use cases. Depend on ports only. Own the transaction boundary.
- `db/` — SQLAlchemy adapters implementing the domain's ports.
- `api/` — HTTP delivery. The only layer aware of status codes.

Ports are `typing.Protocol`, satisfied structurally, so no adapter inherits from
the domain.

## Consequences

Business rules are unit-testable in microseconds against in-memory fakes
(`tests/unit/fakes.py`). Storage and transport are swappable without touching
logic. New contributors have an unambiguous answer to "where does this go?".

The cost is indirection: recording an event touches a domain entity, a port, a
repository, and a service. For a CRUD endpoint this is more ceremony than a
route that calls the ORM directly, and that cost is paid on every feature
whether or not the feature has interesting rules.

The layering is checkable rather than merely aspirational:

```bash
grep -rE "^(from|import) (fastapi|sqlalchemy)" src/m4d/domain/   # must be empty
```

## Alternatives considered

**Framework-native (routes call the ORM).** Much less code, and genuinely the
right answer for a service that is a thin shell over a database. Rejected
because the rules here — idempotency, time-window semantics, severity ordering —
are exactly the things that become untestable when fused to a session.

**Full hexagonal with a separate application layer and DTOs at every boundary.**
More rigorous, and better at very large scale. Rejected as disproportionate:
another mapping layer for no benefit a four-layer split does not already deliver.

**Single module.** Fastest to start, and fine for a service that will stay
small. Rejected because this one explicitly will not.
