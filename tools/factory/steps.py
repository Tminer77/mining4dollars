"""Running build steps without leaking the credentials they need.

A release pipeline holds signing keys and API keys in its environment, and its
logs are the first thing anyone reads when a build fails. Every command run
here has its output scrubbed of every secret value it was given, so a step that
echoes its environment — and some do — cannot turn a public CI log into a
credential leak.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["REDACTION", "Step", "StepResult", "StepRunner", "redact"]

#: What a secret's value is replaced with in captured output.
REDACTION = "***"

#: Values shorter than this are not redacted: a two-character secret would
#: match constantly and turn the log into noise. Real credentials are long.
_MIN_REDACTABLE = 8


@dataclass(frozen=True, slots=True)
class Step:
    """One command in a build plan."""

    name: str
    argv: tuple[str, ...]
    #: Working directory, relative to the repository root.
    cwd: str = "."
    #: Environment names this step needs; used for the plan's documentation and
    #: to decide what must be redacted from its output.
    secrets: tuple[str, ...] = ()
    #: Extra non-secret environment for this step alone.
    env: Mapping[str, str] = field(default_factory=dict)
    #: True for steps that cannot run anywhere but macOS.
    macos_only: bool = False

    @property
    def command(self) -> str:
        """The step as it would be typed, for display."""
        return " ".join(self.argv)


@dataclass(frozen=True, slots=True)
class StepResult:
    """What one step did."""

    step: Step
    exit_code: int
    output: str
    duration_seconds: float

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def redact(text: str, secrets: Sequence[str]) -> str:
    """Replace every secret *value* in ``text`` with :data:`REDACTION`.

    Longest first, so a secret that contains another is masked whole rather
    than leaving a recognisable fragment behind.
    """
    scrubbed = text
    for value in sorted({s for s in secrets if len(s) >= _MIN_REDACTABLE}, key=len, reverse=True):
        scrubbed = scrubbed.replace(value, REDACTION)
    return scrubbed


class StepRunner:
    """Executes a plan, stopping at the first failure.

    A release is strictly ordered — there is no useful sense in which uploading
    proceeds after archiving failed — so unlike a test gate there is nothing to
    gain from running the rest.
    """

    def __init__(
        self,
        root: Path,
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 3600.0,
        observer: object = None,
    ) -> None:
        self._root = root
        self._env = dict(os.environ if env is None else env)
        self._timeout = timeout_seconds
        self._observer = observer

    def run(self, steps: Sequence[Step]) -> list[StepResult]:
        """Run ``steps`` in order, stopping after the first failure."""
        results: list[StepResult] = []
        for step in steps:
            result = self.run_one(step)
            results.append(result)
            if not result.passed:
                break
        return results

    def run_one(self, step: Step) -> StepResult:
        """Run a single step, redacting its secrets from the captured output."""
        environment = dict(self._env)
        environment.update(step.env)
        # Only the values this step was actually given can appear in its output.
        secret_values = [environment[name] for name in step.secrets if name in environment]

        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - argv is built by the factory, not by input
                list(step.argv),
                cwd=self._root / step.cwd,
                env=environment,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
            output = completed.stdout + completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as expired:
            output = _text(expired.stdout) + _text(expired.stderr)
            output += f"\n[timed out after {self._timeout:.0f}s]"
            exit_code = -1
        except OSError as error:
            output = f"Could not execute {step.command!r}: {error}"
            exit_code = -1

        return StepResult(
            step=step,
            exit_code=exit_code,
            output=redact(output, secret_values),
            duration_seconds=time.monotonic() - started,
        )


def _text(stream: str | bytes | None) -> str:
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream
