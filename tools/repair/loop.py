"""The repair loop itself.

One attempt is: ask the model, parse its reply into file changes, apply them
atomically, re-run the gate. The loop terminates when the gate passes, when the
model declines, or when the attempt budget runs out — never on the model's own
claim that it is finished.

The conversation is cumulative. Each failure goes back with the previous reply
still in history, so the model can see that its last diagnosis did not hold.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from anthropic.types import MessageParam

from tools.repair.client import ModelClient, ModelError, ModelReply
from tools.repair.journal import Journal
from tools.repair.patch import Patch, PatchError, apply_patch, parse_patch
from tools.repair.transcript import (
    SYSTEM_PROMPT,
    failure_prompt,
    initial_prompt,
    protocol_error_prompt,
    truncated_reply_prompt,
)
from tools.repair.verification import DEFAULT_LOG_LIMIT, VerificationReport, Verifier

__all__ = ["DEFAULT_MAX_ATTEMPTS", "AttemptRecord", "RepairLoop", "RepairOutcome"]

DEFAULT_MAX_ATTEMPTS = 15

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """What one turn of the loop did, for the caller to display or assert on."""

    attempt: int
    paths: tuple[str, ...] = ()
    applied: bool = False
    gate_passed: bool = False
    note: str | None = None


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    """The verdict of a run."""

    repaired: bool
    reason: str
    attempts: tuple[AttemptRecord, ...] = ()
    #: Present under ``dry_run``: what the first reply would have written.
    proposed: Patch | None = None

    @property
    def attempts_used(self) -> int:
        return len(self.attempts)


@dataclass(slots=True)
class RepairLoop:
    """Drives model, filesystem, and gate until the tree verifies or the budget ends.

    Args:
        client: The model to ask. Any :class:`~tools.repair.client.ModelClient`.
        verifier: The gate whose exit status defines success.
        root: Repository root; every write is confined to it.
        max_attempts: How many patches to try before giving up.
        journal: Where to record the run, or ``None`` to keep no record.
        dry_run: Ask for one patch, report it, and write nothing.
        log_limit: Characters of gate output carried back per turn.
        observer: Called with a human-readable line at each step, for progress
            output. The loop itself never writes to stdout.
    """

    client: ModelClient
    verifier: Verifier
    root: Path
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    journal: Journal | None = None
    dry_run: bool = False
    log_limit: int = DEFAULT_LOG_LIMIT
    observer: Callable[[str], None] = lambda _message: None
    _messages: list[MessageParam] = field(default_factory=list, init=False)

    def run(self) -> RepairOutcome:
        """Execute the loop and return its verdict."""
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")

        self._say(f"Running the gate: {'; '.join(self.verifier.commands)}")
        baseline = self.verifier.run()
        if self.journal is not None:
            self.journal.record_baseline(baseline)

        if baseline.passed:
            # Nothing to repair. Spending a max-effort turn to confirm that is
            # pure cost, and a model asked to fix a healthy tree will find
            # something to change.
            self._say("The gate already passes; nothing to repair.")
            return self._finish(RepairOutcome(repaired=True, reason="gate already passing"))

        self._report_failure(baseline)
        self._messages = [
            {"role": "user", "content": initial_prompt(baseline, log_limit=self.log_limit)}
        ]

        records: list[AttemptRecord] = []
        for attempt in range(1, self.max_attempts + 1):
            self._say(f"--- attempt {attempt}/{self.max_attempts}: querying {self._model_name()}")
            try:
                reply = self.client.complete(system=SYSTEM_PROMPT, messages=self._messages)
            except ModelError as error:
                return self._finish(
                    RepairOutcome(
                        repaired=False,
                        reason=f"the model could not be reached: {error}",
                        attempts=tuple(records),
                    )
                )

            if reply.refusal is not None:
                self._record(attempt, reply, note="declined")
                return self._finish(
                    RepairOutcome(
                        repaired=False,
                        reason=f"the model declined the request: {reply.refusal}",
                        attempts=tuple(records),
                    )
                )

            self._messages.append({"role": "assistant", "content": reply.text or "(empty reply)"})

            outcome, record = self._apply_and_verify(attempt, reply)
            records.append(record)
            if outcome is not None:
                return self._finish(
                    RepairOutcome(
                        repaired=outcome.repaired,
                        reason=outcome.reason,
                        attempts=tuple(records),
                        proposed=outcome.proposed,
                    )
                )

        return self._finish(
            RepairOutcome(
                repaired=False,
                reason=f"the gate still fails after {self.max_attempts} attempts",
                attempts=tuple(records),
            )
        )

    def _apply_and_verify(
        self, attempt: int, reply: ModelReply
    ) -> tuple[RepairOutcome | None, AttemptRecord]:
        """Carry out one attempt.

        Returns a terminal outcome when the run should stop, or ``None`` to keep
        going, alongside the record of what happened.
        """
        try:
            patch = parse_patch(reply.text)
        except PatchError as error:
            return self._retry_with_protocol_error(attempt, reply, str(error))

        if not patch:
            note = (
                "the reply was cut off before any file block closed"
                if reply.truncated
                else "the reply contained no file blocks"
            )
            self._say(f"No changes proposed: {note}.")
            self._record(attempt, reply, note=note)
            self._messages.append(
                {
                    "role": "user",
                    "content": truncated_reply_prompt()
                    if reply.truncated
                    else protocol_error_prompt(
                        "no file blocks were found", reply=reply.text, log_limit=self.log_limit
                    ),
                }
            )
            return None, AttemptRecord(attempt=attempt, note=note)

        if reply.truncated:
            # Some blocks parsed, but the reply stopped at the token ceiling, so
            # the last one is a fragment that would truncate a real file.
            note = "the reply was cut off at the output token limit"
            self._say(f"Discarding the patch: {note}.")
            self._record(attempt, reply, patch=patch, note=note)
            self._messages.append({"role": "user", "content": truncated_reply_prompt()})
            return None, AttemptRecord(attempt=attempt, paths=patch.paths, note=note)

        self._say(f"Patch touches {len(patch.changes)} file(s): {', '.join(patch.paths)}")

        if self.dry_run:
            self._record(attempt, reply, patch=patch, note="dry run: nothing written")
            return (
                RepairOutcome(
                    repaired=False,
                    reason="dry run: the proposed patch was not applied",
                    proposed=patch,
                ),
                AttemptRecord(attempt=attempt, paths=patch.paths, note="dry run"),
            )

        try:
            applied = apply_patch(self.root, patch)
        except PatchError as error:
            return self._retry_with_protocol_error(attempt, reply, str(error), patch=patch)

        report = self.verifier.run()
        self._record(attempt, reply, patch=patch, report=report)

        if report.passed:
            self._say("The gate passes.")
            return (
                RepairOutcome(repaired=True, reason=f"the gate passed on attempt {attempt}"),
                AttemptRecord(attempt=attempt, paths=patch.paths, applied=True, gate_passed=True),
            )

        self._report_failure(report)
        logger.debug("attempt %d changed %s", attempt, ", ".join(applied.paths) or "nothing")
        self._messages.append(
            {
                "role": "user",
                "content": failure_prompt(report, log_limit=self.log_limit, attempt=attempt),
            }
        )
        return None, AttemptRecord(attempt=attempt, paths=patch.paths, applied=True)

    def _retry_with_protocol_error(
        self, attempt: int, reply: ModelReply, error: str, *, patch: Patch | None = None
    ) -> tuple[None, AttemptRecord]:
        """Tell the model its reply was unusable and let it try again.

        A malformed reply costs an attempt but changes nothing on disk, so the
        loop stays in a known-good state.
        """
        self._say(f"Nothing applied: {error}")
        self._record(attempt, reply, patch=patch, note=error)
        self._messages.append(
            {
                "role": "user",
                "content": protocol_error_prompt(error, reply=reply.text, log_limit=self.log_limit),
            }
        )
        return None, AttemptRecord(
            attempt=attempt, paths=() if patch is None else patch.paths, note=error
        )

    def _record(
        self,
        attempt: int,
        reply: ModelReply,
        *,
        patch: Patch | None = None,
        report: VerificationReport | None = None,
        note: str | None = None,
    ) -> None:
        if self.journal is not None:
            self.journal.record_attempt(attempt, reply=reply, patch=patch, report=report, note=note)

    def _finish(self, outcome: RepairOutcome) -> RepairOutcome:
        if self.journal is not None:
            self.journal.record_outcome(
                {
                    "repaired": outcome.repaired,
                    "reason": outcome.reason,
                    "attempts": outcome.attempts_used,
                }
            )
        return outcome

    def _report_failure(self, report: VerificationReport) -> None:
        failure = report.first_failure
        if failure is not None:
            status = "timed out" if failure.timed_out else f"exit {failure.exit_code}"
            self._say(f"Gate failed: {failure.command} ({status})")

    def _model_name(self) -> str:
        return getattr(self.client, "model", type(self.client).__name__)

    def _say(self, message: str) -> None:
        self.observer(message)
        logger.info("%s", message)
