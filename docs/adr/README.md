# Architecture decision records

One file per decision that had a real alternative. The point is not to document
what the code does — the code does that — but to record *why*, so that a future
reader can tell a deliberate trade-off from an accident, and knows what would
have to change for the decision to be worth revisiting.

A decision that had no plausible alternative does not need a record.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-layered-architecture.md) | Layered architecture with ports and adapters | Accepted |
| [0002](0002-async-end-to-end.md) | Async end to end, enforced at startup | Accepted |
| [0003](0003-problem-details-errors.md) | RFC 9457 problem details for every error | Accepted |
| [0004](0004-keyset-pagination.md) | Keyset pagination instead of offset | Accepted |
| [0005](0005-idempotent-ingest.md) | Idempotent ingest backed by a partial unique index | Accepted |
| [0006](0006-liveness-vs-readiness.md) | Separate liveness and readiness probes | Accepted |
| [0007](0007-real-postgres-in-tests.md) | Integration tests run on real PostgreSQL | Accepted |
| [0008](0008-linear-timestamp-protocol.md) | Linear Timestamp Protocol as the LLM control plane | Accepted |

## Writing one

Copy the shape of an existing record: Context, Decision, Consequences,
Alternatives considered. Keep it to a page. Record the alternatives honestly,
including their genuine advantages — a record that makes the chosen option look
obvious is not helping anyone.

Records are immutable once accepted. To change a decision, add a new record that
supersedes the old one and update the status of both.
