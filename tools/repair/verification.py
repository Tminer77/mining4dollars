"""The gate that decides whether the tree is repaired.

The model's own assessment of its work is not evidence. A command's exit status
is. Everything in this module exists to produce two things: a boolean the loop
can terminate on, and a log the model can act on.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_COMMANDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "CommandResult",
    "VerificationReport",
    "Verifier",
]

#: The repository's own gate. ``make check`` is lint, types, and tests, in the
#: order CI runs them, so a green loop means a green pipeline.
DEFAULT_COMMANDS: tuple[str, ...] = ("make check",)

DEFAULT_TIMEOUT_SECONDS = 900.0

#: How much of a failing command's output travels back to the model. Compilers
#: and test runners repeat themselves; the first and last few thousand
#: characters carry the diagnosis, and the middle is what blows up a context
#: window over fifteen attempts.
DEFAULT_LOG_LIMIT = 20_000

_ELISION = "\n\n[... {dropped} characters elided ...]\n\n"


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of one verification command."""

    command: str
    exit_code: int
    output: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """The outcome of a full pass over the configured commands."""

    results: tuple[CommandResult, ...]

    @property
    def passed(self) -> bool:
        """True only when every command that ran succeeded, and one did."""
        return bool(self.results) and all(result.passed for result in self.results)

    @property
    def first_failure(self) -> CommandResult | None:
        return next((result for result in self.results if not result.passed), None)

    def failure_log(self, limit: int = DEFAULT_LOG_LIMIT) -> str:
        """Render the failing commands as text to send back to the model."""
        sections: list[str] = []
        for result in self.results:
            if result.passed:
                sections.append(f"$ {result.command}\n[passed in {result.duration_seconds:.1f}s]")
                continue
            status = "timed out" if result.timed_out else f"exit status {result.exit_code}"
            sections.append(
                f"$ {result.command}\n[{status} after {result.duration_seconds:.1f}s]\n"
                f"{truncate(result.output, limit)}"
            )
        return "\n\n".join(sections)


def truncate(text: str, limit: int) -> str:
    """Keep the head and tail of ``text``, dropping the middle.

    Both ends matter: a test runner puts the first failure at the top and the
    summary at the bottom, and either alone is a partial picture.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    dropped = len(text) - limit
    return f"{text[:head]}{_ELISION.format(dropped=dropped)}{text[-tail:]}"


class Verifier:
    """Runs the configured commands in the repository and reports the result."""

    def __init__(
        self,
        commands: Sequence[str] = DEFAULT_COMMANDS,
        *,
        cwd: Path,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        stop_on_first_failure: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if not commands:
            raise ValueError("A verifier needs at least one command.")
        # Parsed up front so an unrunnable or unbalanced command is a startup
        # error rather than a traceback fifteen minutes into a run.
        self._argv = tuple(_parse(command) for command in commands)
        self._commands = tuple(commands)
        self._cwd = cwd
        self._timeout_seconds = timeout_seconds
        self._stop_on_first_failure = stop_on_first_failure
        self._env = dict(env) if env is not None else None

    @property
    def commands(self) -> tuple[str, ...]:
        return self._commands

    def run(self) -> VerificationReport:
        """Execute the gate.

        Stops at the first failure by default: running mypy over a tree ruff has
        already rejected produces a second wave of errors that are consequences
        of the first, and handing the model both invites it to fix the symptom.
        """
        results: list[CommandResult] = []
        for command, argv in zip(self._commands, self._argv, strict=True):
            result = self._run_one(command, argv)
            results.append(result)
            if not result.passed and self._stop_on_first_failure:
                break
        return VerificationReport(results=tuple(results))

    def _run_one(self, command: str, argv: Sequence[str]) -> CommandResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - argv comes from the operator, not the model
                list(argv),
                cwd=self._cwd,
                env=self._env,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            # A gate that hangs is a failure with evidence attached: whatever it
            # managed to print before the deadline is usually where it stuck.
            return CommandResult(
                command=command,
                exit_code=-1,
                output=_decode(expired.stdout) + _decode(expired.stderr),
                duration_seconds=time.monotonic() - started,
                timed_out=True,
            )
        except OSError as error:
            return CommandResult(
                command=command,
                exit_code=-1,
                output=f"Could not execute {command!r}: {error}",
                duration_seconds=time.monotonic() - started,
            )

        return CommandResult(
            command=command,
            exit_code=completed.returncode,
            output=completed.stdout + completed.stderr,
            duration_seconds=time.monotonic() - started,
        )


def _parse(command: str) -> list[str]:
    """Split ``command`` the way a shell would, rejecting anything unrunnable."""
    try:
        argv = shlex.split(command)
    except ValueError as error:
        raise ValueError(f"{command!r} could not be parsed: {error}") from error
    if not argv:
        raise ValueError(f"{command!r} is not a runnable command.")
    return argv


def _decode(stream: str | bytes | None) -> str:
    """Normalise partial output captured from a timed-out process."""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream
