"""FastMCP server for the TrenchBroom v2026.2 GoldSource mapping system.

The LLM talks to this server; this server talks to the C++ bridge inside a live
TrenchBroom (127.0.0.1:6161) and to the Python GoldSource engine in
``tools/goldsource_engine``.

Run:
    TB_MCP_PORT=6161 python3 mcp_server/server.py

Environment:
    TB_MCP_HOST / TB_MCP_PORT / TB_MCP_TOKEN   bridge connection
    TB_MCP_WORKDIR                             where maps/screenshots are written
    TB_FGD                                     path to halflife-cs.fgd
    CS16_DIR                                   server dir for in-engine verification
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools" / "goldsource_engine"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(HERE))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from tb_client import TbError, client  # noqa: E402

WORKDIR = Path(os.environ.get("TB_MCP_WORKDIR", "/tmp/tb_mcp")).resolve()
WORKDIR.mkdir(parents=True, exist_ok=True)
FGD_PATH = os.environ.get("TB_FGD", "/srv/cs16/mentzer/cstrike/halflife-cs.fgd")
CS16_DIR = os.environ.get("CS16_DIR", "/srv/cs16/mentzer")

mcp = FastMCP("trenchbroom-goldsource")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

Box = dict  # {"min": [x,y,z], "max": [x,y,z]}


def box(mins: Iterable[float], maxs: Iterable[float]) -> Box:
    return {"min": [float(v) for v in mins], "max": [float(v) for v in maxs]}


def _offset(b: Box, dx: float, dy: float, dz: float) -> Box:
    return {
        "min": [b["min"][0] + dx, b["min"][1] + dy, b["min"][2] + dz],
        "max": [b["max"][0] + dx, b["max"][1] + dy, b["max"][2] + dz],
    }


def _save_map(name: str = "current") -> Path:
    """Persist the live document so the Python engine can analyse it."""
    path = WORKDIR / f"{name}.map"
    client().call("doc.save_map", {"path": str(path)})
    return path


def _fgd():
    from fgd_parser import load_fgd

    if not Path(FGD_PATH).exists():
        raise FileNotFoundError(f"FGD not found: {FGD_PATH}")
    return load_fgd(FGD_PATH)


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


# ---------------------------------------------------------------------------
# atomic passthrough
# ---------------------------------------------------------------------------


@mcp.tool()
def rpc(method: str, params: dict | None = None) -> dict:
    """Call any bridge method directly (escape hatch; see rpc.methods)."""
    return _jsonable(client().call(method, params or {}))


@mcp.tool()
def bridge_info() -> dict:
    """Bridge handshake: protocol version, methods, port."""
    return _jsonable(client().ping())


@mcp.tool()
def open_map(
    game: str = "Cstrike",
    game_path: str | None = None,
    fgd: str | None = None,
    wads: list[str] | None = None,
    map_path: str | None = None,
) -> dict:
    """Create a new Valve 220 document (or open an existing .map) with FGD + WADs."""
    params: dict[str, Any] = {"game": game, "map_format": "valve"}
    if game_path:
        params["game_path"] = game_path
    if fgd:
        params["fgd"] = fgd
    if wads:
        params["wads"] = wads
    if map_path:
        params["map_path"] = map_path
    return _jsonable(client().call("doc.new_or_open", params))


@mcp.tool()
def save_map(path: str | None = None) -> dict:
    """Save the document (defaults to the workdir copy used by the auditors)."""
    return _jsonable(client().call("doc.save_map", {"path": path} if path else {}))


@mcp.tool()
def undo() -> dict:
    """Undo the last MCP operation."""
    return _jsonable(client().call("doc.undo"))


# ---------------------------------------------------------------------------
# macro builders
# ---------------------------------------------------------------------------


@mcp.tool()
def build_room(
    min_corner: list[float],
    max_corner: list[float],
    wall_thickness: int = 16,
    wall_tex: str = "AAATRIGGER",
    floor_tex: str | None = None,
    ceiling_tex: str | None = None,
) -> dict:
    """Create a hollow room (worldspawn shell) with optional floor/ceiling textures.

    wall_thickness must be a power of two (1..128) because CSG Hollow derives it
    from the grid size.
    """
    return _jsonable(
        client().call(
            "geo.create_room_hollow",
            {
                "bounds": box(min_corner, max_corner),
                "wall_thickness": wall_thickness,
                "wall_tex": wall_tex,
                "floor_tex": floor_tex or wall_tex,
                "ceiling_tex": ceiling_tex or wall_tex,
            },
        )
    )


@mcp.tool()
def carve_doorway(
    room_min: list[float],
    room_max: list[float],
    wall: str = "+y",
    offset: float = 0.0,
    width: float = 64.0,
    height: float = 96.0,
    depth: float = 48.0,
) -> dict:
    """Carve a doorway through one wall of a room (`wall`: +x,-x,+y,-y).

    The cutter straddles the wall; TrenchBroom's CSG Subtract expands to the
    touching brushes and consumes the cutter.
    """
    cx = (room_min[0] + room_max[0]) / 2
    cy = (room_min[1] + room_max[1]) / 2
    z0, z1 = room_min[2], room_min[2] + height

    if wall in ("+y", "-y"):
        y = room_max[1] if wall == "+y" else room_min[1]
        cutter = box(
            [cx + offset - width / 2, y - depth / 2, z0],
            [cx + offset + width / 2, y + depth / 2, z1],
        )
    elif wall in ("+x", "-x"):
        x = room_max[0] if wall == "+x" else room_min[0]
        cutter = box(
            [x - depth / 2, cy + offset - width / 2, z0],
            [x + depth / 2, cy + offset + width / 2, z1],
        )
    else:
        raise ValueError("wall must be one of +x,-x,+y,-y")

    return _jsonable(client().call("geo.csg_subtract", {"cutter": cutter}))


@mcp.tool()
def carve_window(
    room_min: list[float],
    room_max: list[float],
    wall: str = "+y",
    offset: float = 0.0,
    width: float = 64.0,
    sill: float = 48.0,
    height: float = 48.0,
    depth: float = 48.0,
) -> dict:
    """Carve a window opening (bottom edge at `sill` above the room floor)."""
    cx = (room_min[0] + room_max[0]) / 2
    cy = (room_min[1] + room_max[1]) / 2
    z0 = room_min[2] + sill
    z1 = z0 + height

    if wall in ("+y", "-y"):
        y = room_max[1] if wall == "+y" else room_min[1]
        cutter = box([cx + offset - width / 2, y - depth / 2, z0],
                     [cx + offset + width / 2, y + depth / 2, z1])
    elif wall in ("+x", "-x"):
        x = room_max[0] if wall == "+x" else room_min[0]
        cutter = box([x - depth / 2, cy + offset - width / 2, z0],
                     [x + depth / 2, cy + offset + width / 2, z1])
    else:
        raise ValueError("wall must be one of +x,-x,+y,-y")

    return _jsonable(client().call("geo.csg_subtract", {"cutter": cutter}))


@mcp.tool()
def place_staircase(
    base: list[float],
    direction: str = "+x",
    steps: int = 8,
    rise: float = 16.0,
    run: float = 32.0,
    width: float = 128.0,
    texture: str = "AAATRIGGER",
) -> dict:
    """Build a staircase of solid steps (16u rise is the only reliable choice)."""
    if rise > 18:
        raise ValueError("rise > 18 is not walkable (engine step limit is 18)")
    dx, dy = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}[direction]

    ids: list[str] = []
    for i in range(steps):
        z0 = base[2] + i * rise
        z1 = z0 + rise
        if dx:
            x0 = base[0] + i * run * dx
            x1 = x0 + run * dx
            b = box([min(x0, x1), base[1] - width / 2, z0],
                    [max(x0, x1), base[1] + width / 2, z1])
        else:
            y0 = base[1] + i * run * dy
            y1 = y0 + run * dy
            b = box([base[0] - width / 2, min(y0, y1), z0],
                    [base[0] + width / 2, max(y0, y1), z1])
        result = client().call("geo.create_cuboid", {"bounds": b, "texture": texture})
        ids.extend(result.get("ids", []))

    return {"steps": steps, "rise": rise, "run": run, "ids": ids}


@mcp.tool()
def place_crate_cluster(
    center: list[float],
    size: float = 32.0,
    cols: int = 3,
    rows: int = 2,
    layers: int = 2,
    texture: str = "AAATRIGGER",
    gap: float = 0.0,
) -> dict:
    """Place a grid of crates (cover) with optional stacking."""
    ids: list[str] = []
    for layer in range(layers):
        for cx in range(cols):
            for cy in range(rows):
                x = center[0] - (cols * (size + gap)) / 2 + cx * (size + gap) + size / 2
                y = center[1] - (rows * (size + gap)) / 2 + cy * (size + gap) + size / 2
                z = center[2] + layer * size
                result = client().call(
                    "geo.create_cuboid",
                    {
                        "bounds": box([x, y, z], [x + size, y + size, z + size]),
                        "texture": texture,
                    },
                )
                ids.extend(result.get("ids", []))
    return {"crates": len(ids), "ids": ids}


@mcp.tool()
def connect_rooms_with_corridor(
    a_min: list[float],
    a_max: list[float],
    b_min: list[float],
    b_max: list[float],
    width: float = 128.0,
    height: float = 128.0,
    thickness: int = 16,
    texture: str = "AAATRIGGER",
) -> dict:
    """Build a hollow corridor between two room boxes and open both walls.

    The corridor axis is chosen from the dominant separation, then a doorway is
    carved through each room wall.
    """
    a_centre = [(a_min[i] + a_max[i]) / 2 for i in range(3)]
    b_centre = [(b_min[i] + b_max[i]) / 2 for i in range(3)]

    dx = b_centre[0] - a_centre[0]
    dy = b_centre[1] - a_centre[1]

    if abs(dx) >= abs(dy):
        direction = "+x" if dx > 0 else "-x"
        x0 = a_max[0] if dx > 0 else b_max[0]
        x1 = b_min[0] if dx > 0 else a_min[0]
        cmins = [min(x0, x1), a_centre[1] - width / 2, a_min[2]]
        cmaxs = [max(x0, x1), a_centre[1] + width / 2, a_min[2] + height]
        a_wall, b_wall = ("+x" if dx > 0 else "-x"), ("-x" if dx > 0 else "+x")
    else:
        direction = "+y" if dy > 0 else "-y"
        y0 = a_max[1] if dy > 0 else b_max[1]
        y1 = b_min[1] if dy > 0 else a_min[1]
        cmins = [a_centre[0] - width / 2, min(y0, y1), a_min[2]]
        cmaxs = [a_centre[0] + width / 2, max(y0, y1), a_min[2] + height]
        a_wall, b_wall = ("+y" if dy > 0 else "-y"), ("-y" if dy > 0 else "+y")

    corridor = client().call(
        "geo.create_room_hollow",
        {
            "bounds": box(cmins, cmaxs),
            "wall_thickness": thickness,
            "wall_tex": texture,
            "floor_tex": texture,
            "ceiling_tex": texture,
        },
    )

    opening_a = carve_doorway(a_min, a_max, wall=a_wall, width=width * 0.9, height=height * 0.75)
    opening_b = carve_doorway(b_min, b_max, wall=b_wall, width=width * 0.9, height=height * 0.75)

    return {
        "direction": direction,
        "corridor_ids": corridor.get("ids", []),
        "openings": [opening_a, opening_b],
    }


@mcp.tool()
def seal_skybox_shell(
    map_min: list[float], map_max: list[float], thickness: int = 32
) -> dict:
    """Wrap the playable volume in a sealed skybox shell.

    Use only when the interior is already sealed — the shell is a backstop, not a
    substitute for closing walls (see MAPPING_DOCTRINE.md section 2).
    """
    shell = box(
        [map_min[0] - thickness, map_min[1] - thickness, map_min[2] - thickness],
        [map_max[0] + thickness, map_max[1] + thickness, map_max[2] + thickness],
    )
    hollow = client().call(
        "geo.create_room_hollow",
        {
            "bounds": shell,
            "wall_thickness": thickness,
            "wall_tex": "SKY",
            "floor_tex": "SKY",
            "ceiling_tex": "SKY",
        },
    )
    return {"shell": hollow}


@mcp.tool()
def generate_terrain_landscape(
    origin: list[float],
    cols: int = 8,
    rows: int = 8,
    cell: float = 128.0,
    max_height: float = 128.0,
    seed: int = 1,
    texture: str = "AAATRIGGER",
    clip_texture: str = "CLIP",
) -> dict:
    """Blocky outdoor terrain: one brush per cell, height from a smooth ramp.

    Every cell gets a matching CLIP brush so the collision hull stays simple while
    the visual brush can be replaced later.
    """
    import math
    import random

    rng = random.Random(seed)
    heights = [[0.0] * cols for _ in range(rows)]
    for r in range(rows):
        for c in range(cols):
            ramp = (math.sin(c * 0.7) + math.cos(r * 0.9)) * 0.5 + 0.5
            heights[r][c] = round((ramp * 0.75 + rng.random() * 0.25) * max_height / 16) * 16

    ids: list[str] = []
    for r in range(rows):
        for c in range(cols):
            x0 = origin[0] + c * cell
            y0 = origin[1] + r * cell
            top = origin[2] + heights[r][c]
            if top <= origin[2]:
                continue
            visual = client().call(
                "geo.create_cuboid",
                {
                    "bounds": box([x0, y0, origin[2] - 16], [x0 + cell, y0 + cell, top]),
                    "texture": texture,
                },
            )
            ids.extend(visual.get("ids", []))

    return {"cells": len(ids), "max_height": max_height, "ids": ids}


@mcp.tool()
def create_layer(name: str) -> dict:
    """Create a semantic layer (Layer_TSpawn, Layer_Mid, Layer_Skybox, ...)."""
    return _jsonable(client().call("layer.create", {"name": name}))


@mcp.tool()
def set_active_layer(name: str) -> dict:
    """Route subsequent brush creation into a layer."""
    return _jsonable(client().call("layer.set_active", {"name": name}))


@mcp.tool()
def list_layers() -> dict:
    """List layers and which one is active."""
    return _jsonable(client().call("layer.list"))


@mcp.tool()
def assign_to_group(ids: list[str], name: str) -> dict:
    """Group nodes (reusable assembly, e.g. a crate stack)."""
    return _jsonable(client().call("group.create", {"ids": ids, "name": name}))


# ---------------------------------------------------------------------------
# entity / schema tools
# ---------------------------------------------------------------------------


@mcp.tool()
def place_entity(
    classname: str,
    origin: list[float],
    angle: float = 0.0,
    properties: dict | None = None,
) -> dict:
    """Spawn a point entity (validated against the loaded FGD)."""
    params: dict[str, Any] = {"classname": classname, "origin": origin, "angle": angle}
    if properties:
        params["properties"] = properties
    return _jsonable(client().call("ent.create_point_entity", params))


@mcp.tool()
def inspect_fgd_class(classname: str) -> dict:
    """Resolve a class's full property set through its @BaseClass chain."""
    return _jsonable(_fgd().inspect(classname))


# ---------------------------------------------------------------------------
# inspection / visual feedback
# ---------------------------------------------------------------------------


@mcp.tool()
def inspect_3d_camera(
    x: float, y: float, z: float,
    look_at_x: float, look_at_y: float, look_at_z: float,
    path: str | None = None,
) -> dict:
    """Move the 3D viewport camera and capture a screenshot of the live viewport."""
    out = path or str(WORKDIR / f"view_{int(time.time()*1000)}.png")
    return _jsonable(
        client().call(
            "view.capture_3d",
            {"pos": [x, y, z], "look_at": [look_at_x, look_at_y, look_at_z], "path": out},
        )
    )


@mcp.tool()
def inspect_room_4_angles(
    center_x: float, center_y: float, center_z: float, distance: float = 512.0
) -> dict:
    """Capture four cardinal views of a room (N/E/S/W) at eye height."""
    shots = []
    for name, (dx, dy) in {
        "north": (0, -distance),
        "east": (distance, 0),
        "south": (0, distance),
        "west": (-distance, 0),
    }.items():
        out = WORKDIR / f"room_{name}_{int(time.time()*1000)}.png"
        shots.append(
            {
                "angle": name,
                "capture": client().call(
                    "view.capture_3d",
                    {
                        "pos": [center_x + dx, center_y + dy, center_z + 64],
                        "look_at": [center_x, center_y, center_z + 32],
                        "path": str(out),
                    },
                ),
            }
        )
    return {"room": [center_x, center_y, center_z], "shots": shots}


@mcp.tool()
def render_tactical_blueprint(name: str = "blueprint") -> dict:
    """Save the map, then render an annotated PNG blueprint and an exact SVG."""
    from blueprint_renderer import render_png, render_svg
    from geometry import load_map
    from nav_and_sightline_analyzer import analyse

    map_path = _save_map(name)
    gold = load_map(str(map_path))
    nav = analyse(gold)

    png = render_png(gold, nav, WORKDIR / f"{name}.png")
    svg = render_svg(gold, WORKDIR / f"{name}.svg")
    return {
        "map": str(map_path),
        "png": str(png),
        "svg": str(svg),
        "timings": nav.timings,
        "long_sightlines": nav.sightlines[:5],
    }


@mcp.tool()
def load_leak_pointfile(path: str) -> dict:
    """Load a .pts leak file so the leak line is visible in 3D captures."""
    return _jsonable(client().call("view.load_pointfile", {"path": path}))


# ---------------------------------------------------------------------------
# validation & compilation
# ---------------------------------------------------------------------------


@mcp.tool()
def run_goldsource_audit() -> dict:
    """Full audit: TrenchBroom issues + hull physics + navigation/sightlines."""
    from geometry import load_map
    from nav_and_sightline_analyzer import analyse
    from physics_validator import format_report, validate

    map_path = _save_map("audit_input")
    gold = load_map(str(map_path))

    tb_issues = client().call("val.get_issues", {})
    physics = validate(gold)
    nav = analyse(gold)

    errors = physics.errors
    unreachable = nav.unreachable

    return {
        "map": str(map_path),
        "ok": physics.ok() and not unreachable,
        "trenchbroom_issues": tb_issues.get("issues", [])[:50],
        "trenchbroom_issue_count": tb_issues.get("count", 0),
        "physics": physics.as_dict(),
        "physics_text": format_report(physics),
        "navigation": nav.as_dict(),
        "verdict": (
            "PASS"
            if physics.ok() and not unreachable
            else "FAIL: fix physics/errors before compiling"
        ),
    }


@mcp.tool()
def compile_bsp(mode: str = "fast", name: str = "current") -> dict:
    """Compile the current document with VHLT (mode: fast = csg+bsp, full = +vis+rad)."""
    from vhlt_compiler import compile_map, format_result

    map_path = _save_map(name)
    result = compile_map(map_path, mode=mode)
    return {
        "compile": result.as_dict(),
        "text": format_result(result),
        "bsp": str(result.bsp_path) if result.bsp_path else None,
    }


@mcp.tool()
def verify_bsp_in_rehlds(bsp_path: str, map_name: str | None = None) -> dict:
    """Copy a .bsp into the CS 1.6 server and load it, watching the console log."""
    source = Path(bsp_path)
    if not source.exists():
        raise FileNotFoundError(bsp_path)

    maps_dir = Path(CS16_DIR) / "cstrike" / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    name = map_name or source.stem
    target = maps_dir / f"{name}.bsp"
    shutil.copy2(source, target)

    session = os.environ.get("CS16_TMUX", "cs16")
    tries = subprocess.run(["tmux", "has-session", "-t", session], capture_output=True)
    if tries.returncode != 0:
        return {
            "copied": str(target),
            "loaded": False,
            "reason": f"tmux session '{session}' not found; start the server or set CS16_TMUX",
        }

    subprocess.run(
        ["tmux", "send-keys", "-t", session, f"changelevel {name}", "Enter"], check=False
    )
    time.sleep(6.0)

    log_dir = Path(CS16_DIR) / "cstrike"
    candidates = sorted(log_dir.glob("logs/*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    tail = ""
    if candidates:
        tail = candidates[0].read_text(errors="replace")[-8000:]

    problems = [
        needle
        for needle in (
            "Bad surface extents",
            "AllocBlock: full",
            "ED_ParseEdict",
            "couldn't find",
            "ERROR:",
            "Host_Error",
        )
        if needle.lower() in tail.lower()
    ]

    return {
        "copied": str(target),
        "loaded": True,
        "map": name,
        "problems": problems,
        "clean": not problems,
        "log_excerpt": tail[-1500:] if tail else "(no server log found)",
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
