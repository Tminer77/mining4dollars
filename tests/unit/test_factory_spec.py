"""The release spec: what it accepts, and what it refuses to let through."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.factory.spec import (
    ANDROID_SECRETS,
    APPLE_SECRETS,
    SpecError,
    load_spec,
)

MINIMAL = """\
[app]
name = "My App"
version = "1.0.0"

[apple]
bundle_id = "com.example.myapp"
project = "MyApp.xcodeproj"
scheme = "MyApp"
"""

BOTH = (
    MINIMAL
    + """
[android]
package = "com.example.myapp"
module = "wear"
track = "beta"
"""
)


def write(root: Path, body: str) -> Path:
    (root / "factory.toml").write_text(body)
    return root


class TestLoading:
    def test_reads_the_app_section(self, tmp_path: Path) -> None:
        spec = load_spec(write(tmp_path, MINIMAL))
        assert (spec.name, spec.version) == ("My App", "1.0.0")

    def test_reports_only_the_platforms_configured(self, tmp_path: Path) -> None:
        assert load_spec(write(tmp_path, MINIMAL)).platforms == ("apple",)

    def test_reports_both_platforms_in_a_stable_order(self, tmp_path: Path) -> None:
        assert load_spec(write(tmp_path, BOTH)).platforms == ("apple", "android")

    def test_applies_apple_defaults(self, tmp_path: Path) -> None:
        apple = load_spec(write(tmp_path, MINIMAL)).apple
        assert apple is not None
        assert apple.track == "testflight"
        assert apple.export_method == "app-store"
        assert apple.secrets == APPLE_SECRETS

    def test_applies_android_defaults(self, tmp_path: Path) -> None:
        android = load_spec(write(tmp_path, BOTH)).android
        assert android is not None
        assert android.project_dir == "."
        assert android.secrets == ANDROID_SECRETS

    def test_derives_the_gradle_tasks_from_the_module(self, tmp_path: Path) -> None:
        android = load_spec(write(tmp_path, BOTH)).android
        assert android is not None
        assert android.bundle_task == ":wear:bundleRelease"
        assert android.test_task == ":wear:testReleaseUnitTest"

    def test_recognises_a_workspace(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("MyApp.xcodeproj", "MyApp.xcworkspace")
        apple = load_spec(write(tmp_path, body)).apple
        assert apple is not None
        assert apple.is_workspace

    def test_a_missing_file_says_how_to_make_one(self, tmp_path: Path) -> None:
        with pytest.raises(SpecError, match=r"tools\.factory init"):
            load_spec(tmp_path)

    def test_rejects_malformed_toml(self, tmp_path: Path) -> None:
        with pytest.raises(SpecError, match="not valid TOML"):
            load_spec(write(tmp_path, "[app\nname ="))

    def test_rejects_a_spec_targeting_nothing(self, tmp_path: Path) -> None:
        body = '[app]\nname = "X"\nversion = "1.0.0"\n'
        with pytest.raises(SpecError, match="targets no platforms"):
            load_spec(write(tmp_path, body))


class TestStrictness:
    """A silently ignored key is a setting the operator believes is in force."""

    def test_rejects_an_unknown_top_level_section(self, tmp_path: Path) -> None:
        with pytest.raises(SpecError, match="unknown key"):
            load_spec(write(tmp_path, MINIMAL + "\n[windows]\nfoo = 1\n"))

    def test_rejects_an_unknown_apple_key(self, tmp_path: Path) -> None:
        with pytest.raises(SpecError, match="unknown key"):
            load_spec(write(tmp_path, MINIMAL + 'teem = "ABC123"\n'))

    def test_names_the_keys_it_does_know(self, tmp_path: Path) -> None:
        with pytest.raises(SpecError, match="bundle_id"):
            load_spec(write(tmp_path, MINIMAL + 'teem = "ABC123"\n'))


class TestIdentifierValidation:
    @pytest.mark.parametrize(
        "bundle_id",
        ["myapp", "com..myapp", "com.example.", "1com.example.app", "com.example.my_app"],
    )
    def test_rejects_identifiers_the_stores_would(self, tmp_path: Path, bundle_id: str) -> None:
        body = MINIMAL.replace("com.example.myapp", bundle_id)
        with pytest.raises(SpecError, match="reverse-DNS"):
            load_spec(write(tmp_path, body))

    def test_says_the_identifier_is_permanent(self, tmp_path: Path) -> None:
        """The error has to carry the stakes: this cannot be changed later."""
        body = MINIMAL.replace("com.example.myapp", "nope")
        with pytest.raises(SpecError, match="cannot be changed"):
            load_spec(write(tmp_path, body))

    @pytest.mark.parametrize("bundle_id", ["com.example.myapp", "com.example.my-app.watch"])
    def test_accepts_well_formed_identifiers(self, tmp_path: Path, bundle_id: str) -> None:
        body = MINIMAL.replace("com.example.myapp", bundle_id)
        assert load_spec(write(tmp_path, body)).apple is not None


class TestVersionValidation:
    @pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0-beta", "1.0.0.1", ""])
    def test_rejects_versions_the_stores_would(self, tmp_path: Path, version: str) -> None:
        body = MINIMAL.replace('version = "1.0.0"', f'version = "{version}"')
        with pytest.raises(SpecError):
            load_spec(write(tmp_path, body))

    def test_accepts_a_plain_semver(self, tmp_path: Path) -> None:
        body = MINIMAL.replace('version = "1.0.0"', 'version = "12.4.31"')
        assert load_spec(write(tmp_path, body)).version == "12.4.31"


class TestConsistency:
    def test_rejects_an_export_method_that_cannot_be_uploaded(self, tmp_path: Path) -> None:
        """ad-hoc archives are rejected at upload, an hour into the run."""
        body = MINIMAL + 'export_method = "ad-hoc"\n'
        with pytest.raises(SpecError, match="requires export_method"):
            load_spec(write(tmp_path, body))

    def test_rejects_an_unknown_track(self, tmp_path: Path) -> None:
        body = MINIMAL + 'track = "nightly"\n'
        with pytest.raises(SpecError, match="must be one of"):
            load_spec(write(tmp_path, body))


class TestTargetLookup:
    def test_finds_a_configured_platform(self, tmp_path: Path) -> None:
        spec = load_spec(write(tmp_path, BOTH))
        assert spec.target_for("android").track == "beta"

    def test_an_unconfigured_platform_says_what_is_configured(self, tmp_path: Path) -> None:
        spec = load_spec(write(tmp_path, MINIMAL))
        with pytest.raises(SpecError, match="apple"):
            spec.target_for("android")
