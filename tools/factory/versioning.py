"""Marketing versions and build numbers.

The two stores agree on one rule that matters more than any other: a build
number can never repeat or go backwards for a given version. Apple rejects the
upload outright; Play rejects the bundle. Both failures land at the end of a
long CI run, so the number is decided and checked here, before anything builds.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "BUILD_STRATEGIES",
    "BuildNumberError",
    "Strategy",
    "Version",
    "parse_strategy",
    "resolve_build_number",
]

Strategy = Literal["ci-run", "timestamp", "explicit"]

#: How a build number is arrived at.
#:
#: ``ci-run`` uses the CI run number: monotonic by construction, and it ties a
#: store build back to the run that produced it. ``timestamp`` uses UTC minutes
#: since an epoch, for building outside CI. ``explicit`` takes the operator's
#: number and only checks it.
BUILD_STRATEGIES: tuple[Strategy, ...] = ("ci-run", "timestamp", "explicit")

#: Environment variables carrying a CI run number, in preference order.
_CI_RUN_VARS = ("GITHUB_RUN_NUMBER", "CI_PIPELINE_IID", "BUILD_NUMBER")

#: 2020-01-01T00:00:00Z. Minutes since then stay comfortably inside the 32-bit
#: range Play requires for versionCode until well past 2100.
_EPOCH = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)

#: Play's hard ceiling on versionCode. Apple has no equivalent limit, so the
#: tighter of the two governs.
MAX_BUILD_NUMBER = 2_100_000_000

_SEMVER = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")


class BuildNumberError(Exception):
    """A build number could not be resolved, or would be rejected on upload."""


def parse_strategy(text: str) -> Strategy:
    """Narrow an operator-supplied string to a known strategy.

    Raises:
        BuildNumberError: if ``text`` names no strategy the factory implements.
    """
    if text not in BUILD_STRATEGIES:
        raise BuildNumberError(
            f"Unknown build strategy {text!r}. Choose one of: {', '.join(BUILD_STRATEGIES)}."
        )
    return text


@dataclass(frozen=True, slots=True, order=True)
class Version:
    """A marketing version, comparable so releases can be ordered."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> Version:
        """Parse ``MAJOR.MINOR.PATCH``.

        Raises:
            BuildNumberError: if ``text`` is not that shape.
        """
        match = _SEMVER.match(text.strip())
        if match is None:
            raise BuildNumberError(f"{text!r} is not a MAJOR.MINOR.PATCH version.")
        return cls(major=int(match["major"]), minor=int(match["minor"]), patch=int(match["patch"]))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def resolve_build_number(
    strategy: Strategy,
    *,
    explicit: int | None = None,
    previous: int | None = None,
    env: Mapping[str, str] | None = None,
    now: dt.datetime | None = None,
) -> int:
    """Decide the build number this run will ship.

    Args:
        strategy: One of :data:`BUILD_STRATEGIES`.
        explicit: The number, required by the ``explicit`` strategy.
        previous: The highest build number already uploaded, if known. The
            result is checked against it so the rejection happens here rather
            than after the archive is built.
        env: Environment to read CI variables from. Defaults to the process's.
        now: Clock for the ``timestamp`` strategy. Injected for tests.

    Returns:
        A positive integer, strictly greater than ``previous`` when given.

    Raises:
        BuildNumberError: if the strategy's input is missing, the number is out
            of range, or it would not be accepted as a new build.
    """
    environment = os.environ if env is None else env

    if strategy == "explicit":
        if explicit is None:
            raise BuildNumberError(
                "The 'explicit' strategy needs a build number; pass --build-number."
            )
        number = explicit
    elif strategy == "ci-run":
        number = _from_ci(environment)
    elif strategy == "timestamp":
        moment = now or dt.datetime.now(tz=dt.UTC)
        number = int((moment - _EPOCH).total_seconds() // 60)
    else:  # pragma: no cover - Strategy is a closed Literal
        raise BuildNumberError(f"Unknown strategy {strategy!r}.")

    if number < 1:
        raise BuildNumberError(f"Build number {number} must be positive.")
    if number > MAX_BUILD_NUMBER:
        raise BuildNumberError(
            f"Build number {number} exceeds Play's versionCode ceiling of {MAX_BUILD_NUMBER}."
        )
    if previous is not None and number <= previous:
        raise BuildNumberError(
            f"Build number {number} is not greater than the last uploaded build ({previous}). "
            "Both stores reject a repeated or lower build number."
        )
    return number


def _from_ci(env: Mapping[str, str]) -> int:
    """Read a run number from whichever CI variable is present."""
    for name in _CI_RUN_VARS:
        raw = env.get(name)
        if raw is None or not raw.strip():
            continue
        try:
            return int(raw.strip())
        except ValueError as error:
            raise BuildNumberError(f"{name}={raw!r} is not an integer.") from error

    raise BuildNumberError(
        "The 'ci-run' strategy found no run number "
        f"({', '.join(_CI_RUN_VARS)} are all unset). "
        "Use --build-strategy timestamp when building outside CI."
    )
