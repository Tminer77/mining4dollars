# mining4dollars

Company control plane for AI antivirus and machine optimizers.

This repository is the load-bearing base the rest of the product is built on:
configuration, structured logging, request correlation, a transactional
persistence layer, a uniform error contract, migrations, and a test suite that
runs against a real PostgreSQL database.

It ships two working vertical slices:

- an append-only **system event log**, the activity record every subsystem
  writes to
- **Shield** — fleet inventory, scan ingest, threat classification, quarantine,
  and optimizer plans

See [Adding a feature](#adding-a-feature) for the path a new capability follows
through the same layers, and [`docs/shield.md`](docs/shield.md) for the product
domain.

- **Language:** Python 3.12
- **API:** FastAPI, async end to end
- **Storage:** PostgreSQL 16 via SQLAlchemy 2.0 (asyncpg) and Alembic
- **Quality gates:** ruff, mypy `--strict`, pytest

---

## Quick start

```bash
# 1. Dependencies
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"

# 2. Configuration
cp .env.example .env      # then edit M4D_DATABASE_URL

# 3. Schema
.venv/bin/alembic upgrade head

# 4. Run
.venv/bin/m4d serve --reload
```

The API is then on <http://localhost:8000>, with interactive documentation at
`/docs` (served everywhere except staging and production).

```bash
curl -X POST localhost:8000/v1/events \
  -H 'Content-Type: application/json' \
  -d '{"source":"demo","kind":"service.started","severity":"info"}'

curl localhost:8000/v1/events?limit=10
```

Shield — enrol a miner, scan it, isolate on a confirmed sample, then propose a tune:

```bash
ENDPOINT=$(curl -s -X POST localhost:8000/v1/endpoints \
  -H 'Content-Type: application/json' \
  -d '{"hostname":"rig-01.site","platform":"linux","role":"miner"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

curl -X POST localhost:8000/v1/endpoints/$ENDPOINT/scans \
  -H 'Content-Type: application/json' \
  -d '{"kind":"quick"}'

curl localhost:8000/v1/fleet
```

`make help` lists every development task.

---

## Architecture

Four layers, with dependencies pointing strictly inward. The domain is the
centre and imports nothing else; infrastructure depends on the domain, never the
reverse.

```
HTTP  ─→  api/         routes, schemas, error rendering, middleware
          services/    use cases; owns the transaction boundary
          domain/      entities, value objects, errors, ports  ← depends on nothing
          db/          SQLAlchemy adapters implementing those ports
```

The rule is enforceable by reading imports: nothing under `domain/` imports
`fastapi`, `sqlalchemy`, or `m4d.config`. That constraint is what makes the
business rules testable without a database and keeps storage decisions from
leaking into them.

Full reasoning, including the request lifecycle and per-layer responsibilities,
is in [`docs/architecture.md`](docs/architecture.md). Individual decisions and
the alternatives rejected are recorded in [`docs/adr/`](docs/adr/).

### Layout

| Path | Holds |
| --- | --- |
| `src/m4d/domain/` | Entities, value objects, domain errors, ports (Protocols) |
| `src/m4d/services/` | Use cases; one transaction boundary each |
| `src/m4d/db/` | Engine, ORM tables, repositories, unit of work |
| `src/m4d/api/` | Routes, wire schemas, error handlers, middleware |
| `src/m4d/observability/` | Structured logging and request context |
| `src/m4d/config.py` | The entire configuration surface |
| `migrations/` | Alembic revisions |
| `tests/unit/` | No I/O; fast |
| `tests/integration/` | Real PostgreSQL |

---

## Configuration

Every setting is read from the environment, validated once at startup, and
exposed through `m4d.config.Settings`. Nothing reads `os.environ` directly, so
the full surface is discoverable in one file. A misconfigured deployment fails
immediately rather than at the first request that touches the bad value.

All variables are prefixed `M4D_`; see [`.env.example`](.env.example) for the
complete list. `m4d config` prints the resolved values with the database
password redacted.

---

## The API contract

**Errors.** Every failure — schema rejection, missing row, or unhandled crash —
is returned as an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem
document with the `application/problem+json` media type:

```json
{
  "type": "about:blank",
  "title": "Not Found",
  "status": 404,
  "detail": "Event '…' was not found",
  "code": "not_found",
  "instance": "/v1/events/…",
  "request_id": "9f2c…"
}
```

Branch on `code`, which is stable; `detail` is prose and may be reworded.

**Correlation.** Every response carries `X-Request-ID`. Send your own to trace a
request across services, or let the server generate one. It appears on every log
line emitted while handling the request and in every error body.

**Pagination.** Listings use keyset, not offset, pagination. Pass the
`next_cursor` from one response as `cursor` on the next; a null `next_cursor`
means the end. Cursors are opaque — do not parse them. Keep filters and `limit`
identical across a walk.

**Idempotency.** `POST /v1/events` accepts an `idempotency_key`. A replay
returns the original event with `200` instead of creating a duplicate and
returning `201`, so a client that retries after a timeout is safe. Correctness
rests on a unique index, so concurrent duplicates are handled too, not just
sequential retries.

**Probes.** `/healthz` is liveness and touches no dependency — a failure means
restart the process. `/readyz` is readiness and checks the database — a failure
means remove the instance from the load balancer but leave it running. Keeping
them separate stops a brief database blip from restarting every instance at
once.

---

## Development

```bash
make install     # virtualenv + dependencies
make fmt         # format
make lint        # ruff + mypy --strict
make test        # unit + integration
make check       # everything CI runs
make migrate     # alembic upgrade head
make revision m="add widgets"
```

### Tests

`tests/unit/` is pure and fast. `tests/integration/` runs against a real
PostgreSQL instance — never SQLite, because the schema depends on JSONB, partial
unique indexes, and row-value comparison, none of which SQLite models. A test
suite that passes on a database you do not deploy is not evidence.

The integration suite applies the Alembic migrations rather than
`metadata.create_all`, so the migration path itself is covered on every run. One
test asserts that autogenerate finds no difference between the migrations and
the models, which catches a model changed without a matching revision.

Point the suite at a database with:

```bash
export M4D_TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/m4d_test
make test
```

Integration tests are skipped, not failed, when no database is reachable, so
`make test` still works offline.

### Adding a feature

The event slice is the worked example; a new capability follows the same path.

1. **Domain** — add the entity and its rules in `domain/`, and the port it needs
   in `domain/ports.py`. No imports outside the domain.
2. **Persistence** — add the table in `db/tables.py`, a repository in
   `db/repositories/` implementing the port, and expose it on the unit of work.
3. **Migration** — `make revision m="…"`, then read the generated SQL before
   committing it.
4. **Service** — add the use case in `services/`, depending only on ports.
5. **API** — add wire schemas in `api/schemas.py` and routes in `api/routes/`.
6. **Tests** — unit tests for the rules, integration tests for the endpoint.

---

## Status

The foundation is complete and verified. Shield is the first product domain:
fleet inventory, classified findings, automatic isolation, and optimizer plans.
Classification is deterministic company policy so it is testable without a
model; a later adapter can wrap it without changing scans or quarantine.
