# 0006 — Separate liveness and readiness probes

**Status:** Accepted · 2026-08-10

## Context

Orchestrators ask two questions with two different remedies:

- *Is this process working?* If not, **restart it**.
- *Can it serve traffic right now?* If not, **stop routing to it** — but leave
  it running.

A single `/health` endpoint that checks the database answers both with the same
value, and therefore answers one of them wrongly. If the database is briefly
unavailable, every replica reports unhealthy, and the orchestrator restarts all
of them at once. The restarts do not fix the database; they add a cold-start
stampede — empty pools, cold caches, a thundering reconnect — to an incident
that would otherwise have healed on its own.

## Decision

Two endpoints, unversioned and mounted at the root because probe URLs must not
move with an API version.

- **`GET /healthz`** — liveness. Touches no dependency. Returns 200 whenever the
  process can serve a request at all.
- **`GET /readyz`** — readiness. Round-trips the database and returns 200 or
  **503** with a per-dependency breakdown including latency.

Two details matter:

*The readiness check is independently time-bounded* at two seconds, shorter than
a typical prober timeout. A check that hangs is reported as not-ready rather
than leaving the prober to time out and guess.

*Its exception handling is deliberately broad.* Any driver, network, or pool
failure means the same thing to a load balancer. Narrowing it would let an
unanticipated exception escape as a 500, which a prober cannot interpret as
cleanly as an explicit unhealthy report.

## Consequences

A database outage takes instances out of rotation and lets them recover in
place; instances return automatically when the dependency does. The readiness
body reports which dependency failed and how slow it was, which is often the
first useful datum in an incident.

There are two endpoints to keep correct rather than one, and readiness must
enumerate dependencies explicitly — a future hard dependency that nobody adds to
the check will be missing from it. That is the trade for not having readiness
quietly become an unbounded fan-out of network calls on every probe.

`test_stays_alive_when_the_database_is_down` exists specifically to stop a
future change from wiring a dependency into liveness.

## Alternatives considered

**One `/health` doing both.** Half the surface. Rejected: it is the restart
stampede described above.

**Readiness checking every dependency transitively.** More thorough. Rejected
because probes run constantly, and a deep check turns each one into a burst of
network calls — and makes an instance unready for a dependency it could have
degraded gracefully without.

**Startup probe as a third endpoint.** Useful where initialisation is slow. Not
needed: startup here is opening a connection pool lazily, and readiness already
covers it.
