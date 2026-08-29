# 0008 — The automated repair loop is closed by the verification gate

**Status:** Accepted · 2026-08-28

## Context

`tools/repair` asks a model to fix a failing tree, applies what it sends, and
repeats. The obvious way to build that is the obvious way to get it wrong: ask
the model for code, write the code, ask the model whether it worked.

A language model is a poor judge of its own output. It cannot see the tree
between turns, it has no way to run anything, and asked "did that fix it?" it
will answer from the same reasoning that produced the patch. A loop that
terminates on the model's assessment terminates on its confidence, which is not
correlated with correctness in the cases that matter — the ones where fifteen
attempts were needed.

Two further hazards are specific to letting a model write files unattended. It
can produce output that is not applicable at all, and it can produce output that
is applicable and destructive: a path outside the repository, a half-written
file from a reply that hit the token ceiling, or a "fix" that deletes the test
that was failing.

## Decision

The loop is closed by a subprocess, not by the model.

- **The gate is the only definition of success.** `make check` — the same
  commands CI runs — is executed after every patch, and its exit status is the
  loop's termination condition. The model is never asked whether it succeeded.
  The gate also runs *before* the first turn: a tree that already passes is
  returned untouched rather than handed to a model that will find something to
  change.
- **Whole files, not diffs.** The model returns complete file contents in
  fenced blocks tagged with the path. A whole file either parses or does not;
  there is no state in which half a hunk applied. The cost is tokens, which is
  the right trade against a corrupted working tree.
- **Every batch is atomic and reversible.** Paths are validated against the
  repository root before anything is opened, writes go through a
  same-directory temporary file and `os.replace`, and a snapshot of the previous
  state is kept so a failure partway through restores the tree. A reply that
  names one bad path changes nothing at all.
- **A truncated reply is discarded whole.** `stop_reason == "max_tokens"` means
  the last block is a fragment; applying it would silently truncate a real file,
  so the loop drops the patch and asks for fewer files per turn.
- **Unusable replies cost an attempt, not the tree.** A parse failure is fed
  back as an instruction and the run continues from a known-good state.
- **The system prompt forbids weakening the gate.** Deleting a failing test is
  the shortest path to green and the least useful one.
- **Every run is journalled.** Replies, patched paths, and full gate output are
  written under `.repair/` so a human can reconstruct and undo the run.

## Consequences

The loop cannot terminate on a false claim of success, and cannot leave the tree
in a state no one chose. What it can do is exhaust its budget without fixing
anything, which is the honest failure mode: the run exits non-zero and the
journal says what was tried.

The gate is run once per attempt, so a slow gate dominates wall-clock time. This
is the intended trade — the alternative is a faster loop with a weaker
termination condition — and `--verify` narrows the gate when iterating on one
subsystem.

Whole-file replies are expensive in output tokens, and large files may not fit
in one turn. The truncation path handles it by asking for fewer files, at the
cost of an attempt.

The model can still write a patch that passes the gate for the wrong reason.
The gate is a lower bound on quality, not a proof; runs are journalled and the
diff is meant to be reviewed, not merged unread.

## Alternatives considered

**Unified diffs.** Far cheaper in tokens and reviewable as-is. Rejected because
a model composing a diff against a file it cannot see gets the context lines
wrong, and a partially-applied patch is exactly the corrupted state this design
exists to avoid. Worth revisiting if the loop is given a tool to read files
mid-turn.

**Letting the model call tools to run the gate itself.** A more capable design,
and the natural one if the loop grows. Rejected here because it moves the
termination decision back inside the model: the loop would end when the model
stops asking for tools, not when the tree verifies.

**Reverting any patch that leaves the gate failing.** Tempting, and safer in the
narrow sense. Rejected because repair is often cumulative — attempt three
frequently builds on attempt two even though the gate still fails — and there is
no reliable way to tell progress from regression from an exit status alone.
Reversal is available (`AppliedPatch.revert`), and the journal plus `git diff`
is how a human exercises the judgement the loop lacks.

**Trusting the model's "all tests pass".** Free, and wrong for the reasons in
Context.
