"""What the model is actually told."""

from __future__ import annotations

from tools.repair.transcript import (
    PATCH_PROTOCOL,
    SYSTEM_PROMPT,
    failure_prompt,
    initial_prompt,
    protocol_error_prompt,
    truncated_reply_prompt,
)
from tools.repair.verification import CommandResult, VerificationReport

FAILED = VerificationReport(
    results=(
        CommandResult(
            command="make check",
            exit_code=1,
            output="tests/unit/test_x.py::test_y FAILED",
            duration_seconds=4.2,
        ),
    )
)


def unwrapped(text: str) -> str:
    """Collapse the prompt's hard wrapping so assertions read as sentences."""
    return " ".join(text.split())


class TestSystemPrompt:
    def test_carries_the_operator_rules_verbatim(self) -> None:
        prompt = unwrapped(SYSTEM_PROMPT)
        assert "Do NOT simplify or replace custom experimental AI loops" in prompt
        assert "full, production-ready, non-stubbed files" in prompt
        assert "fix the exact errors reported by the local test runner" in prompt

    def test_forbids_weakening_the_gate(self) -> None:
        """Deleting the failing test is the shortest path to green and the wrong one."""
        assert "Weakening the verification gate is not a repair" in unwrapped(SYSTEM_PROMPT)

    def test_includes_the_patch_protocol(self) -> None:
        assert PATCH_PROTOCOL in SYSTEM_PROMPT

    def test_explains_that_blocks_replace_whole_files(self) -> None:
        assert "complete" in PATCH_PROTOCOL
        assert "```file:" in PATCH_PROTOCOL


class TestPrompts:
    def test_the_opening_turn_carries_the_gate_output(self) -> None:
        assert "test_y FAILED" in initial_prompt(FAILED, log_limit=10_000)

    def test_the_follow_up_names_the_attempt(self) -> None:
        assert "attempt 4" in failure_prompt(FAILED, log_limit=10_000, attempt=4)

    def test_the_follow_up_pushes_back_on_repeating_a_diagnosis(self) -> None:
        message = unwrapped(failure_prompt(FAILED, log_limit=10_000, attempt=2))
        assert "change your approach" in message

    def test_long_logs_are_trimmed_before_they_are_sent(self) -> None:
        noisy = VerificationReport(
            results=(
                CommandResult(
                    command="make check",
                    exit_code=1,
                    output="x" * 100_000,
                    duration_seconds=1.0,
                ),
            )
        )
        assert len(initial_prompt(noisy, log_limit=2_000)) < 5_000

    def test_the_protocol_error_echoes_what_arrived(self) -> None:
        message = protocol_error_prompt("never closed", reply="```file:a.py", log_limit=10_000)
        assert "never closed" in message
        assert "```file:a.py" in message

    def test_the_protocol_error_says_nothing_was_applied(self) -> None:
        """The model must not assume its patch half-landed."""
        message = protocol_error_prompt("boom", reply="...", log_limit=100)
        assert "Nothing was applied" in message

    def test_the_truncation_notice_asks_for_fewer_files(self) -> None:
        assert "fewer files" in unwrapped(truncated_reply_prompt())
