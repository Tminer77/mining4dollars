# 0004 — Keyset pagination instead of offset

**Status:** Accepted · 2026-08-10

## Context

The tables this platform is built around are append-heavy and read newest-first.
Offset pagination fails them in two distinct ways.

*Performance.* `OFFSET n` makes PostgreSQL produce and discard `n` rows. Page 1
is instant and page 500 is a scan; the cost grows with how far a client has
read, which is precisely what an exporter or a backfill job does.

*Correctness.* Offsets are positions in a result set that is still changing.
Insert a row while a client is walking pages and every subsequent page shifts by
one — the client silently skips a record. Delete one and it sees a duplicate.
This is not a race that can be closed by retrying; it is inherent to the scheme.

## Decision

Keyset pagination. A cursor carries the sort key of the last row seen, and the
next page resumes strictly after it:

```sql
WHERE (occurred_at, id) < (:cursor_occurred_at, :cursor_id)
ORDER BY occurred_at DESC, id DESC
LIMIT :n
```

The key is `(occurred_at, id)`, not `occurred_at` alone. Timestamps collide
constantly under bulk ingest, and without a unique tiebreaker the ordering is
not total, so rows at a page boundary can be skipped or repeated — the exact bug
offsets were rejected for.

Three supporting choices:

- The comparison is written as a **row value**, not as `occurred_at < :x OR
  (occurred_at = :x AND id < :y)`. Both are correct; only the row-value form
  lets PostgreSQL satisfy the page as a single range scan on
  `ix_system_event_occurred_at_id`, whose column order and direction mirror the
  `ORDER BY` exactly.
- Cursors are **opaque** base64. Clients cannot parse them, which leaves the
  sort key free to change later.
- A page fetches `limit + 1` rows. The extra row's presence proves another page
  exists, avoiding a `COUNT(*)` that would be a full scan on an append-only
  table.

## Consequences

Constant cost per page regardless of depth, and a stable sequence under
concurrent writes. Pagination performance now depends on an index, so
`test_keyset_index_exists` asserts it survives.

What is given up is real: there is no "jump to page 7" and no total count. The
API therefore offers neither, rather than offering a slow version. Filters and
`limit` must stay constant across a cursor walk, which is a constraint clients
have to be told about — it is documented on the endpoint.

## Alternatives considered

**Offset/limit.** Universally understood, supports random access and totals, and
is the right choice for small or static result sets. Rejected on both counts
above.

**Offset with a snapshot or `AS OF` timestamp.** Fixes correctness while keeping
random access. Rejected: it needs either long-lived snapshots or history
retention, which is a large amount of machinery for a feature nobody has asked
for.

**Opaque cursor over a monotonic sequence.** Simpler than a composite key, but
ties ordering to insertion rather than occurrence, which is wrong when events
arrive late or are backfilled — see `occurred_at` versus `recorded_at`.
