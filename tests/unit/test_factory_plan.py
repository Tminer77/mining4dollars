"""The commands a release would run — reviewable before anything executes."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from tools.factory.plan import ARTIFACT_DIR, build_plan, export_options, write_export_options
from tools.factory.spec import FactorySpec, load_spec
from tools.factory.steps import Step

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


def spec_for(root: Path, body: str) -> FactorySpec:
    (root / "factory.toml").write_text(body)
    return load_spec(root)


def plan_for(root: Path, body: str, platform: str) -> list[Step]:
    return build_plan(spec_for(root, body), platform, version="1.2.3", build_number=57)


class TestApplePlan:
    def test_archives_exports_then_uploads(self, tmp_path: Path) -> None:
        names = [step.name for step in plan_for(tmp_path, APPLE, "apple")]
        assert names == ["archive", "export", "upload"]

    def test_stamps_the_version_as_a_build_setting(self, tmp_path: Path) -> None:
        """Passing it as a setting keeps CI's working tree clean."""
        archive = plan_for(tmp_path, APPLE, "apple")[0]
        assert "MARKETING_VERSION=1.2.3" in archive.argv
        assert "CURRENT_PROJECT_VERSION=57" in archive.argv

    def test_uses_project_for_an_xcodeproj(self, tmp_path: Path) -> None:
        assert "-project" in plan_for(tmp_path, APPLE, "apple")[0].argv

    def test_uses_workspace_for_an_xcworkspace(self, tmp_path: Path) -> None:
        body = APPLE.replace("MyApp.xcodeproj", "MyApp.xcworkspace")
        assert "-workspace" in plan_for(tmp_path, body, "apple")[0].argv

    def test_passes_the_configured_destination(self, tmp_path: Path) -> None:
        argv = plan_for(tmp_path, APPLE, "apple")[0].argv
        assert argv[argv.index("-destination") + 1] == "generic/platform=watchOS"

    def test_honours_an_overridden_destination(self, tmp_path: Path) -> None:
        body = APPLE + 'destination = "generic/platform=iOS"\n'
        argv = plan_for(tmp_path, body, "apple")[0].argv
        assert argv[argv.index("-destination") + 1] == "generic/platform=iOS"

    def test_every_step_is_marked_macos_only(self, tmp_path: Path) -> None:
        assert all(step.macos_only for step in plan_for(tmp_path, APPLE, "apple"))

    def test_artefacts_stay_inside_the_build_directory(self, tmp_path: Path) -> None:
        for step in plan_for(tmp_path, APPLE, "apple"):
            for argument in step.argv:
                if argument.startswith("build/"):
                    assert argument.startswith(ARTIFACT_DIR)

    def test_export_reads_the_generated_options(self, tmp_path: Path) -> None:
        export = plan_for(tmp_path, APPLE, "apple")[1]
        assert f"{ARTIFACT_DIR}/ExportOptions.plist" in export.argv


class TestAndroidPlan:
    def test_tests_before_bundling(self, tmp_path: Path) -> None:
        names = [step.name for step in plan_for(tmp_path, ANDROID, "android")]
        assert names == ["unit tests", "bundle"]

    def test_targets_the_configured_module(self, tmp_path: Path) -> None:
        bundle = plan_for(tmp_path, ANDROID, "android")[1]
        assert ":wear:bundleRelease" in bundle.argv

    def test_passes_version_and_code_to_gradle(self, tmp_path: Path) -> None:
        bundle = plan_for(tmp_path, ANDROID, "android")[1]
        assert "-PversionName=1.2.3" in bundle.argv
        assert "-PversionCode=57" in bundle.argv

    def test_runs_in_the_project_directory(self, tmp_path: Path) -> None:
        body = ANDROID + 'project_dir = "android"\n'
        assert all(step.cwd == "android" for step in plan_for(tmp_path, body, "android"))

    def test_no_step_needs_macos(self, tmp_path: Path) -> None:
        assert not any(step.macos_only for step in plan_for(tmp_path, ANDROID, "android"))


class TestExportOptions:
    def test_is_a_readable_plist(self, tmp_path: Path) -> None:
        target = spec_for(tmp_path, APPLE).apple
        assert target is not None
        assert isinstance(plistlib.loads(export_options(target)), dict)

    def test_carries_the_configured_export_method(self, tmp_path: Path) -> None:
        target = spec_for(tmp_path, APPLE).apple
        assert target is not None
        assert plistlib.loads(export_options(target))["method"] == "app-store"

    def test_leaves_versioning_to_the_build_settings(self, tmp_path: Path) -> None:
        """Xcode must not renumber the build we deliberately chose."""
        target = spec_for(tmp_path, APPLE).apple
        assert target is not None
        assert plistlib.loads(export_options(target))["manageAppVersionAndBuildNumber"] is False

    def test_is_written_where_the_export_step_looks(self, tmp_path: Path) -> None:
        spec = spec_for(tmp_path, APPLE)
        assert spec.apple is not None
        path = write_export_options(tmp_path, spec.apple)
        assert path == tmp_path / ARTIFACT_DIR / "ExportOptions.plist"
        assert plistlib.loads(path.read_bytes())["method"] == "app-store"


class TestUnconfiguredPlatform:
    def test_refuses_to_plan_one(self, tmp_path: Path) -> None:
        from tools.factory.spec import SpecError

        with pytest.raises(SpecError):
            plan_for(tmp_path, APPLE, "android")
