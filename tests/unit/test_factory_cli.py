"""The command line: exit codes, and what reaches the terminal."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.factory import cli
from tools.factory.spec import ANDROID_SECRETS, APPLE_SECRETS, SPEC_FILENAME

APPLE = """\
[app]
name = "My App"
version = "2.1.0"

[apple]
bundle_id = "com.example.myapp"
project = "MyApp.xcodeproj"
scheme = "MyApp"
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / SPEC_FILENAME).write_text(APPLE)
    (tmp_path / "MyApp.xcodeproj").mkdir()
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "42")
    for name in APPLE_SECRETS:
        monkeypatch.setenv(name, "secret-value-long-enough")
    return tmp_path


class TestInit:
    def test_writes_a_starting_spec(self, tmp_path: Path) -> None:
        assert cli.main(["--root", str(tmp_path), "init"]) == cli.EXIT_OK
        assert (tmp_path / SPEC_FILENAME).is_file()

    def test_the_spec_it_writes_is_valid_once_filled_in(self, tmp_path: Path) -> None:
        from tools.factory.spec import load_spec

        cli.main(["--root", str(tmp_path), "init"])
        assert load_spec(tmp_path).name == "My App"

    def test_refuses_to_clobber_an_existing_spec(self, project: Path) -> None:
        assert cli.main(["--root", str(project), "init"]) == cli.EXIT_UNUSABLE

    def test_leaves_the_existing_spec_untouched(self, project: Path) -> None:
        cli.main(["--root", str(project), "init"])
        assert (project / SPEC_FILENAME).read_text() == APPLE


class TestPreflight:
    def test_passes_on_a_ready_project(self, project: Path) -> None:
        assert cli.main(["--root", str(project), "preflight"]) == cli.EXIT_OK

    def test_blocks_when_a_secret_is_missing(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("APP_STORE_CONNECT_KEY_ID")
        assert cli.main(["--root", str(project), "preflight"]) == cli.EXIT_BLOCKED

    def test_reports_the_skipped_checks(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.main(["--root", str(project), "preflight"])
        assert "skipped" in capsys.readouterr().out

    def test_prints_the_remedy_for_a_failure(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("APP_STORE_CONNECT_ISSUER_ID")
        cli.main(["--root", str(project), "preflight"])
        assert "repository secrets" in capsys.readouterr().out

    def test_defaults_to_every_configured_platform(self, project: Path) -> None:
        assert cli.main(["--root", str(project), "preflight"]) == cli.EXIT_OK


class TestPlan:
    def test_prints_the_steps_in_order(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["--root", str(project), "plan", "--platform", "apple"]) == cli.EXIT_OK
        output = capsys.readouterr().out
        assert "1. archive" in output
        assert "3. upload" in output

    def test_shows_the_version_and_build_it_would_ship(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.main(["--root", str(project), "plan", "--platform", "apple"])
        assert "2.1.0 (42)" in capsys.readouterr().out

    def test_marks_the_macos_only_steps(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.main(["--root", str(project), "plan", "--platform", "apple"])
        assert "[macOS only]" in capsys.readouterr().out

    def test_writes_nothing(self, project: Path) -> None:
        before = sorted(p.name for p in project.iterdir())
        cli.main(["--root", str(project), "plan", "--platform", "apple"])
        assert sorted(p.name for p in project.iterdir()) == before

    def test_shows_the_signing_flags_run_would_use(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A plan that omits what run would pass is worse than no plan."""
        key_path = str(project / "AuthKey_K.p8")
        monkeypatch.setenv("APP_STORE_CONNECT_KEY_PATH", key_path)
        cli.main(["--root", str(project), "plan", "--platform", "apple"])
        output = capsys.readouterr().out
        assert f"-authenticationKeyPath {key_path}" in output
        assert "--apiKey" in output

    def test_omits_the_signing_flags_when_no_key_is_configured(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Locally there is no key path; the developer's keychain signs."""
        cli.main(["--root", str(project), "plan", "--platform", "apple"])
        assert "-authenticationKeyPath" not in capsys.readouterr().out

    def test_fails_when_the_build_number_cannot_be_resolved(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_RUN_NUMBER")
        code = cli.main(["--root", str(project), "plan", "--platform", "apple"])
        assert code == cli.EXIT_UNUSABLE


class TestRun:
    def test_refuses_to_build_when_preflight_fails(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Nothing is built, so nothing has to be cleaned up."""
        monkeypatch.delenv("APP_STORE_CONNECT_KEY_ID")
        code = cli.main(["--root", str(project), "run", "--platform", "apple"])
        assert code == cli.EXIT_BLOCKED
        assert "nothing was built" in capsys.readouterr().err


class TestRunEndToEnd:
    """The Android path runs a real subprocess, so `run` is exercised whole."""

    @pytest.fixture
    def android(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        (tmp_path / SPEC_FILENAME).write_text(
            '[app]\nname = "My App"\nversion = "3.0.0"\n\n'
            '[android]\npackage = "com.example.myapp"\nmodule = "wear"\n'
        )
        # A wrapper that records what it was asked to do and succeeds.
        gradlew = tmp_path / "gradlew"
        gradlew.write_text('#!/bin/sh\necho "gradle: $*" >> "$(dirname "$0")/calls.log"\n')
        gradlew.chmod(0o755)

        monkeypatch.setenv("GITHUB_RUN_NUMBER", "88")
        for name in ANDROID_SECRETS:
            monkeypatch.setenv(name, "secret-value-long-enough")
        return tmp_path

    def test_succeeds_when_every_step_does(self, android: Path) -> None:
        assert cli.main(["--root", str(android), "run", "--platform", "android"]) == cli.EXIT_OK

    def test_runs_the_planned_commands_in_order(self, android: Path) -> None:
        cli.main(["--root", str(android), "run", "--platform", "android"])
        calls = (android / "calls.log").read_text().splitlines()
        assert ":wear:testReleaseUnitTest" in calls[0]
        assert ":wear:bundleRelease" in calls[1]

    def test_passes_the_resolved_version_and_build(self, android: Path) -> None:
        cli.main(["--root", str(android), "run", "--platform", "android"])
        assert "-PversionName=3.0.0 -PversionCode=88" in (android / "calls.log").read_text()

    def test_stops_at_the_first_failing_step(self, android: Path) -> None:
        gradlew = android / "gradlew"
        gradlew.write_text('#!/bin/sh\necho "gradle: $*" >> "$(dirname "$0")/calls.log"\nexit 1\n')
        gradlew.chmod(0o755)

        code = cli.main(["--root", str(android), "run", "--platform", "android"])

        assert code == cli.EXIT_BLOCKED
        assert len((android / "calls.log").read_text().splitlines()) == 1

    def test_prints_the_failing_step_output(
        self, android: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        gradlew = android / "gradlew"
        gradlew.write_text('#!/bin/sh\necho "compileReleaseKotlin FAILED" >&2\nexit 1\n')
        gradlew.chmod(0o755)

        cli.main(["--root", str(android), "run", "--platform", "android"])

        assert "compileReleaseKotlin FAILED" in capsys.readouterr().err


class TestErrors:
    def test_a_missing_root_is_unusable(self, tmp_path: Path) -> None:
        assert cli.main(["--root", str(tmp_path / "nope"), "preflight"]) == cli.EXIT_UNUSABLE

    def test_a_missing_spec_says_how_to_make_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["--root", str(tmp_path), "preflight"]) == cli.EXIT_UNUSABLE
        assert "tools.factory init" in capsys.readouterr().err

    def test_an_unconfigured_platform_says_what_is_configured(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = cli.main(["--root", str(project), "plan", "--platform", "android"])
        assert code == cli.EXIT_UNUSABLE
        assert "apple" in capsys.readouterr().err

    def test_errors_go_to_stderr(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cli.main(["--root", str(tmp_path), "preflight"])
        captured = capsys.readouterr()
        assert "error:" in captured.err
        assert captured.out == ""
