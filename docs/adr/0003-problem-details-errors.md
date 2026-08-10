# 0003 — RFC 9457 problem details for every error

**Status:** Accepted · 2026-08-10

## Context

Left alone, a FastAPI service emits at least three error shapes: `{"detail":
"..."}` from `HTTPException`, a nested array from request validation, and an
HTML traceback or bare 500 from anything unhandled. Clients end up writing a
different error path per endpoint, and most write none for the third case.

Separately, when a user reports "it failed", there is usually nothing in their
report that can be located in the logs.

## Decision

Every error — schema rejection, domain failure, or unhandled crash — is returned
as an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem document with
the `application/problem+json` media type, via handlers registered in
`api/errors.py`.

Two additions to the standard members:

- `code` — a stable, machine-readable identifier. Clients branch on this;
  `detail` is prose and may be reworded.
- `request_id` — the same value as the `X-Request-ID` response header, so a
  caller can quote one string that pinpoints their failure in the logs.

Domain errors map to statuses through one table, not through `HTTPException`
raised at call sites. Internal exception text appears outside production-like
environments and is replaced by a generic message within them.

## Consequences

One error path for clients, and support conversations that start with a request
id instead of a screenshot. The domain stays free of HTTP concepts: it raises
`NotFoundError`, and the mapping to 404 lives in one auditable place.

The cost is that `code` is now part of the public contract and cannot be renamed
freely, and that a new domain error type without a table entry silently falls
back to 400 — correct, but blunter than intended.

## Alternatives considered

**FastAPI defaults.** No work. Rejected for the three-shapes problem above.

**A bespoke envelope such as `{"error": {...}}`.** Equivalent in practice, and
free to design. Rejected because RFC 9457 is already specified, already
understood by tooling, and carries a registered media type.

**Returning tracebacks in production.** Genuinely faster to debug. Rejected:
it publishes file paths, dependency versions, and occasionally data. The request
id gives the same diagnostic power without the disclosure.
