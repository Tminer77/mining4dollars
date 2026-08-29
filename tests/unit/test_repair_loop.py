"""The loop: what it asks, what it applies, and when it stops.

The model is scripted, but the filesystem and the gate are real — the gate here
is an actual subprocess whose exit status decides the run, which is the part of
the design worth testing rather than mocking.
"""

from __future__ import annotations

import shlex
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import MessageParam

from tools.repair.client import ModelClient, ModelError, ModelReply
from tools.repair.journal import Journal
from tools.repair.loop import RepairLoop
from tools.repair.verification import Verifier

#: The gate: passes only once ``state.txt`` reads "done".
GATE_SOURCE = """\
import pathlib, sys
state = pathlib.Path(__file__).parent / "state.txt"
current = state.read_text().strip() if state.exists() else "missing"
if current != "done":
    sys.stderr.write(f"gate failure: state is {current!r}, expected 'done'\\n")
    sys.exit(1)
"""


class ScriptedClient:
    """A :class:`ModelClient` that replays prepared replies and records the ask."""

    model = "scripted"

    def __init__(self, *replies: ModelReply | Exception) -> None:
        self._replies = list(replies)
        self.calls: list[list[MessageParam]] = []
        self.systems: list[str] = []

    def complete(self, *, system: str, messages: Sequence[MessageParam]) -> ModelReply:
        self.calls.append(list(messages))
        self.systems.append(system)
        if not self._replies:
            raise AssertionError("the loop asked for more turns than the test scripted")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def turns(self) -> int:
        return len(self.calls)


def write_state(value: str) -> ModelReply:
    """A reply whose patch sets the gate's state file to ``value``."""
    return ModelReply(text=f"Root cause: the state file.\n\n```file:state.txt\n{value}\n```\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tree whose gate fails until a patch fixes it."""
    (tmp_path / "gate.py").write_text(GATE_SOURCE)
    (tmp_path / "state.txt").write_text("broken\n")
    return tmp_path


@pytest.fixture
def verifier(repo: Path) -> Verifier:
    return Verifier(
        [f"{shlex.quote(sys.executable)} gate.py"], cwd=repo, timeout_seconds=30, env=None
    )


def build(client: ModelClient, verifier: Verifier, repo: Path, **kwargs: Any) -> RepairLoop:
    return RepairLoop(client=client, verifier=verifier, root=repo, **kwargs)


class TestTermination:
    def test_repairs_on_the_first_attempt(self, repo: Path, verifier: Verifier) -> None:
        client = ScriptedClient(write_state("done"))
        outcome = build(client, verifier, repo).run()
        assert outcome.repaired
        assert (repo / "state.txt").read_text() == "done\n"

    def test_keeps_going_until_the_gate_passes(self, repo: Path, verifier: Verifier) -> None:
        client = ScriptedClient(write_state("still wrong"), write_state("done"))
        outcome = build(client, verifier, repo).run()
        assert outcome.repaired
        assert client.turns == 2
        assert outcome.attempts_used == 2

    def test_gives_up_after_the_attempt_budget(self, repo: Path, verifier: Verifier) -> None:
        client = ScriptedClient(*[write_state("wrong") for _ in range(3)])
        outcome = build(client, verifier, repo, max_attempts=3).run()
        assert not outcome.repaired
        assert "after 3 attempts" in outcome.reason
        assert client.turns == 3

    def test_does_not_ask_the_model_when_the_gate_already_passes(
        self, repo: Path, verifier: Verifier
    ) -> None:
        """A max-effort turn on a healthy tree is pure cost and pure risk."""
        (repo / "state.txt").write_text("done\n")
        client = ScriptedClient()
        outcome = build(client, verifier, repo).run()
        assert outcome.repaired
        assert client.turns == 0

    def test_rejects_an_attempt_budget_below_one(self, repo: Path, verifier: Verifier) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            build(ScriptedClient(), verifier, repo, max_attempts=0).run()


class TestConversation:
    def test_the_first_turn_carries_the_failing_gate_output(
        self, repo: Path, verifier: Verifier
    ) -> None:
        client = ScriptedClient(write_state("done"))
        build(client, verifier, repo).run()
        assert "state is 'broken'" in str(client.calls[0][0]["content"])

    def test_the_operator_rules_are_in_the_system_prompt(
        self, repo: Path, verifier: Verifier
    ) -> None:
        """They must not erode as failure logs accumulate in the messages."""
        client = ScriptedClient(write_state("done"))
        build(client, verifier, repo).run()
        assert "Do NOT simplify" in client.systems[0]

    def test_a_later_turn_carries_the_new_failure_and_the_old_reply(
        self, repo: Path, verifier: Verifier
    ) -> None:
        client = ScriptedClient(write_state("first try"), write_state("done"))
        build(client, verifier, repo).run()
        second = client.calls[1]
        assert second[1]["role"] == "assistant"
        assert "state is 'first try'" in str(second[-1]["content"])

    def test_the_conversation_grows_by_two_messages_per_attempt(
        self, repo: Path, verifier: Verifier
    ) -> None:
        client = ScriptedClient(*[write_state("wrong") for _ in range(3)])
        build(client, verifier, repo, max_attempts=3).run()
        assert [len(call) for call in client.calls] == [1, 3, 5]


class TestUnusableReplies:
    def test_an_unparseable_reply_costs_an_attempt_but_changes_nothing(
        self, repo: Path, verifier: Verifier
    ) -> None:
        client = ScriptedClient(ModelReply(text="```file:a.py\nunterminated"), write_state("done"))
        outcome = build(client, verifier, repo).run()
        assert outcome.repaired
        assert not (repo / "a.py").exists()
        assert not outcome.attempts[0].applied

    def test_the_parser_error_goes_back_to_the_model(self, repo: Path, verifier: Verifier) -> None:
        client = ScriptedClient(ModelReply(text="```file:a.py\nunterminated"), write_state("done"))
        build(client, verifier, repo).run()
        assert "never closed" in str(client.calls[1][-1]["content"])

    def test_a_reply_with_no_blocks_is_sent_back_rather_than_ending_the_run(
        self, repo: Path, verifier: Verifier
    ) -> None:
        client = ScriptedClient(ModelReply(text="I am not sure."), write_state("done"))
        assert build(client, verifier, repo).run().repaired

    def test_a_patch_touching_a_forbidden_path_is_refused(
        self, repo: Path, verifier: Verifier
    ) -> None:
        client = ScriptedClient(
            ModelReply(text="```file:../escape.txt\nowned\n```\n"), write_state("done")
        )
        outcome = build(client, verifier, repo).run()
        assert outcome.repaired
        assert not (repo.parent / "escape.txt").exists()

    def test_a_truncated_reply_is_discarded_whole(self, repo: Path, verifier: Verifier) -> None:
        """A cut-off reply's last block is a fragment; applying it truncates a file."""
        truncated = ModelReply(
            text="```file:state.txt\ndone\n```\n```file:other.py\nhalf a fi",
            stop_reason="max_tokens",
        )
        client = ScriptedClient(truncated, write_state("done"))
        build(client, verifier, repo).run()
        assert not (repo / "other.py").exists()

    def test_the_model_is_told_its_reply_was_cut_off(self, repo: Path, verifier: Verifier) -> None:
        truncated = ModelReply(text="```file:state.txt\ndone\n```\n", stop_reason="max_tokens")
        client = ScriptedClient(truncated, write_state("done"))
        build(client, verifier, repo).run()
        assert "cut off" in str(client.calls[1][-1]["content"])


class TestGivingUp:
    def test_a_refusal_ends_the_run_immediately(self, repo: Path, verifier: Verifier) -> None:
        """A decline is a decision, not a transient failure; retrying only spends money."""
        client = ScriptedClient(ModelReply(text="", refusal="not doing that"))
        outcome = build(client, verifier, repo, max_attempts=5).run()
        assert not outcome.repaired
        assert "declined" in outcome.reason
        assert client.turns == 1

    def test_an_api_failure_ends_the_run_with_the_reason(
        self, repo: Path, verifier: Verifier
    ) -> None:
        client = ScriptedClient(ModelError("API returned 500"))
        outcome = build(client, verifier, repo).run()
        assert not outcome.repaired
        assert "500" in outcome.reason


class TestDryRun:
    def test_writes_nothing(self, repo: Path, verifier: Verifier) -> None:
        client = ScriptedClient(write_state("done"))
        build(client, verifier, repo, dry_run=True).run()
        assert (repo / "state.txt").read_text() == "broken\n"

    def test_stops_after_one_turn_and_reports_the_proposal(
        self, repo: Path, verifier: Verifier
    ) -> None:
        client = ScriptedClient(write_state("done"))
        outcome = build(client, verifier, repo, dry_run=True).run()
        assert client.turns == 1
        assert not outcome.repaired
        assert outcome.proposed is not None
        assert outcome.proposed.paths == ("state.txt",)


class TestJournal:
    def test_records_the_reply_and_the_verdict(self, repo: Path, verifier: Verifier) -> None:
        journal = Journal.create(repo)
        client = ScriptedClient(write_state("wrong"), write_state("done"))
        build(client, verifier, repo, journal=journal).run()
        names = {path.name for path in journal.directory.iterdir()}
        assert {"baseline.log", "attempt-01-reply.md", "attempt-02-reply.md"} <= names
        assert '"repaired": true' in (journal.directory / "outcome.json").read_text()

    def test_stores_the_full_gate_log_not_the_trimmed_one(
        self, repo: Path, verifier: Verifier
    ) -> None:
        journal = Journal.create(repo)
        client = ScriptedClient(write_state("wrong"), write_state("done"))
        build(client, verifier, repo, journal=journal, log_limit=10).run()
        assert "state is 'wrong'" in (journal.directory / "attempt-01-gate.log").read_text()


class TestObserver:
    def test_progress_goes_to_the_caller_not_to_stdout(
        self, repo: Path, verifier: Verifier, capsys: pytest.CaptureFixture[str]
    ) -> None:
        seen: list[str] = []
        client = ScriptedClient(write_state("done"))
        build(client, verifier, repo, observer=seen.append).run()
        assert any("attempt 1" in line for line in seen)
        assert capsys.readouterr().out == ""
