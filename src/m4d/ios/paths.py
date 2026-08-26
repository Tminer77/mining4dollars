"""Paths for the iOS Blueprint package."""

from __future__ import annotations

from pathlib import Path

__all__ = ["static_directory"]


def static_directory() -> Path:
    """Directory that holds the iOS web app files."""
    return Path(__file__).resolve().parent / "static"
