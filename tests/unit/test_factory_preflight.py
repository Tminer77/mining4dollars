"""The gate that runs before a runner spends thirty minutes on an archive."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.factory.preflight import Check, PreflightReport, Status, preflight
from tools.factory.spec import ANDROID_SECRETS, APPLE_SECRETS, FactorySpec, load_spec

APPLE = """\
[app]
name = "My App"
version = "1.0.0"

[apple]
bundle_id = "com.example.myapp"
project = "MyApp.xcodeproj"
scheme = "MyApp"
"""

ANDROID = """\
[app]
name = "My App"
version = "1.0.0"

[android]
package = "com.example.myapp"
module = "wear"
"""

CI_ENV = {"GITHUB_RUN_NUMBER": "42"}
APPLE_ENV = {**CI_ENV, **dict.fromkeys(APPLE_SECRETS, "secret-value-long-enough")}
ANDROID_ENV = {**CI_ENV, **dict.fromkeys(ANDROID_SECRETS, "secret-value-long-enough")}


def apple_spec(root: Path, body: str = APPLE) -> FactorySpec:
    (root / "factory.toml").write_text(body)
    (root / "MyApp.xcodeproj").mkdir(exist_ok=True)
    return load_spec(root)


def android_spec(root: Path, *, wrapper: bool = True, executable: bool = True) -> FactorySpec:
    (root / "factory.toml").write_text(ANDROID)
    if wrapper:
        gradlew = root / "gradlew"
        gradlew.write_text("#!/bin/sh\n")
        gradlew.chmod(0o755 if executable else 0o644)
    return load_spec(root)


def check(report: PreflightReport, name: str) -> Check:
    return next(c for c in report.checks if c.name == name)


class TestCredentials:
    def test_passes_when_every_secret_is_set(self, tmp_path: Path) -> None:
        report = preflight(apple_spec(tmp_path), "apple", env=APPLE_ENV)
        assert check(report, "credentials").status is Status.PASSED

    def test_fails_and_names_what_is_missing(self, tmp_path: Path) -> None:
        env = {**APPLE_ENV}
        del env["APP_STORE_CONNECT_KEY_ID"]
        result = check(preflight(apple_spec(tmp_path), "apple", env=env), "credentials")
        assert result.status is Status.FAILED
        assert "APP_STORE_CONNECT_KEY_ID" in result.detail

    def test_an_empty_secret_counts_as_missing(self, tmp_path: Path) -> None:
        """A secret that failed to interpolate arrives as an empty string."""
        env = {**APPLE_ENV, "APP_STORE_CONNECT_KEY_ID": "   "}
        result = check(preflight(apple_spec(tmp_path), "apple", env=env), "credentials")
        assert result.status is Status.FAILED

    def test_a_failure_carries_a_remedy(self, tmp_path: Path) -> None:
        result = check(preflight(apple_spec(tmp_path), "apple", env=CI_ENV), "credentials")
        assert "repository secrets" in result.remedy


class TestBuildNumber:
    def test_passes_with_a_ci_run_number(self, tmp_path: Path) -> None:
        result = check(preflight(apple_spec(tmp_path), "apple", env=APPLE_ENV), "build number")
        assert result.status is Status.PASSED
        assert "42" in result.detail

    def test_fails_when_it_would_be_rejected_at_upload(self, tmp_path: Path) -> None:
        report = preflight(apple_spec(tmp_path), "apple", env=APPLE_ENV, previous_build=99)
        assert check(report, "build number").status is Status.FAILED

    def test_fails_on_an_unknown_strategy(self, tmp_path: Path) -> None:
        report = preflight(apple_spec(tmp_path), "apple", env=APPLE_ENV, build_strategy="magic")
        assert check(report, "build number").status is Status.FAILED

    def test_blocks_the_whole_report(self, tmp_path: Path) -> None:
        report = preflight(apple_spec(tmp_path), "apple", env=APPLE_ENV, previous_build=99)
        assert not report.passed


class TestProjectPaths:
    def test_passes_when_the_project_exists(self, tmp_path: Path) -> None:
        report = preflight(apple_spec(tmp_path), "apple", env=APPLE_ENV)
        assert check(report, "Xcode project").status is Status.PASSED

    def test_fails_when_the_project_is_missing(self, tmp_path: Path) -> None:
        (tmp_path / "factory.toml").write_text(APPLE)
        report = preflight(load_spec(tmp_path), "apple", env=APPLE_ENV)
        assert check(report, "Xcode project").status is Status.FAILED

    def test_points_at_the_key_to_correct(self, tmp_path: Path) -> None:
        (tmp_path / "factory.toml").write_text(APPLE)
        result = check(preflight(load_spec(tmp_path), "apple", env=APPLE_ENV), "Xcode project")
        assert "[apple] project" in result.remedy


class TestRequiredPaths:
    def test_absent_when_none_are_declared(self, tmp_path: Path) -> None:
        report = preflight(apple_spec(tmp_path), "apple", env=APPLE_ENV)
        assert all(c.name != "required files" for c in report.checks)

    def test_fails_when_a_declared_file_is_missing(self, tmp_path: Path) -> None:
        body = APPLE.replace(
            'version = "1.0.0"', 'version = "1.0.0"\nrequired_paths = ["PrivacyInfo.xcprivacy"]'
        )
        result = check(
            preflight(apple_spec(tmp_path, body), "apple", env=APPLE_ENV), "required files"
        )
        assert result.status is Status.FAILED
        assert "PrivacyInfo.xcprivacy" in result.detail

    def test_passes_once_it_exists(self, tmp_path: Path) -> None:
        body = APPLE.replace(
            'version = "1.0.0"', 'version = "1.0.0"\nrequired_paths = ["PrivacyInfo.xcprivacy"]'
        )
        spec = apple_spec(tmp_path, body)
        (tmp_path / "PrivacyInfo.xcprivacy").write_text("")
        assert (
            check(preflight(spec, "apple", env=APPLE_ENV), "required files").status is Status.PASSED
        )


class TestToolchain:
    def test_is_skipped_not_passed_off_macos(self, tmp_path: Path) -> None:
        """A check that never ran must never read as a pass."""
        result = check(preflight(apple_spec(tmp_path), "apple", env=APPLE_ENV), "toolchain")
        assert result.status is Status.SKIPPED

    def test_a_skip_does_not_block_the_release(self, tmp_path: Path) -> None:
        assert preflight(apple_spec(tmp_path), "apple", env=APPLE_ENV).passed

    def test_a_skip_says_where_it_will_run(self, tmp_path: Path) -> None:
        result = check(preflight(apple_spec(tmp_path), "apple", env=APPLE_ENV), "toolchain")
        assert "macOS runner" in result.remedy


class TestAndroid:
    def test_passes_with_an_executable_wrapper(self, tmp_path: Path) -> None:
        report = preflight(android_spec(tmp_path), "android", env=ANDROID_ENV)
        assert report.passed

    def test_fails_without_a_wrapper(self, tmp_path: Path) -> None:
        report = preflight(android_spec(tmp_path, wrapper=False), "android", env=ANDROID_ENV)
        assert check(report, "gradle wrapper").status is Status.FAILED

    def test_a_non_executable_wrapper_fails_here_not_on_the_runner(self, tmp_path: Path) -> None:
        report = preflight(android_spec(tmp_path, executable=False), "android", env=ANDROID_ENV)
        result = check(report, "gradle wrapper")
        assert result.status is Status.FAILED
        assert "chmod=+x" in result.remedy

    def test_names_the_track_it_would_publish_to(self, tmp_path: Path) -> None:
        report = preflight(android_spec(tmp_path), "android", env=ANDROID_ENV)
        assert "internal" in check(report, "destination").detail


class TestReport:
    def test_renders_failures_with_their_remedies(self, tmp_path: Path) -> None:
        rendered = preflight(apple_spec(tmp_path), "apple", env=CI_ENV).render()
        assert "[FAIL]" in rendered
        assert "->" in rendered

    def test_collects_the_failures(self, tmp_path: Path) -> None:
        report = preflight(apple_spec(tmp_path), "apple", env=CI_ENV)
        assert [c.name for c in report.failures] == ["credentials"]

    def test_rejects_a_platform_the_spec_does_not_target(self, tmp_path: Path) -> None:
        from tools.factory.spec import SpecError

        with pytest.raises(SpecError):
            preflight(apple_spec(tmp_path), "android", env=APPLE_ENV)
