"""The gate: running commands, judging them, and rendering the failure."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from tools.repair.verification import (
    CommandResult,
    VerificationReport,
    Verifier,
    truncate,
)


def py(code: str) -> str:
    """A shell command running ``code`` under the interpreter running the suite.

    Quoted the way the verifier will unquote it, so these tests exercise the
    real ``shlex`` path rather than a simplified one.
    """
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


#: A command that always succeeds. Depends on nothing being installed.
OK = py("pass")


def failing(message: str = "boom", code: int = 1) -> str:
    return py(f'import sys;sys.stderr.write("{message}");sys.exit({code})')


class TestVerifier:
    def test_a_passing_command_passes(self, tmp_path: Path) -> None:
        assert Verifier([OK], cwd=tmp_path).run().passed

    def test_a_failing_command_fails(self, tmp_path: Path) -> None:
        assert not Verifier([failing()], cwd=tmp_path).run().passed

    def test_captures_both_streams(self, tmp_path: Path) -> None:
        command = py('import sys;print("out");sys.stderr.write("err")')
        result = Verifier([command], cwd=tmp_path).run().results[0]
        assert "out" in result.output
        assert "err" in result.output

    def test_records_the_exit_code(self, tmp_path: Path) -> None:
        assert Verifier([failing(code=3)], cwd=tmp_path).run().results[0].exit_code == 3

    def test_runs_in_the_given_directory(self, tmp_path: Path) -> None:
        command = py("import os;print(os.getcwd())")
        result = Verifier([command], cwd=tmp_path).run().results[0]
        assert str(tmp_path.resolve()) in result.output

    def test_stops_at_the_first_failure(self, tmp_path: Path) -> None:
        """Errors after the first are usually consequences of it."""
        report = Verifier([failing(), OK], cwd=tmp_path).run()
        assert len(report.results) == 1

    def test_runs_every_command_when_asked_to(self, tmp_path: Path) -> None:
        report = Verifier([failing(), OK], cwd=tmp_path, stop_on_first_failure=False).run()
        assert len(report.results) == 2

    def test_all_commands_must_pass(self, tmp_path: Path) -> None:
        assert not Verifier([OK, failing()], cwd=tmp_path).run().passed

    def test_a_timeout_is_a_failure_with_evidence(self, tmp_path: Path) -> None:
        command = py('import time;print("started",flush=True);time.sleep(30)')
        result = Verifier([command], cwd=tmp_path, timeout_seconds=0.5).run().results[0]
        assert result.timed_out
        assert not result.passed

    def test_an_unrunnable_command_is_a_failure_not_a_crash(self, tmp_path: Path) -> None:
        result = Verifier(["definitely-not-a-real-binary"], cwd=tmp_path).run().results[0]
        assert not result.passed
        assert "Could not execute" in result.output

    def test_rejects_an_empty_command_list(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="at least one command"):
            Verifier([], cwd=tmp_path)

    def test_rejects_a_blank_command_before_running_anything(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not a runnable command"):
            Verifier(["   "], cwd=tmp_path)

    def test_rejects_an_unbalanced_quote(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="could not be parsed"):
            Verifier(['pytest -k "broken'], cwd=tmp_path)


class TestReport:
    def test_an_empty_report_has_not_passed(self) -> None:
        """No evidence is not the same as evidence of success."""
        assert not VerificationReport(results=()).passed

    def test_names_the_first_failing_command(self) -> None:
        results = (
            CommandResult(command="lint", exit_code=0, output="", duration_seconds=0.1),
            CommandResult(command="types", exit_code=1, output="bad", duration_seconds=0.2),
            CommandResult(command="test", exit_code=1, output="also bad", duration_seconds=0.3),
        )
        failure = VerificationReport(results=results).first_failure
        assert failure is not None
        assert failure.command == "types"

    def test_failure_log_carries_the_output_of_what_failed(self) -> None:
        results = (
            CommandResult(command="lint", exit_code=0, output="quiet", duration_seconds=0.1),
            CommandResult(command="types", exit_code=1, output="error: bad", duration_seconds=0.2),
        )
        log = VerificationReport(results=results).failure_log()
        assert "error: bad" in log
        assert "$ types" in log
        assert "passed" in log  # the passing command is mentioned, not quoted in full


class TestTruncation:
    def test_short_text_is_untouched(self) -> None:
        assert truncate("short", 100) == "short"

    def test_a_limit_of_zero_disables_truncation(self) -> None:
        """The journal stores the whole log; only the model's copy is trimmed."""
        assert truncate("x" * 10_000, 0) == "x" * 10_000

    def test_keeps_both_ends(self) -> None:
        text = "HEAD" + "x" * 5_000 + "TAIL"
        trimmed = truncate(text, 200)
        assert trimmed.startswith("HEAD")
        assert trimmed.endswith("TAIL")

    def test_says_how_much_it_dropped(self) -> None:
        assert "elided" in truncate("x" * 5_000, 100)

    def test_result_is_bounded_by_the_limit_plus_the_marker(self) -> None:
        trimmed = truncate("x" * 100_000, 1_000)
        assert len(trimmed) < 1_200
