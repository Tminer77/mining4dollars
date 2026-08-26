"""Generate the home-screen PNG without a graphics library."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

__all__ = ["write_icons"]

# Gold on near-black — the Blueprint mark Safari puts on the home screen.
_GOLD = (212, 175, 55)
_INK = (18, 16, 12)


def _chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _solid_png(size: int, rgb: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def write_icons(directory: Path) -> None:
    """Write 180/192/512 PNG icons into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    for size in (180, 192, 512):
        (directory / f"icon-{size}.png").write_bytes(_solid_png(size, _GOLD))
    (directory / "favicon.svg").write_text(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180">'
            f'<rect width="180" height="180" rx="36" fill="rgb{_INK}"/>'
            f'<rect x="14" y="14" width="152" height="152" rx="28" fill="rgb{_GOLD}"/>'
            '<text x="90" y="122" text-anchor="middle" font-size="92" '
            'font-family="ui-rounded, system-ui, sans-serif" font-weight="700" '
            f'fill="rgb{_INK}">B</text></svg>\n'
        ),
        encoding="utf-8",
    )
