"""Clock implementations.

Reading the wall clock directly makes behaviour untestable and quietly
timezone-dependent. Time enters the system through this port instead.
"""

from __future__ import annotations

import datetime as dt

__all__ = ["FrozenClock", "SystemClock"]


class SystemClock:
    """The real clock. Always timezone-aware UTC."""

    def now(self) -> dt.datetime:
        """Return the current UTC time."""
        return dt.datetime.now(tz=dt.UTC)


class FrozenClock:
    """A clock stuck at a fixed instant, for deterministic tests.

    Lives in the source tree rather than the test suite so that any consumer of
    this package can test against it too.
    """

    def __init__(self, instant: dt.datetime) -> None:
        if instant.tzinfo is None:
            msg = "FrozenClock requires a timezone-aware datetime."
            raise ValueError(msg)
        self._instant = instant.astimezone(dt.UTC)

    def now(self) -> dt.datetime:
        """Return the fixed instant."""
        return self._instant

    def advance(self, delta: dt.timedelta) -> None:
        """Move the clock forward by ``delta``."""
        self._instant += delta
