"""The gate that runs before anything is built.

A release pipeline fails expensively. An archive is thirty minutes of macOS
runner time, and the errors that waste it — an unset secret, a build number
already used, a scheme that does not exist — are all knowable in under a
second. Every check here is one of those: cheap, decided locally, and carrying
the remedy in its own result rather than in documentation someone has to find.

Checks that genuinely cannot be answered in this environment (whether
``xcodebuild`` will accept a scheme, say, when running on Linux) report
:data:`Status.SKIPPED` rather than guessing. A skipped check is honest; a
passing one that was never run is how a gate stops meaning anything.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from tools.factory.spec import AndroidTarget, AppleTarget, FactorySpec
from tools.factory.versioning import BuildNumberError, parse_strategy, resolve_build_number

__all__ = ["Check", "PreflightReport", "Status", "preflight"]


class Status(Enum):
    """The outcome of one check."""

    PASSED = "passed"
    FAILED = "failed"
    #: Not answerable here — a macOS-only check on Linux, say. Never a pass.
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Check:
    """One preflight result."""

    name: str
    status: Status
    detail: str
    #: What to do about it. Empty when the check passed.
    remedy: str = ""

    @property
    def ok(self) -> bool:
        return self.status is not Status.FAILED


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Every check that ran, and whether the release may proceed."""

    platform: str
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        """True when nothing failed. Skipped checks do not block."""
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status is Status.FAILED)

    @property
    def skipped(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status is Status.SKIPPED)

    def render(self) -> str:
        """A human-readable report, remedies included."""
        symbols = {Status.PASSED: "PASS", Status.FAILED: "FAIL", Status.SKIPPED: "SKIP"}
        lines = [f"Preflight — {self.platform}"]
        for check in self.checks:
            lines.append(f"  [{symbols[check.status]}] {check.name}: {check.detail}")
            if check.remedy:
                lines.append(f"         -> {check.remedy}")
        return "\n".join(lines)


def preflight(
    spec: FactorySpec,
    platform: str,
    *,
    build_strategy: str = "ci-run",
    explicit_build: int | None = None,
    previous_build: int | None = None,
    env: Mapping[str, str] | None = None,
) -> PreflightReport:
    """Run every check that applies to ``platform``.

    Args:
        spec: The release spec.
        platform: ``"apple"`` or ``"android"``.
        build_strategy: How the build number is resolved; checked here so an
            unset CI variable fails now rather than after the build.
        explicit_build: Build number, for the ``explicit`` strategy.
        previous_build: Highest build number already uploaded, when known.
        env: Environment to read secrets from. Defaults to the process's.

    Raises:
        SpecError: if the spec does not target ``platform``.
    """
    target = spec.target_for(platform)
    environment = dict(os.environ if env is None else env)

    checks: list[Check] = [
        _check_secrets(target.secrets, environment),
        _check_build_number(build_strategy, explicit_build, previous_build, environment),
        *_check_required_paths(spec),
    ]

    if isinstance(target, AppleTarget):
        checks.extend(_apple_checks(spec, target))
    else:
        checks.extend(_android_checks(spec, target))

    return PreflightReport(platform=platform, checks=tuple(checks))


def _check_secrets(names: Sequence[str], env: Mapping[str, str]) -> Check:
    """Every credential must be present and non-empty."""
    missing = [name for name in names if not env.get(name, "").strip()]
    if missing:
        return Check(
            name="credentials",
            status=Status.FAILED,
            detail=f"{len(missing)} of {len(names)} required secrets are unset: "
            + ", ".join(missing),
            remedy="Add them as repository secrets and pass them into the job's env. "
            "Never commit them, and never echo them in a step.",
        )
    return Check(
        name="credentials",
        status=Status.PASSED,
        detail=f"all {len(names)} required secrets are present",
    )


def _check_build_number(
    strategy: str, explicit: int | None, previous: int | None, env: Mapping[str, str]
) -> Check:
    """The number must resolve, and must be one the store will accept."""
    try:
        number = resolve_build_number(
            parse_strategy(strategy), explicit=explicit, previous=previous, env=env
        )
    except BuildNumberError as error:
        return Check(
            name="build number",
            status=Status.FAILED,
            detail=str(error),
            remedy="Left unresolved, this fails at upload — after the build.",
        )

    against = f", ahead of the last uploaded build ({previous})" if previous is not None else ""
    return Check(
        name="build number",
        status=Status.PASSED,
        detail=f"{number} via the {strategy!r} strategy{against}",
    )


def _check_required_paths(spec: FactorySpec) -> list[Check]:
    """Operator-declared paths that must exist for the release to be complete."""
    if not spec.required_paths:
        return []

    missing = [path for path in spec.required_paths if not (spec.root / path).exists()]
    if missing:
        return [
            Check(
                name="required files",
                status=Status.FAILED,
                detail="missing: " + ", ".join(missing),
                remedy="These are listed in [app] required_paths. Add the files, "
                "or drop them from the spec if they are no longer needed.",
            )
        ]
    return [
        Check(
            name="required files",
            status=Status.PASSED,
            detail=f"all {len(spec.required_paths)} declared paths exist",
        )
    ]


def _apple_checks(spec: FactorySpec, target: AppleTarget) -> list[Check]:
    checks = [_check_path(spec.root, target.project, "Xcode project", "[apple] project")]

    if sys.platform != "darwin":
        checks.append(
            Check(
                name="toolchain",
                status=Status.SKIPPED,
                detail=f"xcodebuild cannot be checked on {sys.platform}",
                remedy="The archive step needs a macOS runner. This check runs there.",
            )
        )
    elif shutil.which("xcodebuild") is None:  # pragma: no cover - needs macOS
        checks.append(
            Check(
                name="toolchain",
                status=Status.FAILED,
                detail="xcodebuild is not on PATH",
                remedy="Install Xcode and run `xcode-select --install`.",
            )
        )
    else:  # pragma: no cover - needs macOS
        checks.append(
            Check(name="toolchain", status=Status.PASSED, detail="xcodebuild is available")
        )

    checks.append(
        Check(
            name="destination",
            status=Status.PASSED,
            detail=f"{target.track} via {target.export_method}, building {target.destination}",
        )
    )
    return checks


def _android_checks(spec: FactorySpec, target: AndroidTarget) -> list[Check]:
    project_dir = spec.root / target.project_dir
    checks = [
        _check_path(spec.root, target.project_dir, "Gradle project", "[android] project_dir"),
    ]

    wrapper = project_dir / "gradlew"
    if not wrapper.is_file():
        checks.append(
            Check(
                name="gradle wrapper",
                status=Status.FAILED,
                detail=f"no gradlew in {target.project_dir}",
                remedy="Commit the Gradle wrapper (`gradle wrapper`) so CI builds with "
                "the same Gradle version you do.",
            )
        )
    elif not os.access(wrapper, os.X_OK):
        checks.append(
            Check(
                name="gradle wrapper",
                status=Status.FAILED,
                detail="gradlew is not executable",
                remedy="Run `git update-index --chmod=+x gradlew` and commit; a "
                "non-executable wrapper fails only on the runner.",
            )
        )
    else:
        checks.append(
            Check(name="gradle wrapper", status=Status.PASSED, detail="gradlew is executable")
        )

    checks.append(
        Check(
            name="destination",
            status=Status.PASSED,
            detail=f"{target.package} to the {target.track} track via {target.bundle_task}",
        )
    )
    return checks


def _check_path(root: Path, relative: str, label: str, source: str) -> Check:
    """One declared path must exist, or the build cannot start."""
    if (root / relative).exists():
        return Check(name=label, status=Status.PASSED, detail=f"found {relative}")
    return Check(
        name=label,
        status=Status.FAILED,
        detail=f"{relative} does not exist under {root}",
        remedy=f"Correct {source} in factory.toml, or check out the project first.",
    )
