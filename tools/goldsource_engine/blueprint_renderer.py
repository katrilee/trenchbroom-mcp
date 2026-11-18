"""Tactical blueprint output: annotated PNG (Pillow) and exact SVG.

Two audiences, two formats:

* **PNG** — human/agent readable overview: brush footprints, height colouring,
  spawn markers, objectives, A* timings, grid and scale bar.
* **SVG** — machine-exact vector footprints (true convex hulls, not bounding
  boxes) with coordinate labels, so a client can measure without raster error.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Sequence

from geometry import Brush, GoldMap, Vec3
from nav_and_sightline_analyzer import NavResult

try:  # Pillow is only needed for the PNG path
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# palette
# ---------------------------------------------------------------------------


def _height_colour(z: float, z_min: float, z_max: float) -> tuple[int, int, int]:
    if z_max - z_min < 1e-6:
        t = 0.5
    else:
        t = max(0.0, min(1.0, (z - z_min) / (z_max - z_min)))
    # blue (low) -> green -> warm yellow (high)
    r = int(40 + 200 * t)
    g = int(90 + 130 * t)
    b = int(200 - 150 * t)
    return (r, g, b)


def _brush_footprint(brush: Brush) -> list[tuple[float, float]]:
    return brush.footprint("z")


# ---------------------------------------------------------------------------
# PNG
# ---------------------------------------------------------------------------


def render_png(
    map_: GoldMap,
    nav: NavResult | None,
    path: str | Path,
    width: int = 1600,
    margin: int = 60,
    show_grid: bool = True,
) -> Path:
    if Image is None:
        raise RuntimeError("Pillow is required for PNG output")

    brushes = [b for b in map_.all_brushes() if b.is_valid]
    if not brushes:
        raise ValueError("map has no brushes to render")

    lo, hi = map_.bounds()
    span_x = max(hi[0] - lo[0], 1.0)
    span_y = max(hi[1] - lo[1], 1.0)
    scale = (width - 2 * margin) / max(span_x, span_y)
    height = int(span_y * scale) + 2 * margin

    def to_px(x: float, y: float) -> tuple[float, float]:
        return (margin + (x - lo[0]) * scale, height - margin - (y - lo[1]) * scale)

    image = Image.new("RGB", (width, height), (18, 20, 24))
    draw = ImageDraw.Draw(image, "RGBA")

    z_min = min(b.bbox[0][2] for b in brushes)
    z_max = max(b.bbox[1][2] for b in brushes)

    if show_grid:
        step = 256.0
        gx = math.floor(lo[0] / step) * step
        while gx <= hi[0]:
            x0, y0 = to_px(gx, lo[1])
            x1, y1 = to_px(gx, hi[1])
            draw.line([(x0, y0), (x1, y1)], fill=(60, 65, 75, 120), width=1)
            gx += step
        gy = math.floor(lo[1] / step) * step
        while gy <= hi[1]:
            x0, y0 = to_px(lo[0], gy)
            x1, y1 = to_px(hi[0], gy)
            draw.line([(x0, y0), (x1, y1)], fill=(60, 65, 75, 120), width=1)
            gy += step

    map_area = span_x * span_y
    # lowest first, so walls and crates land on top of floors/ceilings
    for brush in sorted(brushes, key=lambda b: b.bbox[1][2]):
        hull = _brush_footprint(brush)
        if len(hull) < 3:
            continue
        points = [to_px(x, y) for x, y in hull]
        colour = _height_colour(brush.bbox[0][2], z_min, z_max)

        lo_b, hi_b = brush.bbox
        thickness = hi_b[2] - lo_b[2]
        area = (hi_b[0] - lo_b[0]) * (hi_b[1] - lo_b[1])
        is_slab = thickness <= 32.0 and area >= 0.25 * map_area

        if is_slab:
            # floors and ceilings: outline only, so they never hide content
            draw.polygon(points, fill=colour + (40,), outline=(200, 200, 210, 140))
        else:
            draw.polygon(points, fill=colour + (190,), outline=(245, 245, 245, 225))

    # navigable cells (cheap dots) — shows what the player can actually walk
    if nav is not None and nav.cells:
        for cell in nav.cells.values():
            x, y = to_px(cell.x, cell.y)
            draw.ellipse([x - 1.5, y - 1.5, x + 1.5, y + 1.5], fill=(90, 220, 140, 70))

    def label(text: str, world: Vec3, colour: tuple[int, int, int]) -> None:
        x, y = to_px(world[0], world[1])
        draw.ellipse([x - 7, y - 7, x + 7, y + 7], outline=colour, width=3)
        # the default bitmap font has no metrics API; 6px/char is accurate enough
        tw = len(text) * 6
        draw.rectangle([x + 10, y - 9, x + 16 + tw, y + 5], fill=(15, 15, 18, 220))
        draw.text((x + 12, y - 8), text, fill=colour)

    # entities
    for index, entity in enumerate(map_.entities):
        origin = entity.origin()
        if entity.classname.startswith("info_player"):
            if origin:
                team = "T" if "terrorist" in entity.classname or "deathmatch" in entity.classname else "CT"
                label(team, origin, (255, 90, 90) if team == "T" else (90, 160, 255))
        elif entity.classname in ("func_bomb_target", "info_bomb_target"):
            centre = origin or _entity_centre(entity)
            if centre:
                label("A", centre, (255, 200, 60))
        elif entity.classname == "func_buyzone":
            centre = origin or _entity_centre(entity)
            if centre:
                label("buy", centre, (200, 120, 255))

    # timing panel
    y_text = 12
    if nav is not None:
        for site, teams in sorted(nav.timings.items()):
            line = f"{site}: " + ", ".join(
                f"{t}={v:.1f}s" if v is not None else f"{t}=X" for t, v in teams.items()
            )
            draw.text((12, y_text), line, fill=(240, 240, 240))
            y_text += 16

    # scale bar (256 units)
    bar = 256 * scale
    x0 = width - margin - bar
    y0 = height - 24
    draw.line([(x0, y0), (x0 + bar, y0)], fill=(255, 255, 255), width=3)
    draw.text((x0, y0 - 16), "256u", fill=(255, 255, 255))

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return out


def _entity_centre(entity) -> Vec3 | None:
    if not entity.brushes:
        return None
    lo = [min(b.bbox[0][i] for b in entity.brushes) for i in range(3)]
    hi = [max(b.bbox[1][i] for b in entity.brushes) for i in range(3)]
    return tuple((lo[i] + hi[i]) / 2 for i in range(3))  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------


def render_svg(
    map_: GoldMap,
    path: str | Path,
    padding: float = 64.0,
    show_labels: bool = True,
) -> Path:
    brushes = [b for b in map_.all_brushes() if b.is_valid]
    if not brushes:
        raise ValueError("map has no brushes to render")

    lo, hi = map_.bounds()
    min_x, min_y = lo[0] - padding, lo[1] - padding
    max_x, max_y = hi[0] + padding, hi[1] + padding
    width = max_x - min_x
    height = max_y - min_y

    z_min = min(b.bbox[0][2] for b in brushes)
    z_max = max(b.bbox[1][2] for b in brushes)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{min_x:.3f} {-max_y:.3f} '
        f'{width:.3f} {height:.3f}" width="{width:.0f}" height="{height:.0f}">',
        '<g transform="scale(1,-1)">',
        "<style>"
        ".b{stroke:#e8e8e8;stroke-width:2;stroke-opacity:.55}"
        ".t{font:12px sans-serif;fill:#1b1b1b}"
        ".l{stroke:#9aa0aa;stroke-width:1;stroke-opacity:.35}"
        "</style>",
    ]

    # grid
    step = 256.0
    gx = math.floor(min_x / step) * step
    while gx <= max_x:
        parts.append(f'<line class="l" x1="{gx:.1f}" y1="{min_y:.1f}" x2="{gx:.1f}" y2="{max_y:.1f}"/>')
        parts.append(
            f'<text class="t" transform="scale(1,-1)" x="{gx:.1f}" y="{-min_y + 14:.1f}">{gx:.0f}</text>'
        )
        gx += step
    gy = math.floor(min_y / step) * step
    while gy <= max_y:
        parts.append(f'<line class="l" x1="{min_x:.1f}" y1="{gy:.1f}" x2="{max_x:.1f}" y2="{gy:.1f}"/>')
        gy += step

    for brush in brushes:
        hull = _brush_footprint(brush)
        if len(hull) < 3:
            continue
        r, g, b = _height_colour(brush.bbox[0][2], z_min, z_max)
        points = " ".join(f"{x:.4f},{y:.4f}" for x, y in hull)
        parts.append(
            f'<polygon class="b" points="{points}" fill="rgb({r},{g},{b})" fill-opacity="0.55"/>'
        )

    if show_labels:
        for entity in map_.entities:
            origin = entity.origin() or _entity_centre(entity)
            if origin is None:
                continue
            text = entity.classname
            parts.append(
                f'<text class="t" transform="scale(1,-1)" x="{origin[0]:.1f}" '
                f'y="{-origin[1] - 6:.1f}">{text}</text>'
            )

    parts.append("</g></svg>")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    return out
