"""Running build steps, and never leaking what they were given to run with."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from tools.factory.steps import REDACTION, Step, StepRunner, redact

SECRET = "sk-live-abcdef0123456789"


def py(code: str) -> tuple[str, ...]:
    """A command running ``code`` under the interpreter running the suite."""
    return (sys.executable, "-c", code)


class TestRedaction:
    def test_masks_a_secret_value(self) -> None:
        assert redact(f"token={SECRET} sent", [SECRET]) == f"token={REDACTION} sent"

    def test_masks_every_occurrence(self) -> None:
        assert redact(f"{SECRET} {SECRET}", [SECRET]).count(REDACTION) == 2

    def test_leaves_unrelated_text_alone(self) -> None:
        assert redact("nothing to see", [SECRET]) == "nothing to see"

    def test_masks_the_longer_secret_whole(self) -> None:
        """A short secret inside a long one must not leave the tail readable."""
        short = "abcdef0123456789"
        scrubbed = redact(f"value={SECRET}", [short, SECRET])
        assert SECRET not in scrubbed
        assert short not in scrubbed

    def test_ignores_values_too_short_to_be_credentials(self) -> None:
        """Masking "true" or "1" would turn the log into noise."""
        assert redact("mode=true", ["true"]) == "mode=true"

    def test_handles_no_secrets(self) -> None:
        assert redact("plain", []) == "plain"


class TestRunner:
    def test_reports_success(self, tmp_path: Path) -> None:
        result = StepRunner(tmp_path, env={}).run_one(Step(name="ok", argv=py("pass")))
        assert result.passed

    def test_reports_the_exit_code(self, tmp_path: Path) -> None:
        step = Step(name="fail", argv=py("import sys;sys.exit(3)"))
        assert StepRunner(tmp_path, env={}).run_one(step).exit_code == 3

    def test_captures_both_streams(self, tmp_path: Path) -> None:
        step = Step(name="talk", argv=py('import sys;print("out");sys.stderr.write("err")'))
        output = StepRunner(tmp_path, env={}).run_one(step).output
        assert "out" in output
        assert "err" in output

    def test_runs_in_the_step_directory(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        step = Step(name="pwd", argv=py("import os;print(os.getcwd())"), cwd="sub")
        assert "sub" in StepRunner(tmp_path, env={}).run_one(step).output

    def test_an_unrunnable_step_fails_rather_than_raising(self, tmp_path: Path) -> None:
        result = StepRunner(tmp_path, env={}).run_one(Step(name="ghost", argv=("no-such-binary",)))
        assert not result.passed
        assert "Could not execute" in result.output

    def test_a_timeout_is_a_failure(self, tmp_path: Path) -> None:
        step = Step(name="hang", argv=py("import time;time.sleep(30)"))
        result = StepRunner(tmp_path, env={}, timeout_seconds=0.5).run_one(step)
        assert not result.passed
        assert "timed out" in result.output

    def test_passes_step_environment_through(self, tmp_path: Path) -> None:
        step = Step(
            name="env",
            argv=py('import os;print(os.environ["GREETING"])'),
            env={"GREETING": "hello"},
        )
        assert "hello" in StepRunner(tmp_path, env={}).run_one(step).output


class TestRunnerRedaction:
    """A step that echoes its environment must not turn a public log into a leak."""

    def test_scrubs_a_secret_the_step_printed(self, tmp_path: Path) -> None:
        step = Step(
            name="leaky",
            argv=py('import os;print(os.environ["API_KEY"])'),
            secrets=("API_KEY",),
        )
        result = StepRunner(tmp_path, env={"API_KEY": SECRET}).run_one(step)
        assert SECRET not in result.output
        assert REDACTION in result.output

    def test_scrubs_a_secret_echoed_inside_a_larger_message(self, tmp_path: Path) -> None:
        code = 'import os,sys;sys.stderr.write("auth failed for " + os.environ["API_KEY"])'
        step = Step(name="leaky", argv=py(code), secrets=("API_KEY",))
        result = StepRunner(tmp_path, env={"API_KEY": SECRET}).run_one(step)
        assert SECRET not in result.output


class TestOrdering:
    def test_runs_steps_in_order(self, tmp_path: Path) -> None:
        steps = [Step(name=str(n), argv=py(f"print({n})")) for n in range(3)]
        results = StepRunner(tmp_path, env={}).run(steps)
        assert [result.step.name for result in results] == ["0", "1", "2"]

    def test_stops_at_the_first_failure(self, tmp_path: Path) -> None:
        """There is no sense in which uploading proceeds after archiving failed."""
        steps = [
            Step(name="first", argv=py("pass")),
            Step(name="boom", argv=py("import sys;sys.exit(1)")),
            Step(name="never", argv=py("pass")),
        ]
        results = StepRunner(tmp_path, env={}).run(steps)
        assert [result.step.name for result in results] == ["first", "boom"]


class TestDisplay:
    def test_a_step_renders_as_a_typable_command(self) -> None:
        step = Step(name="archive", argv=("xcodebuild", "archive", "-scheme", "MyApp"))
        assert step.command == "xcodebuild archive -scheme MyApp"

    def test_the_rendered_command_matches_the_argv(self) -> None:
        step = Step(name="x", argv=("echo", "hello"))
        assert shlex.split(step.command) == list(step.argv)
