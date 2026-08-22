# 0008 — Profit ranking lives in the domain

**Status:** Accepted · 2026-08-22

## Context

The foundation shipped an append-only event log and four layers, and stopped
there. The repository is named mining4dollars. The original product is not an
activity record: it is a profitability engine. "Which coin should this rig
mine so that the operator makes the most dollars after electricity?"

Putting that arithmetic in a route handler or a spreadsheet export would make
it untestable without HTTP and would let a later "AI optimiser" quietly replace
the only rule the product has.

## Decision

Profit ranking is a pure function in `domain/profit.py`:

- A coin is eligible only if it is enabled, the worker has a capability for
  its algorithm, and a quote exists.
- Revenue is the quote scaled by the worker's hashrate over the quote's
  reference hashrate.
- Cost is 24 hours of electricity at the algorithm's draw (capability watts,
  else the worker default) and the worker's `$/kWh`.
- Rank by profit descending, ticker ascending.
- Auto-assign only a profitable coin. Force-assign may pick a loss.
- Switch only when the gain meets a $0.10/day margin.

Quotes are ingested; they are not fetched inside the domain. A WhatToMine
adapter can be added later as a producer of `NewQuote`. It cannot replace
`rank_options`.

## Consequences

The original-intent tests in `tests/unit/test_domain_profit.py` are the spec.
They run without a database. A change that makes a high-gross thirsty coin
beat a modest efficient one is a failing test, not a product decision.

The cost is that live market data is someone else's job. That is accepted:
wrong dollars from a stale quote are an operations problem, wrong dollars
from broken arithmetic are a platform bug.

## Alternatives considered

- **Gross revenue only.** Simpler, and on a single worker with one power
  number it ranks the same. Rejected because electricity is why this is a
  dollars product, and per-algorithm watts make the ranking diverge.
- **A model or optimiser service.** Rejected. "ERG at 200 W beats RVN at
  1500 W on this tariff" is not a prediction.
- **Leave the domain generic.** That is what the foundation did. It left the
  repository unable to do the thing it is named for.
