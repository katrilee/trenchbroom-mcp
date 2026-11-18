#!/usr/bin/env python3
"""Generate a GoldSource WAD2 with procedural nature textures.

    python3 tools/wadgen.py --out /root/hlds/cstrike/mentzer_nature.wad

Textures are seamless (all noise wraps), 8-bit paletted, power-of-two, and named
the way a mapper expects: GRASS, GRASS_DARK, DIRT, ROCK, CLIFF, BARK, LEAVES,
WATER, PATH, MOSS, SKY_DAY.
"""
from __future__ import annotations

import argparse
import math
import random
import struct
from pathlib import Path

from PIL import Image

TYP_MIPTEX = 0x44


# ---------------------------------------------------------------------------
# noise
# ---------------------------------------------------------------------------


def _value_noise(size: int, cells: int, seed: int):
    """Tileable value noise in [0,1]."""
    rng = random.Random(seed)
    grid = [[rng.random() for _ in range(cells)] for _ in range(cells)]

    def smooth(t: float) -> float:
        return t * t * (3 - 2 * t)

    out = [[0.0] * size for _ in range(size)]
    for y in range(size):
        fy = y / size * cells
        y0 = int(fy) % cells
        y1 = (y0 + 1) % cells
        ty = smooth(fy - int(fy))
        for x in range(size):
            fx = x / size * cells
            x0 = int(fx) % cells
            x1 = (x0 + 1) % cells
            tx = smooth(fx - int(fx))
            v = (
                grid[y0][x0] * (1 - tx) * (1 - ty)
                + grid[y0][x1] * tx * (1 - ty)
                + grid[y1][x0] * (1 - tx) * ty
                + grid[y1][x1] * tx * ty
            )
            out[y][x] = v
    return out


def _fbm(size: int, seed: int, octaves: int = 4, base_cells: int = 4):
    acc = [[0.0] * size for _ in range(size)]
    amp, cells, total = 1.0, base_cells, 0.0
    for o in range(octaves):
        layer = _value_noise(size, max(1, cells), seed + o * 131)
        for y in range(size):
            row, lrow = acc[y], layer[y]
            for x in range(size):
                row[x] += lrow[x] * amp
        total += amp
        amp *= 0.5
        cells *= 2
    for y in range(size):
        for x in range(size):
            acc[y][x] /= total
    return acc


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ---------------------------------------------------------------------------
# textures
# ---------------------------------------------------------------------------


def grass(size: int, seed: int, dark: bool = False) -> Image.Image:
    base = (38, 92, 34) if dark else (58, 124, 46)
    light = (86, 150, 58) if dark else (118, 178, 72)
    n = _fbm(size, seed, octaves=5, base_cells=6)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            v = n[y][x]
            c = _mix(base, light, v * v)
            # sparse blade highlights, wrapped so the texture still tiles
            if ((x * 7 + y * 13 + seed) % 37) == 0:
                c = _mix(c, (170, 210, 110), 0.55)
            px[x, y] = c
    return img


def dirt(size: int, seed: int) -> Image.Image:
    n = _fbm(size, seed, octaves=4, base_cells=5)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            c = _mix((74, 54, 36), (128, 96, 62), n[y][x])
            if ((x * 11 + y * 5 + seed) % 53) == 0:
                c = _mix(c, (150, 120, 88), 0.7)
            px[x, y] = c
    return img


def rock(size: int, seed: int) -> Image.Image:
    n = _fbm(size, seed, octaves=5, base_cells=4)
    ridge = _fbm(size, seed + 7, octaves=3, base_cells=9)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            v = n[y][x] * 0.7 + ridge[y][x] * 0.3
            c = _mix((72, 70, 66), (146, 142, 134), v)
            # thin dark cracks
            if abs(ridge[y][x] - 0.5) < 0.012:
                c = _mix(c, (40, 40, 38), 0.8)
            px[x, y] = c
    return img


def cliff(size: int, seed: int) -> Image.Image:
    n = _fbm(size, seed, octaves=4, base_cells=3)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            band = 0.5 + 0.5 * math.sin((y / size) * 12 * math.pi + n[y][x] * 6)
            c = _mix((84, 76, 66), (132, 120, 102), band * 0.8 + n[y][x] * 0.2)
            px[x, y] = c
    return img


def bark(size: int, seed: int) -> Image.Image:
    n = _fbm(size, seed, octaves=4, base_cells=3)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            streak = 0.5 + 0.5 * math.sin(x / size * 18 * math.pi + n[y][x] * 8)
            c = _mix((58, 40, 26), (112, 82, 52), streak * 0.7 + n[y][x] * 0.3)
            px[x, y] = c
    return img


def leaves(size: int, seed: int) -> Image.Image:
    n = _fbm(size, seed, octaves=4, base_cells=7)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            v = n[y][x]
            c = _mix((28, 74, 30), (96, 168, 62), v)
            if v > 0.72:
                c = _mix(c, (140, 200, 84), 0.6)
            px[x, y] = c
    return img


def water(size: int, seed: int) -> Image.Image:
    n = _fbm(size, seed, octaves=3, base_cells=5)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            ripple = 0.5 + 0.5 * math.sin((x / size) * 10 * math.pi + n[y][x] * 9)
            c = _mix((26, 58, 96), (74, 132, 176), ripple * 0.8 + n[y][x] * 0.2)
            px[x, y] = c
    return img


def path(size: int, seed: int) -> Image.Image:
    n = _fbm(size, seed, octaves=4, base_cells=8)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            v = n[y][x]
            c = _mix((96, 88, 74), (168, 158, 138), v)
            if ((x * 13 + y * 29 + seed) % 61) == 0:
                c = _mix(c, (200, 195, 180), 0.8)
            px[x, y] = c
    return img


def moss(size: int, seed: int) -> Image.Image:
    n = _fbm(size, seed, octaves=5, base_cells=6)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            v = n[y][x]
            c = _mix((34, 62, 30), (92, 132, 56), v)
            px[x, y] = c
    return img


def sky_day(size: int, seed: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        t = y / size
        c = _mix((150, 200, 240), (58, 120, 200), t)
        for x in range(size):
            px[x, y] = c
    return img


TEXTURES = {
    "GRASS": lambda s, seed: grass(s, seed),
    "GRASS_DARK": lambda s, seed: grass(s, seed, dark=True),
    "DIRT": dirt,
    "ROCK": rock,
    "CLIFF": cliff,
    "BARK": bark,
    "LEAVES": leaves,
    "WATER": water,
    "PATH": path,
    "MOSS": moss,
    "SKY_DAY": sky_day,
}

SIZES = {"GRASS": 256, "LEAVES": 128, "WATER": 128, "SKY_DAY": 128}


# ---------------------------------------------------------------------------
# WAD2
# ---------------------------------------------------------------------------


def _mip(img: Image.Image, size: int) -> bytes:
    """Four mip levels as raw palette indices."""
    out = b""
    for level in range(4):
        s = max(1, size >> level)
        level_img = img.resize((s, s), Image.NEAREST) if s != size else img
        out += level_img.tobytes()
    return out


def _to_indexed(img: Image.Image, size: int):
    # 256-colour palette per miptex (WAD2 allows it; GoldSource reads it)
    q = img.convert("RGB").quantize(colors=256, method=Image.MEDIANCUT, dither=Image.NONE)
    palette = q.getpalette()[: 256 * 3]
    palette += bytes(256 * 3 - len(palette))
    indices = q.tobytes()
    return indices, bytes(palette)


def build_wad(out: Path, size_default: int = 128) -> list[str]:
    lumps: list[tuple[str, bytes]] = []

    for name, maker in TEXTURES.items():
        size = SIZES.get(name, size_default)
        img = maker(size, hash(name) & 0xFFFF)
        indexed = Image.frombytes("L", (size, size), img.convert("RGB").quantize(256).tobytes())
        # build mips from the original RGB, quantise each level to the same palette
        q0 = img.convert("RGB").quantize(colors=256, method=Image.MEDIANCUT, dither=Image.NONE)
        palette = bytes(q0.getpalette()[: 256 * 3]).ljust(768, b"\0")

        mip_bytes = b""
        offsets = []
        for level in range(4):
            s = max(1, size >> level)
            level_rgb = img.resize((s, s), Image.NEAREST) if s != size else img
            level_q = level_rgb.convert("RGB").quantize(palette=q0, dither=Image.NONE)
            offsets.append(len(mip_bytes))
            mip_bytes += level_q.tobytes()

        header = name.encode("ascii")[:15].ljust(16, b"\0")
        header += struct.pack("<II", size, size)
        header += struct.pack("<4I", *[16 + o for o in offsets])
        entry = header + mip_bytes + struct.pack("<H", 256) + palette
        lumps.append((name, entry))

    # directory
    offset = 12
    directory = b""
    body = b""
    for name, data in lumps:
        directory += struct.pack("<iiiBBBB", offset + len(body), len(data), len(data),
                                 TYP_MIPTEX, 0, 0, 0)
        directory += name.encode("ascii")[:15].ljust(16, b"\0")
        body += data
        if len(body) % 4:
            body += b"\0" * (4 - len(body) % 4)

    header = b"WAD2" + struct.pack("<ii", len(lumps), 12 + len(body))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(header + body + directory)
    return [name for name, _ in lumps]


def read_wad(path: Path) -> list[tuple[str, int, int]]:
    data = path.read_bytes()
    assert data[:4] == b"WAD2", "not a WAD2 file"
    count, table = struct.unpack_from("<ii", data, 4)
    out = []
    for i in range(count):
        base = table + i * 32
        pos, _, _, kind, _, _, _ = struct.unpack_from("<iiiBBBB", data, base)
        name = data[base + 16 : base + 32].split(b"\0")[0].decode("ascii", "replace")
        if kind == TYP_MIPTEX:
            w, h = struct.unpack_from("<II", data, pos + 16)
            out.append((name, w, h))
    return out


def contact_sheet(out: Path, cell: int = 128) -> Path:
    cols = 4
    rows = (len(TEXTURES) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 16)), (20, 20, 22))
    for index, (name, maker) in enumerate(TEXTURES.items()):
        size = SIZES.get(name, 128)
        img = maker(size, hash(name) & 0xFFFF).resize((cell, cell), Image.LANCZOS)
        x = (index % cols) * cell
        y = (index // cols) * (cell + 16)
        sheet.paste(img, (x, y))
    sheet.save(out)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="/root/hlds/cstrike/mentzer_nature.wad")
    parser.add_argument("--sheet", default="/tmp/opencode/nature_sheet.png")
    args = parser.parse_args()

    out = Path(args.out)
    names = build_wad(out)
    listed = read_wad(out)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    for name, w, h in listed:
        print(f"  {name:<12} {w}x{h}")
    print(f"contact sheet: {contact_sheet(Path(args.sheet))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
