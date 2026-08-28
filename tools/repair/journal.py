"""On-disk record of what the loop did.

An unattended process that rewrites source files must leave a trail. Every
attempt's reply, the paths it touched, and the gate output it produced are
written under ``.repair/`` so a human can reconstruct — and undo — the run
afterwards, rather than inferring it from a terminal that has since scrolled.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from tools.repair.client import ModelReply
from tools.repair.patch import Patch
from tools.repair.verification import VerificationReport

__all__ = ["JOURNAL_DIRNAME", "Journal"]

JOURNAL_DIRNAME = ".repair"


@dataclass(frozen=True, slots=True)
class Journal:
    """A directory holding one run's artefacts."""

    directory: Path

    @classmethod
    def create(cls, root: Path, *, now: dt.datetime | None = None) -> Journal:
        """Open a fresh run directory under ``root/.repair``."""
        stamp = (now or dt.datetime.now(tz=dt.UTC)).strftime("%Y%m%dT%H%M%SZ")
        directory = root / JOURNAL_DIRNAME / stamp
        directory.mkdir(parents=True, exist_ok=True)
        return cls(directory=directory)

    def record_baseline(self, report: VerificationReport) -> None:
        """Store the gate output the run started from."""
        self._write("baseline.log", report.failure_log(limit=0))

    def record_attempt(
        self,
        attempt: int,
        *,
        reply: ModelReply,
        patch: Patch | None = None,
        report: VerificationReport | None = None,
        note: str | None = None,
    ) -> None:
        """Store one attempt: what the model said, did, and what came of it."""
        prefix = f"attempt-{attempt:02d}"
        self._write(f"{prefix}-reply.md", reply.text)

        summary: dict[str, object] = {
            "attempt": attempt,
            "stop_reason": reply.stop_reason,
            "request_id": reply.request_id,
            "paths": list(patch.paths) if patch is not None else [],
            "gate_passed": report.passed if report is not None else None,
            "note": note,
        }
        self._write(f"{prefix}-summary.json", json.dumps(summary, indent=2) + "\n")

        if report is not None:
            self._write(f"{prefix}-gate.log", report.failure_log(limit=0))

    def record_outcome(self, payload: dict[str, object]) -> None:
        """Store the run's verdict."""
        self._write("outcome.json", json.dumps(payload, indent=2) + "\n")

    def _write(self, name: str, content: str) -> None:
        (self.directory / name).write_text(content, encoding="utf-8")
