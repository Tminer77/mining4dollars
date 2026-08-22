# CLAUDE.md

Working agreement for agents operating in this repository. It applies to every
task, without needing to be restated. The Linear Timestamp Protocol in
[`docs/protocol.md`](docs/protocol.md) is the runtime enforcement of the same
rules; this file is how work is decomposed before it is committed onto the tape.

## Execution protocol

Non-trivial work is planned as a directed acyclic graph and executed as one.

**Nodes are atomic agent jobs.** One node does one thing that can be judged
right or wrong on its own: implement one module, write one test file, audit one
concern, answer one question. If a node's output needs two separate defences,
it is two nodes. If describing a node needs the word "and", it is probably two
nodes.

**Edges are real data dependencies only.** An edge exists when node B literally
cannot start without node A's output — B reads the interface A defines, or
consumes the file A writes. An edge does not exist because A is conceptually
prior, because A "feels" like it should come first, or because sequencing is
tidier to read. Ordering that is merely aesthetic is the single most common
cause of serial execution that had no reason to be serial.

**Independent nodes fan out in parallel.** Everything at the same depth in the
graph launches in one batch — one message, multiple tool calls. The critical
path is the only thing that dictates wall-clock time. Parallel *proposal* is
allowed. Parallel *history* is not: commits serialise onto the linear tape.

**Every output gets a fresh-context verifier.** A separate agent, with no
knowledge of how the work was produced and no stake in it, checks the artifact
against the requirement. It reads the code, runs the gates, and reports
pass/fail with evidence. The producing agent never grades itself: it has already
convinced itself once, which is exactly the bias the check exists to catch.
Verification is a node like any other, so verifiers for independent outputs also
run in parallel. A failed verification feeds back to the producing node; it does
not get argued past. A verify node cannot commit before the parent it checks.

**Language is closed.** Utterances that are meant to become history are
interpreted against the glossary first. Unbound words are named, not guessed.
An action whose interpretation is incomplete must not be treated as done.

**Work that is not on the tape did not happen.** Propose freely. Commit is the
gate. The Tree of Claude is the draft; the linear timestamp tape is the record.

**Parallel work is isolated in git worktrees.** Any node that writes files runs
in its own worktree, so concurrent agents cannot collide in the index, in the
working tree, or on a branch. Integration is a deliberate, explicit step after
verification — never a side effect of two agents having shared a checkout.
Worktrees are removed when their node completes.

**One synthesized report.** The user gets a single consolidated result at the
end: what was built, what each verifier concluded, what failed and what was
done about it, and what remains open. Not a transcript, not per-agent output
relayed verbatim, not a running commentary of intermediate states.

## Scope

The protocol governs how work is decomposed and checked; it does not change what
is being asked for. Trivial and conversational turns — a one-line question, a
single-file typo — are answered directly. Spinning up a graph for those costs
more than it returns.

## Repository gates

Verifier nodes run the same gates CI does. Nothing is reported as done until
these pass:

```bash
make check         # lint + types + test — everything CI runs
```

Narrower targets exist for tight loops: `make lint` (ruff), `make types`
(mypy `--strict`), `make test-unit` (I/O-free), `make test-integration` (needs
`M4D_TEST_DATABASE_URL`; skips without it). A verifier reports on `make check`.

Architecture and the reasoning behind it live in
[`docs/architecture.md`](docs/architecture.md); decisions with real alternatives
are recorded in [`docs/adr/`](docs/adr/). The Linear Timestamp Protocol is
specified in [`docs/protocol.md`](docs/protocol.md) and decided in
[ADR-0008](docs/adr/0008-linear-timestamp-protocol.md). A change that
contradicts an accepted ADR needs a new ADR, not a quiet edit.
