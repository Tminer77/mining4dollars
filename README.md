# mining4dollars

Foundation services for the mining4dollars platform.

This repository is the load-bearing base the rest of the product is built on:
configuration, structured logging, request correlation, a transactional
persistence layer, a uniform error contract, migrations, and a test suite that
runs against a real PostgreSQL database.

It currently ships one working vertical slice — an append-only **system event
log** — that exercises every layer end to end. The slice is real, not a
placeholder: an activity record is something every subsystem built here will
need. See [Adding a feature](#adding-a-feature) for the path a new capability
follows through the same layers.

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
| `tools/repair/` | Developer tooling: the automated repair loop |
| `tools/factory/` | Developer tooling: the App Store and Play release factories |

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

### Automated repair

`make repair` drives `make check` to green by asking Claude for whole-file
patches and re-running the gate after each one. It is developer tooling under
`tools/repair/`; nothing in the service imports it.

```bash
uv pip install --python .venv/bin/python -e ".[repair]"
export ANTHROPIC_API_KEY=...        # or: ant auth login

make repair a="--dry-run"           # one turn, prints the patch, writes nothing
make repair                         # up to 15 attempts against `make check`
make repair a='--verify "make lint" --max-attempts 5'
```

The loop's termination condition is the gate's exit status, never the model's
opinion of its own work: a tree that already passes is left untouched, every
patch is applied atomically and confined to the repository, and a reply cut off
at the token limit is discarded rather than written half-formed. Each run leaves
its replies and gate logs under `.repair/`, which is gitignored.

It rewrites source files in place. Run it on a clean tree so `git diff` is the
review, and read the diff — a gate is a lower bound on quality, not a proof. The
reasoning behind the design is in
[ADR 0008](docs/adr/0008-verified-automated-repair.md).

### App factories

`tools/factory/` ships an app to the App Store or Google Play. It is driven by a
`factory.toml` in the app's repository rather than by arguments, so a release is
reproducible from the repository instead of from someone's shell history. It is
project-agnostic: point it at any Xcode or Gradle project.

```bash
python -m tools.factory init --with-workflows   # spec + release workflows
python -m tools.factory preflight               # can this ship?
python -m tools.factory plan --platform apple   # exactly what would run
python -m tools.factory run  --platform apple   # do it (needs the toolchain)
```

`preflight` is the part that earns its keep. Every failure it can catch — an
unset secret, a build number already uploaded, a missing project, a
non-executable `gradlew` — otherwise surfaces half an hour into a runner's
archive, and every result carries the fix rather than pointing at documentation:

```
  [FAIL] build number: Build number 57 is not greater than the last uploaded build (99).
         -> Left unresolved, this fails at upload — after the build.
  [SKIP] toolchain: xcodebuild cannot be checked on linux
         -> The archive step needs a macOS runner. This check runs there.
```

A check that cannot run here reports `SKIP`, never `PASS`. The reasoning is in
[ADR 0009](docs/adr/0009-declarative-release-factories.md).

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

The foundation is complete and verified. The domain slice is deliberately
generic: the platform's specific entities are not yet modelled, and adding them
is the next step.
