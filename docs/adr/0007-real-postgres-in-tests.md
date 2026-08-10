# 0007 — Integration tests run on real PostgreSQL

**Status:** Accepted · 2026-08-10

## Context

Testing the persistence layer against SQLite is fast, needs no service, and
works offline. It is also, for this schema, close to meaningless.

The design depends on behaviour SQLite does not have: `JSONB`, partial unique
indexes (`WHERE idempotency_key IS NOT NULL`), row-value comparison
(`(a, b) < (c, d)`), `SAVEPOINT` semantics under a failed constraint, and
`timestamptz`. Every one of those is load-bearing for a decision recorded in
another ADR. A green SQLite suite would be evidence about a database we do not
deploy.

## Decision

Integration tests run against PostgreSQL 16 — the deployed major version — in
CI and locally.

- The schema is built by **running the Alembic migrations**, not
  `metadata.create_all`, so the migration path is exercised on every run rather
  than only the models it is meant to produce.
- The session fixture downgrades to base first, guaranteeing a known starting
  point and incidentally proving the down path.
- `test_models_and_migrations_agree` asserts autogenerate finds no structural
  difference between models and schema, catching a model edited without a
  revision.
- Constraints are tested by trying to violate them with raw SQL, since their
  entire purpose is to stop writes that bypass the application.
- Tests **skip rather than fail** when `M4D_TEST_DATABASE_URL` is unset, so the
  suite still runs offline.

## Consequences

The suite tests what actually ships. Real constraint violations, real JSONB
round-trips, real savepoint recovery.

The costs: contributors need PostgreSQL reachable to run the full suite
(`docker compose up db` provides it); CI needs a service container, worth a few
seconds of startup; and the integration suite is slower than the unit suite by
roughly an order of magnitude, which is why the two are separated and
`make test-unit` exists.

The autogenerate drift check needs a documented exclusion list. Alembic cannot
reflect expression indexes such as `occurred_at DESC`, and the CHECK backing a
non-native `Enum` is emitted at DDL time and never appears in metadata, so both
would be permanent false positives. Those objects are asserted on directly
instead, and the shared `include_object` policy lives in
`m4d.db.autogenerate` so that revision generation and the test cannot drift
apart from each other.

## Alternatives considered

**SQLite in memory.** Fast, zero setup, no service. Rejected for the reasons
above; the features under test do not exist there.

**Testcontainers.** Manages the database lifecycle per run, removing the
"is Postgres running?" question. Genuinely attractive, and a reasonable future
change. Rejected for now because it requires a working Docker daemon in every
environment, which is a heavier prerequisite than a connection string.

**Mocking the repository in every test.** Fast and dependency-free. Already done
where it belongs — `tests/unit/fakes.py` covers service logic. It cannot,
however, test whether the SQL is correct, which is the entire purpose of the
integration suite.
