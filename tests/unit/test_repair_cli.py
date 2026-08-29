"""The command line: argument handling, exit codes, and what reaches the terminal."""

from __future__ import annotations

import shlex
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from anthropic.types import MessageParam

from tools.repair import cli
from tools.repair.client import ModelReply
from tools.repair.journal import JOURNAL_DIRNAME

GATE_SOURCE = """\
import pathlib, sys
state = pathlib.Path(__file__).parent / "state.txt"
if state.read_text().strip() != "done":
    sys.stderr.write("gate failure\\n")
    sys.exit(1)
"""

FIX = ModelReply(text="```file:state.txt\ndone\n```\n")


class ScriptedClient:
    model = "scripted"

    def __init__(self, *replies: ModelReply) -> None:
        self._replies = list(replies)
        self.turns = 0

    def complete(self, *, system: str, messages: Sequence[MessageParam]) -> ModelReply:
        self.turns += 1
        return self._replies.pop(0)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "gate.py").write_text(GATE_SOURCE)
    (tmp_path / "state.txt").write_text("broken\n")
    return tmp_path


@pytest.fixture
def gate_command(repo: Path) -> str:
    return f"{shlex.quote(sys.executable)} gate.py"


def install(monkeypatch: pytest.MonkeyPatch, client: ScriptedClient) -> None:
    """Replace the API-backed client with a scripted one."""
    monkeypatch.setattr(cli, "AnthropicModelClient", lambda **_kwargs: client)


class TestParser:
    def test_defaults_to_fifteen_attempts(self) -> None:
        """The operator's stated budget."""
        assert cli.build_parser().parse_args([]).max_attempts == 15

    def test_defaults_to_max_effort(self) -> None:
        assert cli.build_parser().parse_args([]).effort == "max"

    def test_defaults_to_opus_5(self) -> None:
        assert cli.build_parser().parse_args([]).model == "claude-opus-5"

    def test_verify_is_repeatable_and_ordered(self) -> None:
        args = cli.build_parser().parse_args(["--verify", "ruff check .", "--verify", "pytest"])
        assert args.verify == ["ruff check .", "pytest"]

    def test_rejects_an_unknown_effort(self) -> None:
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--effort", "maximum"])


class TestExitCodes:
    def test_zero_when_the_gate_ends_green(
        self, repo: Path, gate_command: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, ScriptedClient(FIX))
        code = cli.main(["--root", str(repo), "--verify", gate_command, "--no-journal"])
        assert code == cli.EXIT_REPAIRED
        assert (repo / "state.txt").read_text() == "done\n"

    def test_one_when_the_budget_runs_out(
        self, repo: Path, gate_command: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        noop = ModelReply(text="```file:state.txt\nstill broken\n```\n")
        install(monkeypatch, ScriptedClient(noop))
        code = cli.main(
            ["--root", str(repo), "--verify", gate_command, "--no-journal", "--max-attempts", "1"]
        )
        assert code == cli.EXIT_NOT_REPAIRED

    def test_two_when_the_root_is_not_a_directory(self, tmp_path: Path) -> None:
        assert cli.main(["--root", str(tmp_path / "nope")]) == cli.EXIT_UNUSABLE

    def test_two_when_the_attempt_budget_is_meaningless(self, repo: Path) -> None:
        assert cli.main(["--root", str(repo), "--max-attempts", "0"]) == cli.EXIT_UNUSABLE

    def test_two_when_a_gate_command_is_unrunnable(self, repo: Path) -> None:
        """Caught before the first turn, not fifteen minutes in."""
        assert cli.main(["--root", str(repo), "--verify", "  "]) == cli.EXIT_UNUSABLE


class TestOutput:
    def test_announces_success(
        self,
        repo: Path,
        gate_command: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        install(monkeypatch, ScriptedClient(FIX))
        cli.main(["--root", str(repo), "--verify", gate_command, "--no-journal"])
        assert "[SUCCESS]" in capsys.readouterr().out

    def test_announces_failure_and_points_at_the_journal(
        self,
        repo: Path,
        gate_command: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        install(monkeypatch, ScriptedClient(ModelReply(text="no idea")))
        cli.main(["--root", str(repo), "--verify", gate_command, "--max-attempts", "1"])
        output = capsys.readouterr().out
        assert "[FAIL]" in output
        assert JOURNAL_DIRNAME in output

    def test_errors_go_to_stderr(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cli.main(["--root", str(tmp_path / "nope")])
        captured = capsys.readouterr()
        assert "error:" in captured.err
        assert captured.out == ""


class TestDryRun:
    def test_lists_the_proposed_changes_without_writing(
        self,
        repo: Path,
        gate_command: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        install(monkeypatch, ScriptedClient(FIX))
        code = cli.main(["--root", str(repo), "--verify", gate_command, "--dry-run"])
        output = capsys.readouterr().out
        assert code == cli.EXIT_NOT_REPAIRED
        assert "state.txt" in output
        assert "not written" in output
        assert (repo / "state.txt").read_text() == "broken\n"

    def test_keeps_no_journal(
        self, repo: Path, gate_command: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, ScriptedClient(FIX))
        cli.main(["--root", str(repo), "--verify", gate_command, "--dry-run"])
        assert not (repo / JOURNAL_DIRNAME).exists()


class TestJournalling:
    def test_writes_a_run_directory_by_default(
        self, repo: Path, gate_command: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, ScriptedClient(FIX))
        cli.main(["--root", str(repo), "--verify", gate_command])
        runs = list((repo / JOURNAL_DIRNAME).iterdir())
        assert len(runs) == 1
        assert (runs[0] / "outcome.json").exists()

    def test_no_journal_leaves_the_tree_clean(
        self, repo: Path, gate_command: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install(monkeypatch, ScriptedClient(FIX))
        cli.main(["--root", str(repo), "--verify", gate_command, "--no-journal"])
        assert not (repo / JOURNAL_DIRNAME).exists()
