# Mining for dollars

The product is older than this repository's current code. **mining4dollars**
exists to answer one question:

> Given this hardware, this electricity tariff, and today's market, which coin
> makes the most dollars?

Not "which coin has the highest hashrate." Not "which coin has the highest
gross revenue." Dollars after the power bill. That is the original 2017 intent,
and it is what the ranking function computes.

## The arithmetic

```
revenue = quote.revenue_usd_per_day × (worker_hps / quote.reference_hps)
cost    = (watts / 1000) × 24 × usd_per_kwh
profit  = revenue − cost
```

A quote is WhatToMine-shaped: "at H hashes/second, this coin grosses R dollars
in 24 hours." Scaling onto a rig is a ratio of hashrates. Electricity is
subtracted per algorithm when a capability records its own draw, so a thirsty
high-gross coin can lose to a modest efficient one.

Auto-assign only commits to a **profitable** coin. Idle beats a guaranteed
loss. An operator can still force a loser.

A new coin must beat the current assignment by **$0.10/day** before the
platform switches. Without that margin the fleet flaps on quote noise.

## The HTTP path

1. `POST /v1/coins` — list what the fleet may mine.
2. `POST /v1/pools` — optional stratum destinations.
3. `POST /v1/workers` — enrol a rig, with watts and `$/kWh`.
4. `POST /v1/workers/{id}/capabilities` — benchmarked (algorithm, hashrate[, watts]).
5. `POST /v1/quotes` — ingest a market snapshot.
6. `GET /v1/workers/{id}/profitability` — ranked options, best profit first.
7. `POST /v1/workers/{id}/assign` — point the rig at the winner (or a forced coin).
8. `POST /v1/workers/{id}/heartbeat` — telemetry; makes the worker `online`.
9. `GET /v1/fleet` — dollars the **online, assigned** fleet is estimated to make.

Every state change writes a `mining.*` event on the existing append-only log,
inside the same transaction.

## What this is not

It does not download a miner binary, talk to a GPU, or scrape WhatToMine. Those
are adapters. The domain ranks dollars; a later process can feed it quotes and
honour assignments. Classification of "what is profitable" is not a model call.
