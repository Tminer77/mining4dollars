# 0008 — Linear Timestamp Protocol as the LLM control plane

**Status:** Accepted · 2026-08-22

## Context

A tree of agent jobs — proposed in parallel, some depending on others — is the
right shape for work. It is the wrong shape for history. Two branches that
commit without a total order invent two pasts, and an LLM that can invent
vocabulary can invent the facts those pasts contain.

Wall clocks do not save this. Clocks skew, jump, and run backwards. Ordering
events by `occurred_at` is how a log is queried, not how a machine is
controlled. Without a linear commit, "what happened" is a matter of opinion.

The glossary is the other half. If the machine is allowed to act on words it
has not been given, it will eventually act on a reality it has not been given.
Interpretation has to be a function from an utterance to a closed vocabulary,
with a name for every word it cannot bind — not a guess.

## Decision

The Linear Timestamp Protocol is a first-class domain on this foundation.

1. **Glossary.** Canonical terms with aliases. The interpreter binds an
   utterance greedily, left to right, against that lexicon. Unbound tokens are
   listed, never invented. Deprecated terms bind but cannot commit.
2. **Tree of Claude.** Nodes may be proposed in parallel. Edges are real parent
   dependencies. A node is a draft until it commits.
3. **Tape.** Commit assigns the next integer tick. Tick *n+1* cannot exist
   until tick *n* does. The server is the only clock; clients cannot claim a
   tick. If the wall clock runs backwards, the previous wall is kept and the
   tick still advances. `clock_skewed` records that the machine's clock lied
   and the protocol did not.
4. **Guardrails.** Commit is refused when language is unbound, a parent is not
   yet committed, genesis has not happened, or the node is already history.
   The refusal is a `guardrail_violation`, not a guess at what the caller
   meant.

The existing append-only event log remains the activity record. Every protocol
state change writes a `protocol.*` event in the same unit of work. The tape is
a different artefact: it is the control sequence, replayed oldest first from
origin, not the operator listing newest first.

Genesis is tick 0. The core glossary is seeded with the protocol's own
vocabulary so the origin utterance can bind.

## Consequences

LLM and agent action that is not on the tape did not happen. Drafting remains
cheap: proposing stores an interpretation, including unbound words, so an
operator can see why a commit would be refused. History remains expensive on
purpose.

The cost is ceremony. A one-line note does not need a node. The protocol is
for action that must not drift.

## Alternatives considered

**Wall-clock ordering only.** Already how the event log is queried. Rejected as
a control plane because clocks are not monotonic across machines or across a
single machine that steps backwards.

**Lamport clocks without a glossary.** Give causality without meaning. An agent
can still commit "xyzzy" as a perfectly well-ordered lie.

**Vector clocks.** Precisely capture concurrent branches. Rejected because the
point of this protocol is that concurrency is allowed in the *tree* and
forbidden in *history*. A vector clock would preserve the ambiguity the tape
exists to collapse.

**Let the model decide.** The failure mode this record exists to prevent.
Classification of language is a lookup, not a prediction.

**Social protocol only (a CLAUDE.md working agreement).** Necessary and
insufficient. A document that agents are asked to follow is not a lock on the
clock row.
