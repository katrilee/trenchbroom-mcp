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
for _candidate in (_HERE, _HERE.parent / "mcp_server", Path("/root/trenchbroom-mcp/mcp_server")):
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
                corners = {
                    (x0, y0): heights[i][j],
                    (x1, y0): heights[i + 1][j],
                    (x1, y1): heights[i + 1][j + 1],
                    (x0, y1): heights[i][j + 1],
                }
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

    print(f"{n}x{n} cells, {made} brushes, cell {cell:.0f}u, smooth={args.smooth}")


def cmd_height(args) -> None:
    """Terrain height at a world position (same function the terrain uses)."""
    n = args.cells
    cell = (2.0 * args.half) / n
    gx = int((args.at[0] + args.half) / cell)
    gy = int((args.at[1] + args.half) / cell)
    gx = max(0, min(n - 1, gx))
    gy = max(0, min(n - 1, gy))
    print(f"{_height(gx, gy, n, args.seed, args.max_height):.0f}")


def cmd_tree(args) -> None:
    """Trunk + three stacked cone rings, arrayed around its own axis."""
    import math

    clients = client()
    x, y, z = args.at
    r = args.radius
    h = args.height
    segments = args.segments

    clients.call(
        "geo.create_cuboid",
        {
            "bounds": {"min": [x - 8, y - 8, z], "max": [x + 8, y + 8, z + h * 0.55]},
            "texture": args.bark_tex,
        },
    )

    made = 1
    for level, (z_frac, r_frac) in enumerate(((0.35, 1.0), (0.58, 0.72), (0.78, 0.46))):
        outer = max(12.0, r * r_frac)
        inner = outer * 0.45
        half_angle = math.pi / segments
        half_w = outer * math.tan(half_angle)
        z0 = z + h * z_frac
        z1 = z0 + h * 0.22
        wedge = clients.call(
            "geo.create_cuboid",
            {
                "bounds": {
                    "min": [x + inner, y - half_w, z0],
                    "max": [x + outer, y + half_w, z1],
                },
                "texture": args.leaf_tex,
            },
        )
        clients.call(
            "geo.duplicate_and_transform",
            {
                "ids": wedge["ids"],
                "count": segments - 1,
                "translate": [0, 0, 0],
                "rotate": {
                    "center": [x, y, 0],
                    "axis": [0, 0, 1],
                    "degrees": 360.0 / segments,
                },
            },
        )
        made += segments
    print(f"tree at {x:.0f},{y:.0f} ({made} brushes)")


def cmd_layers(args) -> None:
    emit(client().call("layer.list"))


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

    tee = sub.add_parser("tree", help="trunk + conical canopy")
    tee.add_argument("--at", type=vec, required=True)
    tee.add_argument("--height", type=float, default=160.0)
    tee.add_argument("--radius", type=float, default=56.0)
    tee.add_argument("--segments", type=int, default=6)
    tee.add_argument("--bark-tex", default="TREE_BARK")
    tee.add_argument("--leaf-tex", default="TREE_LEAVES")
    tee.set_defaults(func=cmd_tree)

    sub.add_parser("layers", help="list layers").set_defaults(func=cmd_layers)

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
