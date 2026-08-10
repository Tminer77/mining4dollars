# 0002 — Async end to end, enforced at startup

**Status:** Accepted · 2026-08-10

## Context

The expected workload is I/O-bound: waiting on PostgreSQL and on outbound HTTP.
Concurrency is limited by how many waits can overlap, not by CPU.

Async only pays off if it is unbroken. A single synchronous database call
blocks the event loop, and every concurrent request on that worker stalls behind
it. The symptom — latency that collapses under load with no CPU saturation and
no slow query — is notoriously hard to attribute.

## Decision

Async from the route to the driver: FastAPI, SQLAlchemy 2.0 async, asyncpg.

Enforced rather than trusted. `Settings` rejects any `database_url` that is not
`postgresql+asyncpg`, so the common mistake — pasting a `postgresql://` URL,
which silently selects a synchronous driver — fails at startup with an
explanation instead of degrading in production.

## Consequences

High concurrency on modest resources, and one consistent idiom throughout.

In exchange: every I/O-touching function is `async`, which is contagious; a
blocking call slipped into a handler is invisible in code review and must go
through `asyncio.to_thread`; and tests need an event loop, with the loop-scope
mismatches that come with it (`asyncio_default_fixture_loop_scope` is set to
`function` for exactly this reason).

CPU-bound work does not belong in this process. If any appears, it goes to a
worker rather than a thread pool.

## Alternatives considered

**Synchronous with more workers.** Simpler to write, simpler to debug, and no
colour-of-function problem. Scales by process, so memory per unit of concurrency
is far higher. Reasonable, and rejected only because the workload is almost
entirely waiting.

**Async API over a sync driver in a thread pool.** Avoids rewriting query code.
Rejected: it caps concurrency at the thread pool size and reintroduces the
blocking behaviour async was adopted to avoid, while looking asynchronous.
