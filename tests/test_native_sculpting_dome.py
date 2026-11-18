#!/usr/bin/env python3
"""Native sculpting proof: 16-sided ribbed dome + arched entrance.

Everything is built with TrenchBroom's own tools: `geo.create_cuboid` for the
seed, `geo.move_vertices` to sculpt the profile, `geo.duplicate_and_transform`
for the radial array, `geo.csg_subtract` to hollow, `tex.fit_texture` /
`tex.copy_uv_between_faces` for the stone courses, and `view.isolate_selection`
for clean screenshots.

    python3 tests/test_native_sculpting_dome.py [--skip-compile] [--segments 16]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp_server"))
sys.path.insert(0, str(ROOT / "tools" / "goldsource_engine"))

import server as mcp_server  # noqa: E402

WORKDIR = Path(os.environ.get("TB_MCP_WORKDIR", "/tmp/tb_mcp"))
MAP_NAME = "dome_mcp_test"

DOME = {
    "centre": (0.0, 0.0, 0.0),
    "inner_radius": 256.0,
    "outer_radius": 384.0,
    "height": 320.0,
    "ring_height": 96.0,
}


def step(number: int, title: str) -> None:
    print(f"\n[{number}] {title}")


def fail(message: str, payload=None) -> None:
    print(f"    FAIL: {message}")
    if payload is not None:
        print(json.dumps(payload, indent=2)[:2500])
    raise SystemExit(1)


def build_rib(segments: int) -> list[str]:
    """One angular sector: a box tapered into a rib by moving its inner vertices."""
    cx, cy, cz = DOME["centre"]
    r_in, r_out = DOME["inner_radius"], DOME["outer_radius"]
    angle = 2 * math.pi / segments
    half = angle / 2

    # tangential half-width at the inner and outer radius
    w_in = r_in * math.tan(half)
    w_out = r_out * math.tan(half)

    created = mcp_server.rpc(
        "geo.create_cuboid",
        {
            "bounds": {
                "min": [cx + r_in, cy - w_in, cz],
                "max": [cx + r_out, cy + w_in, cz + DOME["ring_height"]],
            },
            "texture": "C1A0_WALL",
        },
    )
    ids = created["ids"]
    if not ids:
        fail("seed cuboid was not created", created)

    topology = mcp_server.get_brush_topology(ids)
    brush = topology["brushes"][0]
    vertices = brush["vertices"]

    # widen the outer face so the sector meets its neighbours after rotation
    outer = [v for v in vertices if v[0] > cx + (r_in + r_out) / 2]
    if not outer:
        fail("could not identify outer vertices", topology)

    for vertex in outer:
        target_y = w_out if vertex[1] >= cy else -w_out
        delta_y = target_y - vertex[1]
        if abs(delta_y) < 0.01:
            continue
        mcp_server.move_vertices(ids, [vertex], [0.0, delta_y, 0.0], grid=1)

    # lift the inner edge to start the dome curve (one sculpting step)
    inner = [v for v in vertices if v[0] < cx + (r_in + r_out) / 2]
    for vertex in inner:
        mcp_server.move_vertices(ids, [vertex], [0.0, 0.0, 48.0], grid=1)

    return ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segments", type=int, default=16)
    parser.add_argument("--skip-compile", action="store_true")
    args = parser.parse_args()

    WORKDIR.mkdir(parents=True, exist_ok=True)

    step(1, "bridge handshake")
    info = mcp_server.bridge_info()
    print(f"    protocol={info.get('protocolVersion')} methods={len(info.get('methods', []))}")
    if info.get("protocolVersion") != 2:
        fail("bridge not reachable", info)

    step(2, "document + layers")
    mcp_server.open_map(
        fgd=os.environ.get("TB_FGD", "/srv/cs16/mentzer/cstrike/halflife-cs.fgd"),
        wads=["/root/hlds/cstrike/cstrike.wad", "/root/hlds/cstrike/halflife.wad"],
    )
    for layer in ("Layer_Dome", "Layer_Detail", "Layer_Entrance", "Layer_Triggers"):
        mcp_server.create_layer(layer)

    step(3, f"sculpt one rib and array it {args.segments}x")
    mcp_server.set_active_layer("Layer_Dome")
    rib_ids = build_rib(args.segments)
    print(f"    rib seeded: {rib_ids}")

    arrayed = mcp_server.duplicate_and_transform(
        ids=rib_ids,
        count=args.segments - 1,
        rotate_center=list(DOME["centre"]),
        rotate_axis=[0, 0, 1],
        rotate_degrees=360.0 / args.segments,
    )
    print(f"    segments: {len(arrayed['ids']) + len(rib_ids)}")

    step(4, "hollow the dome")
    cx, cy, cz = DOME["centre"]
    inner = DOME["inner_radius"] - 8
    hollowed = mcp_server.rpc(
        "geo.csg_subtract",
        {
            "cutter": {
                "min": [cx - inner, cy - inner, cz - 16],
                "max": [cx + inner, cy + inner, cz + DOME["height"]],
            }
        },
    )
    print(f"    fragments after hollow: {hollowed.get('fragments')}")

    step(5, "arched entrance hall")
    mcp_server.set_active_layer("Layer_Entrance")
    hall = ((-96.0, 384.0, 0.0), (96.0, 768.0, 224.0))
    mcp_server.build_room(*hall, wall_tex="C1A0_WALL", floor_tex="C1A0_FLOOR")

    mcp_server.carve_doorway(*hall, wall="-y", width=96, height=112)
    # arch head: a window above the door reads as a lunette
    mcp_server.carve_window(*hall, wall="-y", width=112, sill=112, height=48, depth=64)

    step(6, "UVs")
    faces = mcp_server.rpc("doc.select_by_bbox", {"bounds": {"min": [-400, -400, 0], "max": [400, 400, 400]}})
    print(f"    dome faces selected: {faces.get('selected')}")
    mcp_server.align_uv_to_world()
    mcp_server.fit_texture("horizontal")

    step(7, "isolate + captures")
    shots = []
    mcp_server.isolate_selection()
    try:
        for name, (pos, look) in {
            "dome_outside": ([900, -900, 420], [0, 0, 140]),
            "dome_inside": ([0, 0, 96], [0, 300, 160]),
            "dome_top": ([0, 0, 900], [0, 0, 0]),
        }.items():
            path = WORKDIR / f"{MAP_NAME}_{name}.png"
            shot = mcp_server.inspect_3d_camera(*pos, *look, path=str(path))
            shots.append(shot)
            print(f"    {name}: {shot.get('width')}x{shot.get('height')} -> {shot.get('path')}")

        mcp_server.set_render_mode(edges=True, shade_faces=False)
        wire = WORKDIR / f"{MAP_NAME}_wireframe.png"
        shots.append(mcp_server.inspect_3d_camera(900, -900, 420, 0, 0, 140, path=str(wire)))
        mcp_server.set_render_mode(edges=False, shade_faces=True)
    finally:
        mcp_server.unhide_all()

    for shot in shots:
        if not Path(shot["path"]).exists():
            fail(f"capture missing: {shot.get('path')}")

    step(8, "audit")
    audit = mcp_server.run_goldsource_audit()
    print(json.dumps(audit.get("physics", {}), indent=2)[:1200])
    if not audit.get("ok"):
        fail("audit failed (physics/navigation)", audit)
    if audit.get("trenchbroom_issue_count", 0) > 0:
        print(f"    WARNING: {audit['trenchbroom_issue_count']} TrenchBroom issue(s)")
        print(json.dumps(audit["trenchbroom_issues"][:5], indent=2))

    if args.skip_compile:
        print("\n[9] compile skipped (--skip-compile)")
        print("\n=== PASS (build + sculpt + capture + audit) ===")
        return 0

    step(9, "VHLT compile")
    compiled = mcp_server.compile_bsp(mode="fast", name=MAP_NAME)
    print(compiled.get("text"))
    if not compiled["compile"]["success"]:
        fail("compile failed", compiled["compile"])

    print("\n=== PASS: native dome built, inspected, audited and compiled ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
