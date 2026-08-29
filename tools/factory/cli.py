"""Command line entry point for the app factories.

Four verbs, in the order a release uses them::

    python -m tools.factory init                    # write a starting factory.toml
    python -m tools.factory preflight               # can this ship? (both platforms)
    python -m tools.factory plan --platform apple   # what exactly would run
    python -m tools.factory run --platform apple    # do it

``preflight`` and ``plan`` are safe everywhere and need no credentials beyond
what they check for. ``run`` executes real build tooling and is normally the
runner's job, not a laptop's.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.factory.plan import ARTIFACT_DIR, AppleAuth, build_plan, write_export_options
from tools.factory.preflight import preflight
from tools.factory.spec import SPEC_FILENAME, AppleTarget, FactorySpec, SpecError, load_spec
from tools.factory.steps import StepRunner
from tools.factory.versioning import BuildNumberError, parse_strategy, resolve_build_number

__all__ = ["STARTER_SPEC", "build_parser", "install_workflows", "main"]

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_UNUSABLE = 2

STARTER_SPEC = """\
# What this repository ships, and where. Read by `python -m tools.factory`.

[app]
name = "My App"
version = "1.0.0"
# Files whose absence should stop a release. Optional.
# required_paths = ["PrivacyInfo.xcprivacy", "fastlane/metadata"]

[apple]
# Permanent once an App Store Connect record exists. Choose carefully.
bundle_id = "com.example.myapp"
project = "MyApp.xcodeproj"
scheme = "MyApp"
track = "testflight"                      # testflight | app-store
destination = "generic/platform=watchOS"  # or generic/platform=iOS

# [android]
# package = "com.example.myapp"
# module = "app"
# track = "internal"                      # internal | alpha | beta | production
"""


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.factory",
        description="Build and ship an app to the App Store or Google Play.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root holding factory.toml. Default: the working directory.",
    )

    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help=f"Write a starting {SPEC_FILENAME}.")
    init.add_argument(
        "--with-workflows",
        action="store_true",
        help="Also copy the release workflows into .github/workflows/.",
    )

    for name, help_text in (
        ("preflight", "Check whether a release can proceed."),
        ("plan", "Print the commands a release would run."),
        ("run", "Execute the release."),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument(
            "--platform",
            choices=("apple", "android", "all"),
            default="all" if name == "preflight" else None,
            required=name != "preflight",
            help="Which factory to use.",
        )
        sub.add_argument(
            "--build-strategy",
            default="ci-run",
            help="How the build number is chosen: ci-run, timestamp, or explicit. Default: ci-run.",
        )
        sub.add_argument(
            "--build-number", type=int, default=None, help="Required by --build-strategy explicit."
        )
        sub.add_argument(
            "--previous-build",
            type=int,
            default=None,
            help="Highest build number already uploaded. Checked so a rejection "
            "happens here rather than after the build.",
        )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the factory. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    root: Path = args.root.expanduser().resolve()

    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return EXIT_UNUSABLE

    if args.command == "init":
        return _init(root, with_workflows=args.with_workflows)

    try:
        spec = load_spec(root)
    except SpecError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    platforms = spec.platforms if args.platform == "all" else (args.platform,)
    unknown = [name for name in platforms if name not in spec.platforms]
    if unknown:
        print(
            f"error: {SPEC_FILENAME} does not target {', '.join(unknown)}; "
            f"it targets {', '.join(spec.platforms)}",
            file=sys.stderr,
        )
        return EXIT_UNUSABLE

    if args.command == "preflight":
        return _preflight(spec, platforms, args)
    if args.command == "plan":
        return _plan(spec, platforms[0], args)
    return _run(spec, platforms[0], args)


def _init(root: Path, *, with_workflows: bool = False) -> int:
    """Write a starting spec, refusing to clobber one that exists."""
    path = root / SPEC_FILENAME
    if path.exists():
        print(f"error: {path} already exists; edit it rather than re-initialising", file=sys.stderr)
        return EXIT_UNUSABLE
    path.write_text(STARTER_SPEC, encoding="utf-8")
    print(f"Wrote {path}. Fill in the bundle id, project and scheme, then run:")
    print("  python -m tools.factory preflight")

    if with_workflows:
        for name, destination in install_workflows(root):
            print(f"Wrote {destination}" if destination else f"Skipped {name} (already present)")
    return EXIT_OK


def install_workflows(root: Path) -> list[tuple[str, Path | None]]:
    """Copy the release workflow templates into ``root/.github/workflows``.

    An existing file is never overwritten: a workflow someone has already
    tuned to their signing setup is more valuable than the template.
    """
    source = Path(__file__).parent / "workflows"
    target_dir = root / ".github" / "workflows"
    target_dir.mkdir(parents=True, exist_ok=True)

    written: list[tuple[str, Path | None]] = []
    for template in sorted(source.glob("*.yml")):
        destination = target_dir / template.name
        if destination.exists():
            written.append((template.name, None))
            continue
        destination.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        written.append((template.name, destination))
    return written


def _preflight(spec: FactorySpec, platforms: Sequence[str], args: argparse.Namespace) -> int:
    reports = [
        preflight(
            spec,
            platform,
            build_strategy=args.build_strategy,
            explicit_build=args.build_number,
            previous_build=args.previous_build,
        )
        for platform in platforms
    ]
    for report in reports:
        print(report.render())
        print()

    if any(not report.passed for report in reports):
        print("Not ready to ship. Every FAIL above carries the fix.")
        return EXIT_BLOCKED

    skipped = sum(len(report.skipped) for report in reports)
    note = f" ({skipped} check(s) skipped — they run on the build machine)" if skipped else ""
    print(f"Ready to ship{note}.")
    return EXIT_OK


def _resolve(spec: FactorySpec, args: argparse.Namespace) -> tuple[str, int] | None:
    """Work out the version and build number, or report why we cannot."""
    try:
        number = resolve_build_number(
            parse_strategy(args.build_strategy),
            explicit=args.build_number,
            previous=args.previous_build,
        )
    except BuildNumberError as error:
        print(f"error: {error}", file=sys.stderr)
        return None
    return spec.version, number


def _apple_auth() -> AppleAuth | None:
    """The signing credential, if the environment carries a complete one.

    Absent locally, where the developer's own keychain signs; present on a
    runner, which has no certificates of its own.
    """
    path = os.environ.get("APP_STORE_CONNECT_KEY_PATH", "").strip()
    key_id = os.environ.get("APP_STORE_CONNECT_KEY_ID", "").strip()
    issuer = os.environ.get("APP_STORE_CONNECT_ISSUER_ID", "").strip()
    if not (path and key_id and issuer):
        return None
    return AppleAuth(key_path=path, key_id=key_id, issuer_id=issuer)


def _plan(spec: FactorySpec, platform: str, args: argparse.Namespace) -> int:
    resolved = _resolve(spec, args)
    if resolved is None:
        return EXIT_UNUSABLE
    version, number = resolved

    steps = build_plan(
        spec, platform, version=version, build_number=number, apple_auth=_apple_auth()
    )
    print(f"{spec.name} {version} ({number}) -> {platform}\n")
    for index, step in enumerate(steps, 1):
        marker = " [macOS only]" if step.macos_only else ""
        print(f"{index}. {step.name}{marker}")
        print(f"   cd {step.cwd} && {step.command}")
        if step.secrets:
            print(f"   needs: {', '.join(step.secrets)}")
    return EXIT_OK


def _run(spec: FactorySpec, platform: str, args: argparse.Namespace) -> int:
    """Preflight, then execute the plan, stopping at the first failure."""
    report = preflight(
        spec,
        platform,
        build_strategy=args.build_strategy,
        explicit_build=args.build_number,
        previous_build=args.previous_build,
    )
    if not report.passed:
        print(report.render(), file=sys.stderr)
        print("\nerror: preflight failed; nothing was built", file=sys.stderr)
        return EXIT_BLOCKED

    resolved = _resolve(spec, args)
    if resolved is None:
        return EXIT_UNUSABLE
    version, number = resolved

    target = spec.target_for(platform)
    if isinstance(target, AppleTarget):
        # The export step reads this; generating it keeps it in step with the spec.
        write_export_options(spec.root, target)

    steps = build_plan(
        spec, platform, version=version, build_number=number, apple_auth=_apple_auth()
    )
    print(f"{spec.name} {version} ({number}) -> {platform}: {len(steps)} step(s)")

    results = StepRunner(spec.root).run(steps)
    for result in results:
        verdict = "ok" if result.passed else f"FAILED (exit {result.exit_code})"
        print(f"  {result.step.name}: {verdict} in {result.duration_seconds:.1f}s")

    failure = next((result for result in results if not result.passed), None)
    if failure is not None:
        print(f"\n--- {failure.step.name} ---", file=sys.stderr)
        print(failure.output, file=sys.stderr)
        return EXIT_BLOCKED

    print(f"\nShipped. Artefacts under {ARTIFACT_DIR}/.")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through __main__.py
    raise SystemExit(main())
