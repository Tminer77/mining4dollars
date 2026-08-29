"""App factories: one repeatable pipeline per store.

A factory takes a project it did not write, checks that it can be released,
decides the build number, builds it, and hands the artefact to the store. It is
driven by ``factory.toml`` rather than by arguments, so a release is
reproducible from the repository instead of from someone's shell history.

The design principle is the one in :mod:`tools.repair`: the tool's exit status
decides, never an assumption. Nothing is reported as ready that was not
checked, and a check that cannot run in this environment says so rather than
passing by default.

Entry point: ``python -m tools.factory`` (see :mod:`tools.factory.cli`).
"""

from __future__ import annotations

from tools.factory.plan import build_plan, export_options, write_export_options
from tools.factory.preflight import Check, PreflightReport, Status, preflight
from tools.factory.spec import (
    AndroidTarget,
    AppleTarget,
    FactorySpec,
    SpecError,
    load_spec,
)
from tools.factory.steps import Step, StepResult, StepRunner, redact
from tools.factory.versioning import (
    BUILD_STRATEGIES,
    BuildNumberError,
    Version,
    parse_strategy,
    resolve_build_number,
)

__all__ = [
    "BUILD_STRATEGIES",
    "AndroidTarget",
    "AppleTarget",
    "BuildNumberError",
    "Check",
    "FactorySpec",
    "PreflightReport",
    "SpecError",
    "Status",
    "Step",
    "StepResult",
    "StepRunner",
    "Version",
    "build_plan",
    "export_options",
    "load_spec",
    "parse_strategy",
    "preflight",
    "redact",
    "resolve_build_number",
    "write_export_options",
]
