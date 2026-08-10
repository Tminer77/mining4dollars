# 0005 — Idempotent ingest backed by a partial unique index

**Status:** Accepted · 2026-08-10

## Context

Producers retry. A network timeout gives the client no way to know whether the
write landed, so the only safe behaviour is to send again — and without
server-side de-duplication, a retry storm during a blip permanently corrupts the
record with duplicates.

De-duplication has to survive the hard case, not just the easy one. The easy
case is a retry arriving after the original committed. The hard case is two
requests in flight simultaneously, where both look up the key, both find
nothing, and both insert.

## Decision

`POST /v1/events` accepts an optional `idempotency_key`. A replay returns the
original event with **200**; a genuine write returns **201**. The distinction is
carried through the service as `RecordResult.was_created` rather than inferred
at the route.

Enforcement is a **partial unique index**:

```sql
CREATE UNIQUE INDEX uq_system_event_idempotency_key
    ON system_event (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
```

Partial because de-duplication is opt-in. Most events carry no key, and under a
plain unique index those NULLs — while not equal to one another in SQL — would
still bloat the index for no purpose. The predicate keeps only participating
rows.

The service's pre-check is an **optimisation, not the mechanism**. Correctness
comes from the index:

1. Look up the key. If found, return it with `was_created=False`.
2. Otherwise insert. If the insert raises a conflict, the concurrent writer won
   — re-read by key and return the winner's row.

Step 2 requires the failed insert not to poison the transaction, so the
repository wraps it in a `SAVEPOINT` and translates the driver's
`IntegrityError` into a domain `ConflictError`. Without the savepoint the
transaction is unusable and the recovering read cannot happen.

## Consequences

Producers can retry freely, including concurrently. Clients can distinguish
"stored" from "already had it" without a second request.

The costs are honest ones: an index to maintain on every insert; a conflict path
that is easy to break and therefore covered by two dedicated tests
(`test_recovers_when_a_concurrent_writer_wins` in the unit suite, which fakes the
interleaving, and `test_transaction_survives_a_conflict` against real
PostgreSQL); and no expiry on keys, so the index grows with the table. If
retention becomes a problem the fix is a TTL sweep, which is a schema change
away.

Keys are scoped globally rather than per-producer. Two producers choosing the
same key would collide. Documented rather than solved, because scoping to
`(source, idempotency_key)` costs nothing to add later if it is ever needed.

## Alternatives considered

**Pre-check only.** Handles the sequential retry, which is the common case, and
needs no index. Rejected because it fails the concurrent case silently — the
duplicates appear only under the load that makes retries likely.

**`INSERT ... ON CONFLICT DO NOTHING` with a returning clause.** One round trip
instead of two on the conflict path, and genuinely neater. Rejected because the
result no longer distinguishes "inserted" from "already present" without a
further read, and the 200-versus-201 distinction is worth keeping.

**Client-supplied primary keys.** No extra index; identity *is* the key.
Rejected because it lets a caller choose a server-assigned identifier, and makes
every producer responsible for global uniqueness.

**A dedicated idempotency table with TTL.** Standard for payment APIs, and the
right answer when a stored *response* must be replayed. Disproportionate here,
where the stored event is the response.
