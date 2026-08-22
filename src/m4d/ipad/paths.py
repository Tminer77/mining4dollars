"""Locations of the packaged iPad console assets."""

from __future__ import annotations

from pathlib import Path

__all__ = ["static_directory"]


def static_directory() -> Path:
    """Return the directory that holds the console's static files.

    Resolved from this module so the app finds its assets whether it is
    running from a source checkout or an installed wheel.
    """
    return Path(__file__).resolve().parent / "static"
