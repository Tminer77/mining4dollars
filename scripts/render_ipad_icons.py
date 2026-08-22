"""Render the M4D home-screen icons as PNG.

Kept as a script so the assets can be regenerated without a design tool.
The committed PNGs are what Safari actually installs.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "m4d" / "ipad" / "static" / "icons"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _png(width: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    raw = b"".join(
        b"\x00" + b"".join(bytes(pixels[y * width + x]) for x in range(width)) for y in range(width)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, width, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def _blend(
    dst: tuple[int, int, int, int], src: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    sa = src[3] / 255
    if sa <= 0:
        return dst
    out_a = sa + dst[3] / 255 * (1 - sa)
    if out_a <= 0:
        return (0, 0, 0, 0)
    rgb = tuple(round((src[i] * sa + dst[i] * (dst[3] / 255) * (1 - sa)) / out_a) for i in range(3))
    return (rgb[0], rgb[1], rgb[2], round(out_a * 255))


def _rect(
    pixels: list[tuple[int, int, int, int]],
    width: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int, int],
    radius: int = 0,
) -> None:
    for y in range(y0, y1):
        for x in range(x0, x1):
            if radius:
                dx = min(x - x0, x1 - 1 - x)
                dy = min(y - y0, y1 - 1 - y)
                if (
                    dx < radius
                    and dy < radius
                    and (radius - dx) ** 2 + (radius - dy) ** 2 > radius**2
                ):
                    continue
            pixels[y * width + x] = _blend(pixels[y * width + x], color)


def _glyph_m(size: int) -> list[tuple[int, int, int, int]]:
    """A block-letter M that scales cleanly at 180/192/512."""
    pixels = [(11, 11, 12, 255)] * (size * size)
    pad = int(size * 0.12)
    radius = int(size * 0.22)
    _rect(pixels, size, pad, pad, size - pad, size - pad, (22, 20, 15, 255), radius)

    gold = (227, 197, 122, 255)
    inset = int(size * 0.30)
    box = size - 2 * inset
    # 5x7 grid: left post, left shoulder, peak, right shoulder, right post.
    cells = {
        (0, 0),
        (0, 1),
        (0, 2),
        (0, 3),
        (0, 4),
        (0, 5),
        (0, 6),
        (1, 1),
        (1, 2),
        (2, 2),
        (2, 3),
        (3, 1),
        (3, 2),
        (4, 0),
        (4, 1),
        (4, 2),
        (4, 3),
        (4, 4),
        (4, 5),
        (4, 6),
    }
    cell_w = box / 5
    cell_h = box / 7
    for gx, gy in cells:
        x0 = inset + int(gx * cell_w)
        y0 = inset + int(gy * cell_h)
        x1 = inset + int((gx + 1) * cell_w)
        y1 = inset + int((gy + 1) * cell_h)
        _rect(pixels, size, x0, y0, x1, y1, gold)
    return pixels


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for size in (180, 192, 512):
        (ROOT / f"icon-{size}.png").write_bytes(_png(size, _glyph_m(size)))


if __name__ == "__main__":
    main()
