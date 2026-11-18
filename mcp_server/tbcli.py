#!/usr/bin/env python3
"""tb - command line for the TrenchBroom MCP bridge.

Every one of the bridge's RPC methods is reachable, either through a friendly
subcommand or through the universal `tb call` passthrough.

    tb ping
    tb state
    tb open --game Half-Life --fgd .../halflife-cs.fgd --wad cstrike.wad
    tb cube -64,-64,0 64,64,128 --tex C1A0_WALL
    tb room -256,-256,0 256,256,128 --thickness 16
    tb ring --count 16 --inner 40 --outer 48 --z0 0 --z1 96
    tb dup --count 15 --rotz 22.5
    tb cut -32,-32,0 32,32,64          # carve with a cutter cuboid
    tb snap --grid 1
    tb issues
    tb cap --pos 200,-200,160 --look 0,0,48 --out /tmp/a.png
    tb select --all | --bbox MIN MAX | --ids id1,id2
    tb del
    tb save /tmp/mug.map
    tb call geo.csg_union '{"ids":["b:1","b:2"]}'

Set TB_MCP_PORT (default 6161) and TB_MCP_TOKEN if the bridge uses one.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parent / "mcp_server"):
    if (_candidate / "tb_client.py").exists():
        sys.path.insert(0, str(_candidate))
        break

from tb_client import TbError, client  # noqa: E402


def vec(text: str) -> list[float]:
    parts = text.replace(" ", "").split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"expected x,y,z got {text!r}")
    return [float(p) for p in parts]


def vec2(text: str) -> list[float]:
    parts = text.replace(" ", "").split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected x,y got {text!r}")
    return [float(p) for p in parts]


def emit(value) -> None:
    print(json.dumps(value, indent=2, default=str))


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_ping(args) -> None:
    info = client().ping()
    print(f"protocol={info['protocolVersion']}  tb={info['trenchbroomVersion']}  "
          f"methods={len(info['methods'])}  port={info['port']}")


def cmd_methods(args) -> None:
    for name in sorted(client().ping()["methods"]):
        print(name)


def cmd_state(args) -> None:
    emit(client().call("doc.state"))


def cmd_issues(args) -> None:
    result = client().call("val.get_issues", {"include_hidden": args.hidden})
    print(f"{result['count']} issue(s)")
    from collections import Counter

    for description, count in Counter(i["description"] for i in result["issues"]).most_common():
        print(f"  {count:>3}x {description}")


def cmd_open(args) -> None:
    params = {"game": args.game, "map_format": args.format}
    if args.game_path:
        params["game_path"] = args.game_path
    if args.fgd:
        params["fgd"] = args.fgd
    if args.wad:
        params["wads"] = args.wad
    if args.path:
        params["map_path"] = args.path
    emit(client().call("doc.new_or_open", params))


def cmd_cube(args) -> None:
    params = {"bounds": {"min": args.min, "max": args.max}, "texture": args.tex}
    if args.grid:
        params["grid"] = args.grid
    result = client().call("geo.create_cuboid", params)
    print(" ".join(result["ids"]))


def cmd_room(args) -> None:
    result = client().call(
        "geo.create_room_hollow",
        {
            "bounds": {"min": args.min, "max": args.max},
            "wall_thickness": args.thickness,
            "wall_tex": args.tex,
            "floor_tex": args.floor_tex or args.tex,
            "ceiling_tex": args.ceiling_tex or args.tex,
        },
    )
    print(f"{result['fragments']} fragment(s): " + " ".join(result["ids"]))


def cmd_cut(args) -> None:
    result = client().call("geo.csg_subtract", {"cutter": {"min": args.min, "max": args.max}})
    print(f"{result.get('fragments')} result fragment(s)")


def cmd_ring(args) -> None:
    """One wedge, arrayed around Z: the standard way to make a cylinder."""
    import math

    half = math.pi / args.count
    half_w = args.outer * math.tan(half)
    wedge = client().call(
        "geo.create_cuboid",
        {
            "bounds": {
                "min": [args.inner, -half_w, args.z0],
                "max": [args.outer, half_w, args.z1],
            },
            "texture": args.tex,
        },
    )
    result = client().call(
        "geo.duplicate_and_transform",
        {
            "ids": wedge["ids"],
            "count": args.count - 1,
            "translate": [0, 0, 0],
            "rotate": {"center": [0, 0, 0], "axis": [0, 0, 1], "degrees": 360.0 / args.count},
        },
    )
    print(f"{args.count} segments")
    print(" ".join(wedge["ids"] + result["ids"]))


def cmd_dup(args) -> None:
    ids = args.ids.split(",") if args.ids else []
    if ids:
        client().call("doc.select_by_id", {"ids": ids})
    params = {"ids": ids, "count": args.count, "translate": args.translate}
    if args.rotz or args.rotx:
        params["rotate"] = {
            "center": args.center,
            "axis": [1, 0, 0] if args.rotx else [0, 0, 1],
            "degrees": args.rotx or args.rotz,
        }
    result = client().call("geo.duplicate_and_transform", params)
    print(f"{result['copies']} copies")


def cmd_snap(args) -> None:
    result = client().call("geo.snap_vertices", {"grid": args.grid})
    print(f"snapped={result.get('snapped')} grid={result.get('grid')}")


def cmd_cap(args) -> None:
    result = client().call(
        "view.capture_3d",
        {"pos": args.pos, "look_at": args.look, "path": args.out},
    )
    print(f"{result['path']}  {result['width']}x{result['height']}")


def cmd_select(args) -> None:
    if args.all:
        emit(client().call("doc.select_all"))
    elif args.bbox_min and args.bbox_max:
        emit(
            client().call(
                "doc.select_by_bbox",
                {"bounds": {"min": args.bbox_min, "max": args.bbox_max}},
            )
        )
    elif args.ids:
        emit(client().call("doc.select_by_id", {"ids": args.ids.split(",")}))
    else:
        emit(client().call("doc.selection"))


def cmd_del(args) -> None:
    emit(client().call("geo.delete_selected"))


def cmd_save(args) -> None:
    emit(client().call("doc.save_map", {"path": args.path}))



def _height(gx: int, gy: int, n: int, seed: int, max_h: float) -> float:
    """Deterministic rolling hills + a mountain rim, snapped to 8 units."""
    import math

    def noise(ix: int, iy: int) -> float:
        h = (ix * 374761393 + iy * 668265263 + seed * 1442695040888963407) & 0xFFFFFFFF
        h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
        return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0

    def smooth(t: float) -> float:
        return t * t * (3 - 2 * t)

    scale = 4
    x, y = gx / scale, gy / scale
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    fx, fy = smooth(x - x0), smooth(y - y0)
    v = (
        noise(x0, y0) * (1 - fx) * (1 - fy)
        + noise(x0 + 1, y0) * fx * (1 - fy)
        + noise(x0, y0 + 1) * (1 - fx) * fy
        + noise(x0 + 1, y0 + 1) * fx * fy
    )

    cx = (gx - n / 2) / (n / 2)
    cy = (gy - n / 2) / (n / 2)
    edge = max(abs(cx), abs(cy))

    # valley in the middle, mountains rising towards the rim
    bowl = -0.35 * max_h * (1.0 - min(1.0, edge * 1.6))
    mountain = 0.0
    if edge > 0.42:
        mountain = ((edge - 0.42) / 0.58) ** 1.6 * max_h

    # second octave for ridge detail
    detail = (noise(gx * 3, gy * 3) - 0.5) * max_h * 0.18

    h = 96.0 + v * max_h * 0.85 + detail + mountain + bowl
    return max(16.0, round(h / 8.0) * 8.0)


def cmd_terrain(args) -> None:
    """Terrain as stepped cells (blocky) or shared triangular prisms (smooth)."""
    n = args.cells
    cell = (2.0 * args.half) / n
    heights = [
        [_height(i, j, n, args.seed, args.max_height) for j in range(n + 1)]
        for i in range(n + 1)
    ]

    clients = client()
    made = 0

    if not args.smooth:
        for j in range(n):
            for i in range(n):
                x0 = -args.half + i * cell
                y0 = -args.half + j * cell
                clients.call(
                    "geo.create_cuboid",
                    {
                        "bounds": {
                            "min": [x0, y0, args.floor],
                            "max": [x0 + cell, y0 + cell, heights[i][j]],
                        },
                        "texture": args.tex,
                    },
                )
                made += 1
    else:
        # Two triangular prisms per cell. Neighbouring cells share the corner
        # heights, so the surface is continuous - no stair steps, no cracks.
        for j in range(n):
            for i in range(n):
                x0 = -args.half + i * cell
                y0 = -args.half + j * cell
                x1 = x0 + cell
                y1 = y0 + cell
                h00, h10 = heights[i][j], heights[i + 1][j]
                h11, h01 = heights[i + 1][j + 1], heights[i][j + 1]

                # Optimisation: if the four corners are coplanar the cell is a
                # single convex hexahedron - one brush instead of two prisms.
                # On gentle terrain this collapses 30-50% of the cells.
                if abs((h00 + h11) - (h10 + h01)) <= 1.0:
                    top = [[x0, y0, h00], [x1, y0, h10], [x1, y1, h11], [x0, y1, h01]]
                    bottom = [[x, y, args.floor] for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
                    clients.call(
                        "geo.create_brush_from_points",
                        {"points": top + bottom, "texture": args.tex},
                    )
                    made += 1
                    continue

                corners = {(x0, y0): h00, (x1, y0): h10, (x1, y1): h11, (x0, y1): h01}
                for tri in (
                    ((x0, y0), (x1, y0), (x1, y1)),
                    ((x0, y0), (x1, y1), (x0, y1)),
                ):
                    points = [[x, y, corners[(x, y)]] for x, y in tri]
                    points += [[x, y, args.floor] for x, y in tri]
                    clients.call(
                        "geo.create_brush_from_points",
                        {"points": points, "texture": args.tex},
                    )
                made += 2

    est_clipnodes = made * 6
    pct = est_clipnodes / 32767 * 100
    print(f"{n}x{n} cells, {made} brushes, cell {cell:.0f}u, smooth={args.smooth}")
    print(f"  est. clipnodes ~{est_clipnodes} ({pct:.0f}% of 32767)", end="")
    if pct > 80:
        print("  ** OVER BUDGET: reduce --cells or split the map **")
    else:
        print("  ok")


def cmd_height(args) -> None:
    """Terrain height at a world position (same function the terrain uses)."""
    n = args.cells
    cell = (2.0 * args.half) / n
    gx = int((args.at[0] + args.half) / cell)
    gy = int((args.at[1] + args.half) / cell)
    gx = max(0, min(n - 1, gx))
    gy = max(0, min(n - 1, gy))
    print(f"{_height(gx, gy, n, args.seed, args.max_height):.0f}")


def _basis(axis: list[float]) -> tuple[list[float], list[float]]:
    """Two unit vectors perpendicular to `axis`."""
    import math

    ax = axis
    norm = math.sqrt(sum(c * c for c in ax)) or 1.0
    ax = [c / norm for c in ax]
    helper = [0.0, 0.0, 1.0] if abs(ax[2]) < 0.9 else [1.0, 0.0, 0.0]
    u = [
        ax[1] * helper[2] - ax[2] * helper[1],
        ax[2] * helper[0] - ax[0] * helper[2],
        ax[0] * helper[1] - ax[1] * helper[0],
    ]
    n = math.sqrt(sum(c * c for c in u)) or 1.0
    u = [c / n for c in u]
    v = [
        ax[1] * u[2] - ax[2] * u[1],
        ax[2] * u[0] - ax[0] * u[2],
        ax[0] * u[1] - ax[1] * u[0],
    ]
    return u, v


def _ring(center, axis, radius: float, sides: int):
    import math

    u, v = _basis(axis)
    out = []
    for k in range(sides):
        a = 2 * math.pi * k / sides
        out.append(
            [
                center[0] + radius * (math.cos(a) * u[0] + math.sin(a) * v[0]),
                center[1] + radius * (math.cos(a) * u[1] + math.sin(a) * v[1]),
                center[2] + radius * (math.cos(a) * u[2] + math.sin(a) * v[2]),
            ]
        )
    return out


def _rotate_axis(axis, angle: float, azimuth: float):
    """Tilt `axis` by `angle` and spin it around itself by `azimuth`."""
    import math

    u, v = _basis(axis)
    ca, sa = math.cos(angle), math.sin(angle)
    cb, sb = math.cos(azimuth), math.sin(azimuth)
    # direction in the tilted frame
    out = [0.0, 0.0, 0.0]
    for i in range(3):
        out[i] = (
            axis[i] * ca
            + (u[i] * cb + v[i] * sb) * sa
        )
    n = math.sqrt(sum(c * c for c in out)) or 1.0
    return [c / n for c in out]



def _has_hull_rpc() -> bool:
    """True when the build exposes geo.create_brush_from_points."""
    try:
        client().call("geo.create_brush_from_points", {"points": [[0, 0, 0]]})
    except TbError as error:
        return error.code != -32601
    return True


def _cross(a, b):
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _norm(a):
    import math

    n = math.sqrt(_dot(a, a)) or 1.0
    return [c / n for c in a]


def _branch_box(clients, base, axis, length, r0, r1, texture):
    """Tapered, oriented branch built from a cuboid + vertex move + rotation.

    Used when the build has no geo.create_brush_from_points: the brush is created
    along +Z at the origin, its top face is shrunk to r1, then it is rotated onto
    `axis` and moved to `base`. Convexity is preserved by both operations.
    """
    import math

    ids = clients.call(
        "geo.create_cuboid",
        {"bounds": {"min": [-r0, -r0, 0.0], "max": [r0, r0, length]}, "texture": texture},
    )["ids"]

    # Tapering costs 4 extra RPCs per branch; only worth it on thick limbs.
    if r0 < 14.0:
        r1 = r0

    # Shrink the top ring. Ids are content-derived and therefore go stale as soon
    # as the geometry changes, so re-acquire the brush by its (shrinking) bounds
    # before every move - nothing else lives at the origin while a branch is built.
    probe = {
        "min": [-r0 - 2.0, -r0 - 2.0, -2.0],
        "max": [r0 + 2.0, r0 + 2.0, length + 2.0],
    }
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        fresh = clients.call("doc.select_by_bbox", {"bounds": probe})["ids"]
        clients.call(
            "geo.move_vertices",
            {
                "ids": fresh,
                "vertices": [[sx * r0, sy * r0, length]],
                "delta": [sx * (r1 - r0), sy * (r1 - r0), 0.0],
                "grid": 1,
            },
        )

    # orient +Z onto axis
    axis = _norm(axis)
    if abs(_dot(axis, [0.0, 0.0, 1.0])) < 0.999999:
        rot_axis = _cross([0.0, 0.0, 1.0], axis)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, axis[2]))))
        # ids are content-derived, so they go stale as soon as the geometry
        # changes; the brush is still the current selection, so use that.
        clients.call(
            "geo.transform",
            {"rotate": {"center": [0, 0, 0], "axis": rot_axis, "degrees": angle}},
        )

    clients.call("geo.transform", {"translate": base})
    return ids


def _foliage_box(clients, centre, radius, texture):
    return clients.call(
        "geo.create_cuboid",
        {
            "bounds": {
                "min": [centre[0] - radius, centre[1] - radius, centre[2] - radius * 0.7],
                "max": [centre[0] + radius, centre[1] + radius, centre[2] + radius * 0.7],
            },
            "texture": texture,
        },
    )["ids"]


class TreeBuilder:
    """Recursive branch generator: every segment is one convex prism."""

    def __init__(self, args, hull: bool = True):
        import random

        self.args = args
        self.hull = hull
        self.rng = random.Random(args.seed)
        self.brushes = 0
        self.points_batches: list[tuple[list[list[float]], str]] = []
        self.box_batches: list[tuple[list[float], list[float], float, float, float, str]] = []

    def branch(self, base, axis, length, radius, depth, last=False):
        import math

        tip = [base[i] + axis[i] * length for i in range(3)]
        r0 = radius
        r1 = radius * (0.62 if not last else 0.45)
        sides = self.args.sides

        if self.hull:
            pts = _ring(base, axis, r0, sides) + _ring(tip, axis, r1, sides)
            self.points_batches.append((pts, self.args.bark_tex))
        else:
            self.box_batches.append((base, axis, length, r0, r1, self.args.bark_tex))
        self.brushes += 1

        if depth <= 0 or length < self.args.min_length:
            self.foliage(tip, radius)
            return

        children = 2 if depth > 1 else 3
        for c in range(children):
            spread = math.radians(self.args.spread) * (1.0 + 0.25 * (c - (children - 1) / 2))
            azimuth = 2 * math.pi * c / children + self.rng.uniform(-0.5, 0.5)
            child_axis = _rotate_axis(axis, spread, azimuth)
            self.branch(
                tip,
                child_axis,
                length * self.args.length_falloff,
                radius * 0.66,
                depth - 1,
                last=(depth == 1),
            )

    def foliage(self, centre, radius):
        """A couple of convex leaf blobs so the tip does not look like a stick."""
        import math

        for _ in range(self.args.leaf_blobs):
            r = max(radius * 3.0, self.args.height * 0.055) * self.rng.uniform(0.85, 1.15)
            pts = []
            for k in range(7):
                a = 2 * math.pi * k / 7
                elev = (k % 3 - 1) * 0.55
                pts.append(
                    [
                        centre[0] + r * math.cos(a) * math.cos(elev),
                        centre[1] + r * math.sin(a) * math.cos(elev),
                        centre[2] + r * math.sin(elev) + radius,
                    ]
                )
            if self.hull:
                self.points_batches.append((pts, self.args.leaf_tex))
            else:
                self.box_batches.append(
                    (centre, [0.0, 0.0, 1.0], r * 1.4, r, r, self.args.leaf_tex)
                )
            self.brushes += 1


def cmd_tree(args) -> None:
    """Procedural tree: recursive tapered branches + convex leaf blobs."""
    import math

    x, y, z = args.at
    hull = args.method == "hull" or (args.method == "auto" and _has_hull_rpc())
    builder = TreeBuilder(args, hull=hull)
    builder.branch([x, y, z], [0.0, 0.0, 1.0], args.height * 0.45, args.radius, args.depth)

    method = "hull" if hull else "box"
    if args.dry_run:
        print(f"{builder.brushes} brush(es) via {method} (dry run)")
        return

    clients = client()
    for points, texture in builder.points_batches:
        clients.call("geo.create_brush_from_points", {"points": points, "texture": texture})
    for base, axis, length, r0, r1, texture in builder.box_batches:
        _branch_box(clients, base, axis, length, r0, r1, texture)

    print(f"tree at {x:.0f},{y:.0f} depth={args.depth} via {method} -> {builder.brushes} brushes")


def cmd_layers(args) -> None:
    emit(client().call("layer.list"))


def cmd_snapshot(args) -> None:
    """Write a timestamped .map copy into .map_history (never touches the doc)."""
    if args.list:
        emit(client().call("snapshot.list"))
        return
    emit(client().call("snapshot.save", {"label": args.label}))


def cmd_call(args) -> None:
    params = json.loads(args.params) if args.params else {}
    emit(client().call(args.method, params))


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tb", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="bridge handshake").set_defaults(func=cmd_ping)
    sub.add_parser("methods", help="list RPC methods").set_defaults(func=cmd_methods)
    sub.add_parser("state", help="document state").set_defaults(func=cmd_state)

    issues = sub.add_parser("issues", help="TrenchBroom validation issues")
    issues.add_argument("--hidden", action="store_true")
    issues.set_defaults(func=cmd_issues)

    op = sub.add_parser("open", help="new/open document")
    op.add_argument("--game", default="Half-Life")
    op.add_argument("--format", default="valve")
    op.add_argument("--game-path")
    op.add_argument("--fgd")
    op.add_argument("--wad", action="append", default=[])
    op.add_argument("--path")
    op.set_defaults(func=cmd_open)

    cb = sub.add_parser("cube", help="create a cuboid brush")
    cb.add_argument("--min", type=vec, required=True)
    cb.add_argument("--max", type=vec, required=True)
    cb.add_argument("--tex", default="AAATRIGGER")
    cb.add_argument("--grid", type=int)
    cb.set_defaults(func=cmd_cube)

    rm = sub.add_parser("room", help="hollow room")
    rm.add_argument("--min", type=vec, required=True)
    rm.add_argument("--max", type=vec, required=True)
    rm.add_argument("--thickness", type=int, default=16)
    rm.add_argument("--tex", default="AAATRIGGER")
    rm.add_argument("--floor-tex")
    rm.add_argument("--ceiling-tex")
    rm.set_defaults(func=cmd_room)

    ct = sub.add_parser("cut", help="subtract a cutter cuboid")
    ct.add_argument("--min", type=vec, required=True)
    ct.add_argument("--max", type=vec, required=True)
    ct.set_defaults(func=cmd_cut)

    rg = sub.add_parser("ring", help="cylinder from one wedge + radial array")
    rg.add_argument("--count", type=int, default=16)
    rg.add_argument("--inner", type=float, required=True)
    rg.add_argument("--outer", type=float, required=True)
    rg.add_argument("--z0", type=float, default=0.0)
    rg.add_argument("--z1", type=float, default=96.0)
    rg.add_argument("--tex", default="AAATRIGGER")
    rg.set_defaults(func=cmd_ring)

    dp = sub.add_parser("dup", help="duplicate + rotate/translate (N times)")
    dp.add_argument("--ids", help="comma separated (default: use selection)")
    dp.add_argument("--count", type=int, default=1)
    dp.add_argument("--translate", type=vec, default=[0, 0, 0])
    dp.add_argument("--rotz", type=float)
    dp.add_argument("--rotx", type=float)
    dp.add_argument("--center", type=vec, default=[0, 0, 0])
    dp.set_defaults(func=cmd_dup)

    sn = sub.add_parser("snap", help="snap vertices to the grid")
    sn.add_argument("--grid", type=float, default=1.0)
    sn.set_defaults(func=cmd_snap)

    cp = sub.add_parser("cap", help="capture the 3D viewport")
    cp.add_argument("--pos", type=vec, required=True)
    cp.add_argument("--look", type=vec, required=True)
    cp.add_argument("--out", required=True)
    cp.set_defaults(func=cmd_cap)

    sl = sub.add_parser("select", help="selection helpers")
    sl.add_argument("--all", action="store_true")
    sl.add_argument("--bbox-min", type=vec)
    sl.add_argument("--bbox-max", type=vec)
    sl.add_argument("--ids")
    sl.set_defaults(func=cmd_select)

    sub.add_parser("del", help="delete selection").set_defaults(func=cmd_del)

    sv = sub.add_parser("save", help="save the .map")
    sv.add_argument("path")
    sv.set_defaults(func=cmd_save)

    tr = sub.add_parser("terrain", help="solid heightfield terrain")
    tr.add_argument("--half", type=float, default=1536.0, help="half extent (units)")
    tr.add_argument("--cells", type=int, default=32, help="cells per axis")
    tr.add_argument("--smooth", action="store_true", default=True,
                    help="triangular prisms (continuous surface); default")
    tr.add_argument("--blocky", dest="smooth", action="store_false",
                    help="axis-aligned cells (old behaviour)")
    tr.add_argument("--seed", type=int, default=7)
    tr.add_argument("--max-height", type=float, default=320.0, help="mountain rim height")
    tr.add_argument("--floor", type=float, default=-64.0, help="brush bottom z")
    tr.add_argument("--tex", default="GRASS")
    tr.set_defaults(func=cmd_terrain)

    hh = sub.add_parser("height", help="terrain height at x,y (no RPC)")
    hh.add_argument("--at", type=vec2, required=True)
    hh.add_argument("--half", type=float, default=1536.0)
    hh.add_argument("--cells", type=int, default=32)
    hh.add_argument("--cell", type=float, default=0.0)
    hh.add_argument("--seed", type=int, default=7)
    hh.add_argument("--max-height", type=float, default=320.0)
    hh.set_defaults(func=cmd_height)

    tee = sub.add_parser("tree", help="procedural tree (recursive branches + foliage)")
    tee.add_argument("--at", type=vec, required=True)
    tee.add_argument("--height", type=float, default=420.0)
    tee.add_argument("--radius", type=float, default=22.0, help="trunk radius")
    tee.add_argument("--depth", type=int, default=3, help="branching depth")
    tee.add_argument("--sides", type=int, default=5, help="prism sides per branch")
    tee.add_argument("--spread", type=float, default=38.0, help="branch angle (degrees)")
    tee.add_argument("--length-falloff", type=float, default=0.72)
    tee.add_argument("--min-length", type=float, default=60.0)
    tee.add_argument("--leaf-blobs", type=int, default=2)
    tee.add_argument("--seed", type=int, default=1)
    tee.add_argument("--bark-tex", default="TREE_BARK")
    tee.add_argument("--leaf-tex", default="TREE_LEAVES")
    tee.add_argument("--dry-run", action="store_true")
    tee.add_argument("--method", choices=("auto", "hull", "box"), default="auto",
                     help="hull=create_brush_from_points, box=cuboid+vertex move (works on older builds)")
    tee.set_defaults(func=cmd_tree)

    sub.add_parser("layers", help="list layers").set_defaults(func=cmd_layers)

    snp = sub.add_parser("snapshot", help="timestamped .map copy in .map_history")
    snp.add_argument("--label", default="snap")
    snp.add_argument("--list", action="store_true")
    snp.set_defaults(func=cmd_snapshot)

    ca = sub.add_parser("call", help="raw RPC passthrough")
    ca.add_argument("method")
    ca.add_argument("params", nargs="?", default="{}")
    ca.set_defaults(func=cmd_call)

    return p


def normalize_argv(argv: list[str]) -> list[str]:
    """Join `--opt value` so negative vectors are not mistaken for options."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--") and "=" not in token and i + 1 < len(argv):
            nxt = argv[i + 1]
            if re.match(r"^-?\d", nxt):
                out.append(f"{token}={nxt}")
                i += 2
                continue
        out.append(token)
        i += 1
    return out


def main() -> int:
    argv = normalize_argv(sys.argv[1:])
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except TbError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except (ConnectionError, OSError) as error:
        print(f"cannot reach the bridge: {error}", file=sys.stderr)
        print("is TrenchBroom running?  /opt/trenchbroom-mcp/run.sh", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
