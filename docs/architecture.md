# Architecture

This describes how the system is put together and, more usefully, why. Decisions
with real alternatives are recorded individually in [`adr/`](adr/).

## The shape

Four layers. Dependencies point strictly inward.

```
        ┌──────────────────────────────────────────┐
HTTP ──▶│  api/        routes, schemas, errors     │
        │  ipad/       home-screen PWA console     │
        ├──────────────────────────────────────────┤
        │  services/   use cases, transactions     │
        ├──────────────────────────────────────────┤
        │  domain/     entities, rules, ports      │  ◀── depends on nothing
        ├──────────────────────────────────────────┤
        │  db/         adapters implementing ports │
        └──────────────────────────────────────────┘
                              │
                              ▼
                        PostgreSQL
```

`db/` sits at the bottom of the drawing but is not at the bottom of the
dependency graph. It *depends on* `domain/`, implementing the interfaces the
domain declares. The domain has no idea SQLAlchemy exists.

Verifiable in one command:

```bash
grep -rE "^(from|import) (fastapi|sqlalchemy)" src/m4d/domain/   # returns nothing
```

## What each layer is for

### `domain/`

Entities, value objects, errors, and ports. Pure Python: no framework, no ORM,
no settings, no clock, no I/O.

The constraint pays for itself twice. Business rules are testable in
microseconds without a database, and infrastructure choices cannot leak into
them. When `EventFilter` rejects an inverted time window, that rule lives in one
place and is enforced whether the caller arrived over HTTP, from a worker, or
from a test.

Ports are `typing.Protocol` definitions, so implementations satisfy them
structurally. No adapter imports the domain in order to inherit from it.

### `services/`

One use case per method, and the transaction boundary. A service composes domain
objects and ports; it contains no SQL and no HTTP.

Services receive a `uow_factory` and a `Clock` rather than reaching for a global
session or `datetime.now()`. That is what makes `EventService` testable against
dictionaries — see `tests/unit/fakes.py`, which is short precisely because the
ports are well drawn.

### `db/`

SQLAlchemy mappings, the engine, repositories, and the unit of work.

Repositories translate between rows and domain entities and never commit; the
unit of work owns the transaction so that a service can compose several
repository calls into one atomic change. Driver errors are translated into
domain errors at this boundary — an `IntegrityError` never escapes into a
service.

### `api/`

Routes, wire schemas, middleware, and error rendering. The only layer that knows
about status codes.

Wire schemas are separate from domain entities on purpose. If routes serialised
domain objects directly, every internal rename would silently become a breaking
API change and every new internal field would be published by default.

The iPad console in `ipad/` is another delivery surface on the same process: a
standalone web app at `/` that calls the public HTTP API. It does not import
the domain. See [ADR-0009](adr/0009-ipad-pwa-console.md).

## The request lifecycle

A `POST /v1/events` in order:

1. **`RequestContextMiddleware`** assigns a request id — the caller's
   `X-Request-ID` if it is sane, otherwise a fresh one — and binds it to a
   `ContextVar` so every subsequent log line carries it without being passed one.
2. **FastAPI** validates the body against `EventCreateRequest`. A failure here
   never reaches the service; it becomes a 422 problem document.
3. **`EventCreateRequest.to_domain()`** produces a `NewEvent`, whose
   `__post_init__` applies the domain's own rules — non-blank fields,
   timezone-aware timestamps.
4. **`EventService.record()`** opens a unit of work, checks the idempotency key,
   inserts, and commits.
5. **`SqlAlchemyEventRepository.add()`** inserts inside a SAVEPOINT so a unique
   violation can be recovered from rather than poisoning the transaction.
6. **The route** returns 201, or 200 if the write was a replay.
7. **The middleware** logs method, path, status, and duration, and echoes the
   request id on the response.

If any step raises, the handlers in `api/errors.py` render an RFC 9457 problem
document carrying the same request id.

## Decisions worth knowing about

### Async end to end

The workload is I/O-bound: waiting on PostgreSQL and on HTTP clients. Async
suits it, but only if it is unbroken — one synchronous database driver blocks
the event loop and stalls every concurrent request. Rather than rely on
vigilance, `Settings` rejects any DSN that is not `postgresql+asyncpg` at
startup. See [ADR-0002](adr/0002-async-end-to-end.md).

### Keyset pagination

Offset pagination degrades on append-heavy tables — `OFFSET n` makes the
database walk and discard `n` rows — and is incorrect under concurrent writes,
because an insert shifts every later page and clients silently skip records.
Cursors encode `(occurred_at, id)`; the id is the tiebreaker that makes the
ordering total. See [ADR-0004](adr/0004-keyset-pagination.md).

### Idempotent ingest

Producers retry. `POST /v1/events` accepts an `idempotency_key`, and a replay
returns the original event with 200.

The pre-check that looks for an existing key is an optimisation, not the
mechanism. Correctness rests on a partial unique index: when two concurrent
requests carry the same key, one insert loses, and the service recovers by
reading the winner's row. The index is partial so that the many events without
a key never collide with one another. See
[ADR-0005](adr/0005-idempotent-ingest.md).

### Liveness and readiness are different questions

`/healthz` touches nothing and answers "is this process working?" — a failure
should restart it. `/readyz` checks the database and answers "can it serve
traffic?" — a failure should remove it from the load balancer and leave it
running.

Conflating them is actively harmful: a thirty-second database blip would restart
every replica simultaneously and turn a recoverable incident into a cold-start
stampede. See [ADR-0006](adr/0006-liveness-vs-readiness.md).

### Errors are one shape

Every failure is an RFC 9457 problem document with `application/problem+json`,
so a client writes one error path. Clients branch on `code`, which is stable;
`detail` is prose and may be reworded. Internal exception text is included
outside production and suppressed within it. See
[ADR-0003](adr/0003-problem-details-errors.md).

## Testing strategy

`tests/unit/` is pure and runs in well under a second. `tests/integration/` runs
against real PostgreSQL — never SQLite, which models neither JSONB, nor partial
unique indexes, nor row-value comparison. A suite that passed on SQLite would be
evidence about a database we do not deploy.

Integration tests build the schema by running the Alembic migrations, so the
migration path is covered on every run rather than only the models it is
supposed to produce. `test_models_and_migrations_agree` asserts that autogenerate
finds no structural difference, which catches a model edited without a matching
revision. CI additionally proves the downgrade path, because an unreversible
migration is otherwise discovered during an incident.

Integration tests skip, rather than fail, when no database is configured, so the
suite still runs offline.

## Extending it

Adding a capability follows the path the event slice already demonstrates:
domain entity and port, then table, repository, and migration, then service,
then wire schemas and routes, with tests at each level. The README has the
step-by-step version.

Two things to keep true:

- Nothing under `domain/` imports a framework.
- Every schema change arrives as a migration, reviewed as SQL.
