# Linear Timestamp Protocol

The Tree of Claude is a DAG of jobs. The tape is a total order of commits.
The glossary is the closed vocabulary those jobs are allowed to speak.
Together they are the control plane that keeps an LLM on the rails.

Work that is not on the tape did not happen.

## Why

A branching agent graph without a linear commit invents concurrent histories.
An LLM without a glossary invents concurrent meanings. Either one is enough
to walk off the guardrail; both at once is how systems quietly become
unverifiable.

Time here is a control, the same way a lock is a control. The wall clock is
consulted and recorded. It is not in charge. If it runs backwards, the tape
does not: the previous wall is kept, the tick still advances, and
`clock_skewed` is set so the lie is evidence.

## The three artefacts

| Artefact | What it is | What it is not |
| --- | --- | --- |
| **Glossary** | Canonical terms, aliases, definitions | A prompt, a style guide, a suggestion |
| **Tree** | Proposed and committed nodes, with parent edges | History |
| **Tape** | Strictly increasing ticks, oldest first | The event log (that remains newest first) |

A node is *proposed* by uttering something. The interpreter binds that
utterance immediately. Unbound words do not block proposing — drafting may be
messy. They block *commit*. Commit is the only way onto the tape, and commit
is refused when:

- the utterance still contains words the glossary does not know
- a bound term has been deprecated
- any parent is not yet committed
- genesis has not been committed, unless this node *is* genesis
- the node is already committed or already rejected

Rejected drafts are terminal. A correction is a new node.

## The interpreter

`interpret("commit the parent node onto the tape")` against the core glossary
yields bindings `(commit, parent, node, tape)` and no unbound tokens. That
utterance may commit.

`interpret("hack the production database")` yields unbound
`(hack, production, database)` and no bindings. That utterance may be
proposed, so the gap is visible, and must not commit.

Matching is greedy and left to right. `"linear time"` is the term
`linear-time`. `"ltp"` is an alias of the same term. The interpreter does
not guess: a word is in the lexicon or it is named as unbound.

Definitions are snapshotted into the interpretation, so a later edit of a
term cannot rewrite what a committed tick meant.

## The clock

The protocol clock is a single database row, locked with `FOR UPDATE` around
every commit, so concurrent commits serialise onto consecutive ticks rather
than colliding.

- Tick 0 is genesis: `"genesis of the linear-time protocol tape"`.
- Tick *n+1* does not exist until tick *n* does.
- Clients cannot supply a tick. The server assigns it.
- `wall` is timezone-aware UTC, never naive.
- A backwards wall keeps the previous wall and still increments the tick.

## HTTP

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/protocol/bootstrap` | Seed the core glossary and commit genesis. Idempotent. |
| `POST` | `/v1/protocol/terms` | Define a canonical term. |
| `GET` | `/v1/protocol/terms` | List the glossary. |
| `POST` | `/v1/protocol/interpret` | Bind an utterance. Does not commit. |
| `POST` | `/v1/protocol/nodes` | Propose a tree node. Empty parents attach to genesis. |
| `POST` | `/v1/protocol/nodes/{id}/commit` | Serialise onto the tape as the next tick. |
| `POST` | `/v1/protocol/nodes/{id}/reject` | Refuse a draft. |
| `GET` | `/v1/protocol/tape` | Replay ticks oldest first. `after_tick` pages forward. |
| `GET` | `/v1/protocol/tree` | Snapshot: head, nodes, tape. |
| `GET` | `/v1/protocol/head` | The last committed instant. |
| `GET` | `/tree` | Operator display for the same snapshot. |

Guardrail refusals are RFC 9457 problem documents with
`code: "guardrail_violation"` and HTTP 409. Branch on `context.rule`.

Every state change also appends a `protocol.*` event to the existing system
log, in the same transaction.

## Core vocabulary

The seeded glossary is the protocol talking about itself: `linear-time`,
`tick`, `tape`, `tree`, `node`, `parent`, `commit`, `interpret`, `glossary`,
`guardrail`, `utterance`, `alias`, `genesis`, `verify`, `protocol`.

Adding a term is how new language becomes legal. An alias that collides with
another term's key is a conflict: two terms must not claim the same word.

## Adding a term, proposing, committing

```bash
curl -X POST localhost:8000/v1/protocol/bootstrap

curl -X POST localhost:8000/v1/protocol/interpret \
  -H 'Content-Type: application/json' \
  -d '{"utterance":"commit the parent node onto the tape"}'

curl -X POST localhost:8000/v1/protocol/nodes \
  -H 'Content-Type: application/json' \
  -d '{"utterance":"commit the parent node onto the tape"}'

curl -X POST localhost:8000/v1/protocol/nodes/<id>/commit
```

Open `/tree` for the operator view: tape on the left, DAG on the right.
Committed nodes are gold. Proposed nodes are drafts. Rejected nodes are red.
A tick marked `clock held` is one where the wall clock tried to run
backwards and the protocol froze it.
