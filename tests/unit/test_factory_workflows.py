"""The release workflow templates, and installing them into an app repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tools.factory.cli import install_workflows

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "tools" / "factory" / "workflows"
TEMPLATES = sorted(TEMPLATE_DIR.glob("*.yml"))


def parsed(path: Path) -> dict[Any, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


class TestTemplates:
    def test_both_platforms_have_one(self) -> None:
        assert {path.name for path in TEMPLATES} == {
            "release-ios.yml",
            "release-android.yml",
        }

    @pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
    def test_is_valid_yaml(self, template: Path) -> None:
        """GitHub is otherwise the first thing to tell you it is not."""
        assert isinstance(parsed(template), dict)

    @pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
    def test_defines_exactly_one_job(self, template: Path) -> None:
        assert list(parsed(template)["jobs"]) == ["release"]

    @pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
    def test_is_manual_dispatch_only(self, template: Path) -> None:
        """A release should be a decision, not a side effect of a push."""
        # PyYAML reads the bare `on:` key as the boolean True.
        triggers = parsed(template)[True]
        assert list(triggers) == ["workflow_dispatch"]

    @pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
    def test_serialises_releases(self, template: Path) -> None:
        """Two concurrent uploads race for the same build number."""
        concurrency = parsed(template)["concurrency"]
        assert concurrency["cancel-in-progress"] is False

    @pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
    def test_preflights_before_building(self, template: Path) -> None:
        steps = parsed(template)["jobs"]["release"]["steps"]
        names = [step.get("name", "") for step in steps]
        assert names.index("Preflight") < names.index("Show the plan")

    @pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
    def test_takes_a_previous_build_input(self, template: Path) -> None:
        inputs = parsed(template)[True]["workflow_dispatch"]["inputs"]
        assert "previous_build" in inputs

    def test_ios_runs_on_macos(self) -> None:
        assert parsed(TEMPLATE_DIR / "release-ios.yml")["jobs"]["release"]["runs-on"].startswith(
            "macos"
        )

    def test_android_does_not_need_macos(self) -> None:
        assert (
            "macos"
            not in parsed(TEMPLATE_DIR / "release-android.yml")["jobs"]["release"]["runs-on"]
        )

    @pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
    def test_holds_no_literal_credentials(self, template: Path) -> None:
        """Every secret must arrive through the secrets context, never inline."""
        body = template.read_text(encoding="utf-8")
        assert "-----BEGIN" not in body
        assert "sk-" not in body


class TestInstalling:
    def test_writes_both_templates(self, tmp_path: Path) -> None:
        install_workflows(tmp_path)
        installed = {path.name for path in (tmp_path / ".github" / "workflows").iterdir()}
        assert installed == {"release-ios.yml", "release-android.yml"}

    def test_creates_the_directory(self, tmp_path: Path) -> None:
        install_workflows(tmp_path)
        assert (tmp_path / ".github" / "workflows").is_dir()

    def test_copies_them_verbatim(self, tmp_path: Path) -> None:
        install_workflows(tmp_path)
        for template in TEMPLATES:
            copied = tmp_path / ".github" / "workflows" / template.name
            assert copied.read_text() == template.read_text()

    def test_never_overwrites_a_tuned_workflow(self, tmp_path: Path) -> None:
        """Someone's working signing setup beats the template every time."""
        target = tmp_path / ".github" / "workflows"
        target.mkdir(parents=True)
        (target / "release-ios.yml").write_text("name: mine\n")

        install_workflows(tmp_path)

        assert (target / "release-ios.yml").read_text() == "name: mine\n"

    def test_reports_what_it_skipped(self, tmp_path: Path) -> None:
        target = tmp_path / ".github" / "workflows"
        target.mkdir(parents=True)
        (target / "release-ios.yml").write_text("name: mine\n")

        results = dict(install_workflows(tmp_path))

        assert results["release-ios.yml"] is None
        assert results["release-android.yml"] is not None
