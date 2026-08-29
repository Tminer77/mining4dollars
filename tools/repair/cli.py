"""Command line entry point for the repair loop.

Run as ``python -m tools.repair`` or ``make repair``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import get_args

import anthropic

from tools.repair.client import (
    DEFAULT_EFFORT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    AnthropicModelClient,
    Effort,
)
from tools.repair.journal import Journal
from tools.repair.loop import DEFAULT_MAX_ATTEMPTS, RepairLoop
from tools.repair.patch import FileDelete, Patch
from tools.repair.verification import (
    DEFAULT_COMMANDS,
    DEFAULT_LOG_LIMIT,
    DEFAULT_TIMEOUT_SECONDS,
    Verifier,
)

__all__ = ["build_parser", "main"]

#: 0 repaired, 1 not repaired, 2 the loop could not start.
EXIT_REPAIRED = 0
EXIT_NOT_REPAIRED = 1
EXIT_UNUSABLE = 2


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.repair",
        description=(
            "Repair the working tree until the verification gate passes, by "
            "asking Claude for whole-file patches and re-running the gate after "
            "each one."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root. Every write is confined to it. Default: the working directory.",
    )
    parser.add_argument(
        "--verify",
        action="append",
        metavar="COMMAND",
        help=(
            f"A gate command, repeatable and run in order. Default: {'; '.join(DEFAULT_COMMANDS)}"
        ),
    )
    parser.add_argument(
        "--all-gates",
        action="store_true",
        help="Run every gate command even after one fails, instead of stopping at the first.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=f"Per-command timeout. Default: {DEFAULT_TIMEOUT_SECONDS:.0f}.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"Patches to try before giving up. Default: {DEFAULT_MAX_ATTEMPTS}.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Default: {DEFAULT_MODEL}.")
    parser.add_argument(
        "--effort",
        choices=get_args(Effort),
        default=DEFAULT_EFFORT,
        help=f"How much reasoning the model spends per turn. Default: {DEFAULT_EFFORT}.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help=f"Output ceiling per turn. Default: {DEFAULT_MAX_TOKENS}.",
    )
    parser.add_argument(
        "--log-limit",
        type=int,
        default=DEFAULT_LOG_LIMIT,
        metavar="CHARS",
        help=(
            "Characters of gate output sent back per turn; the middle of a "
            f"longer log is elided. Default: {DEFAULT_LOG_LIMIT}."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ask for one patch, print what it would change, and write nothing.",
    )
    parser.add_argument(
        "--no-journal",
        action="store_true",
        help="Do not write the run's artefacts under .repair/.",
    )
    parser.add_argument("--verbose", action="store_true", help="Log at DEBUG.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the repair loop. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    root: Path = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return EXIT_UNUSABLE

    if args.max_attempts < 1:
        print("error: --max-attempts must be at least 1", file=sys.stderr)
        return EXIT_UNUSABLE

    commands = tuple(args.verify) if args.verify else DEFAULT_COMMANDS
    try:
        verifier = Verifier(
            commands,
            cwd=root,
            timeout_seconds=args.timeout,
            stop_on_first_failure=not args.all_gates,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        client = AnthropicModelClient(
            model=args.model, effort=args.effort, max_tokens=args.max_tokens
        )
    except anthropic.AnthropicError as error:
        # Credentials are resolved lazily by the SDK, so this fires on genuine
        # misconfiguration (a bad base URL, say) rather than on a missing key.
        print(f"error: could not construct the API client: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    journal = None if args.no_journal or args.dry_run else Journal.create(root)
    if journal is not None:
        print(f"Recording this run under {journal.directory}")

    loop = RepairLoop(
        client=client,
        verifier=verifier,
        root=root,
        max_attempts=args.max_attempts,
        journal=journal,
        dry_run=args.dry_run,
        log_limit=args.log_limit,
        observer=print,
    )
    outcome = loop.run()

    if outcome.proposed is not None:
        _print_proposal(outcome.proposed)

    print()
    if outcome.repaired:
        print(f"[SUCCESS] {_sentence(outcome.reason)}")
        return EXIT_REPAIRED
    print(f"[FAIL] {_sentence(outcome.reason)}")
    if journal is not None:
        print(f"Full transcript and gate logs: {journal.directory}")
    return EXIT_NOT_REPAIRED


def _sentence(reason: str) -> str:
    """Punctuate a reason that may already carry a full stop of its own."""
    return reason if reason.endswith((".", "!", "?")) else f"{reason}."


def _print_proposal(patch: Patch) -> None:
    """Show what a dry run would have written."""
    print("\nProposed changes (not written):")
    for change in patch.changes:
        kind = "delete" if isinstance(change, FileDelete) else "write"
        print(f"  {kind:>6}  {change.path}")


if __name__ == "__main__":  # pragma: no cover - exercised through __main__.py
    raise SystemExit(main())
