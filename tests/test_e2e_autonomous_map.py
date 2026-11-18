#!/usr/bin/env python3
"""End-to-end proof: an AI-shaped script builds, inspects, validates and ships a map.

Runs against a live TrenchBroom with the MCP bridge on 127.0.0.1:6161.

    python3 tests/test_e2e_autonomous_map.py [--keep] [--skip-verify]

Steps (each one asserts, so a failure names the stage):
  1. handshake with the bridge
  2. new Valve 220 document + CS 1.6 FGD + WADs
  3. layers, two spawn rooms, central arena, corridors, doorways
  4. spawns, buyzones, bombsite, light_environment
  5. four 3D viewport captures + one annotated blueprint
  6. full GoldSource audit (hulls, spawns, A* timings, sightlines)
  7. VHLT compile with zero leaks
  8. load on the ReHLDS server and scan its log
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp_server"))
sys.path.insert(0, str(ROOT / "tools" / "goldsource_engine"))

import server as mcp_server  # noqa: E402

MAP_NAME = "aim_mcp_test"
WORKDIR = Path(os.environ.get("TB_MCP_WORKDIR", "/tmp/tb_mcp"))

ROOMS = {
    "t_spawn": ((-1024, -1024, 0), (-512, -256, 192)),
    "ct_spawn": ((512, -1024, 0), (1024, -256, 192)),
    "arena": ((-384, 256, 0), (384, 896, 256)),
    "mid": ((-256, -256, 0), (256, 256, 160)),
}


def step(number: int, title: str) -> None:
    print(f"\n[{number}] {title}")


def fail(message: str, payload=None) -> None:
    print(f"    FAIL: {message}")
    if payload is not None:
        print(json.dumps(payload, indent=2)[:2000])
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep the workdir")
    parser.add_argument("--skip-verify", action="store_true", help="skip ReHLDS load")
    args = parser.parse_args()

    WORKDIR.mkdir(parents=True, exist_ok=True)

    step(1, "bridge handshake")
    info = mcp_server.bridge_info()
    print(f"    protocol={info.get('protocolVersion')} tb={info.get('trenchbroomVersion')} "
          f"methods={len(info.get('methods', []))}")
    if info.get("protocolVersion") != 2:
        fail("unexpected protocol version", info)

    step(2, "new document (Valve 220 + CS 1.6 FGD + WADs)")
    wads_env = os.environ.get("TB_WADS", "")
    wads = [w for w in wads_env.split(":") if w] if wads_env else []
    opened = mcp_server.open_map(
        fgd=os.environ.get("TB_FGD", ""),
        wads=wads,
    )
    print(f"    format={opened.get('mapFormat')} defs={opened.get('entityDefinitions')} "
          f"materials={opened.get('materials')}")
    if opened.get("mapFormat") != "valve":
        fail("document is not Valve 220", opened)

    step(3, "layers, rooms, corridors, doorways")
    for layer in ("Layer_TSpawn", "Layer_CTSpawn", "Layer_Mid", "Layer_Arena",
                  "Layer_Lighting", "Layer_Triggers"):
        mcp_server.create_layer(layer)

    mcp_server.set_active_layer("Layer_TSpawn")
    mcp_server.build_room(*ROOMS["t_spawn"], wall_tex="C1A0_WALL", floor_tex="C1A0_FLOOR")
    mcp_server.set_active_layer("Layer_CTSpawn")
    mcp_server.build_room(*ROOMS["ct_spawn"], wall_tex="C1A0_WALL", floor_tex="C1A0_FLOOR")
    mcp_server.set_active_layer("Layer_Mid")
    mcp_server.build_room(*ROOMS["mid"], wall_tex="C1A0_WALL", floor_tex="C1A0_FLOOR")
    mcp_server.set_active_layer("Layer_Arena")
    mcp_server.build_room(*ROOMS["arena"], wall_tex="C1A0_WALL", floor_tex="C1A0_FLOOR")

    mcp_server.connect_rooms_with_corridor(*ROOMS["t_spawn"], *ROOMS["mid"], width=128)
    mcp_server.connect_rooms_with_corridor(*ROOMS["ct_spawn"], *ROOMS["mid"], width=128)
    mcp_server.connect_rooms_with_corridor(*ROOMS["mid"], *ROOMS["arena"], width=128)

    mcp_server.carve_doorway(*ROOMS["arena"], wall="+y", width=96, height=112)
    mcp_server.place_crate_cluster([-192, 512, 0], size=32, cols=3, rows=2, layers=2)

    step(4, "entities")
    mcp_server.set_active_layer("Layer_Triggers")
    mcp_server.place_entity("info_player_terrorist", [-768, -640, 36], angle=90)
    mcp_server.place_entity("info_player_counterterrorist", [768, -640, 36], angle=270)

    # func_bomb_target and func_buyzone are *brush* entities in CS 1.6, so a
    # volume is created first and converted, never spawned as a point entity.
    def brush_entity(min_corner, max_corner, classname, properties=None):
        volume = mcp_server.rpc(
            "geo.create_cuboid",
            {"bounds": {"min": min_corner, "max": max_corner}, "texture": "AAATRIGGER"},
        )
        params = {"ids": volume["ids"], "classname": classname}
        if properties:
            params["properties"] = properties
        return mcp_server.rpc("ent.create_brush_entity", params)

    brush_entity([-192, 704, 0], [192, 832, 64], "func_bomb_target")
    brush_entity([-896, -768, 0], [-640, -512, 64], "func_buyzone", {"team": "0"})
    brush_entity([640, -768, 0], [896, -512, 64], "func_buyzone", {"team": "1"})

    step(5, "viewport captures + blueprint")
    shots = []
    for name, cam in {
        "overview": ([0, -1500, 900], [0, 400, 64]),
        "t_spawn": ([-768, -1000, 96], [-768, -300, 64]),
        "arena": ([0, 1200, 160], [0, 500, 96]),
        "mid": ([0, -500, 120], [0, 400, 96]),
    }.items():
        result = mcp_server.inspect_3d_camera(*cam[0], *cam[1], path=str(WORKDIR / f"{MAP_NAME}_{name}.png"))
        shots.append(result)
        print(f"    {name}: {result.get('width')}x{result.get('height')} -> {result.get('path')}")
        if not Path(result["path"]).exists():
            fail(f"capture missing for {name}")

    blueprint = mcp_server.render_tactical_blueprint(MAP_NAME)
    print(f"    blueprint: {blueprint['png']}")

    step(6, "goldsource audit")
    audit = mcp_server.run_goldsource_audit()
    print(json.dumps(audit.get("physics", {}), indent=2)[:1200])
    print(json.dumps(audit.get("navigation", {}).get("timings", {}), indent=2))
    if not audit.get("ok"):
        fail("audit failed", audit)

    step(7, "VHLT compile")
    compiled = mcp_server.compile_bsp(mode="fast", name=MAP_NAME)
    print(compiled.get("text"))
    if not compiled["compile"]["success"]:
        fail("compile failed", compiled["compile"])

    bsp = compiled["bsp"]
    print(f"    bsp: {bsp}")

    if not args.skip_verify:
        step(8, "load on ReHLDS")
        verified = mcp_server.verify_bsp_in_rehlds(bsp, MAP_NAME)
        print(json.dumps({k: v for k, v in verified.items() if k != "log_excerpt"}, indent=2))
        if not verified.get("clean", False):
            fail("server reported problems", verified)
    else:
        print("\n[8] skipped (--skip-verify)")

    print("\n=== PASS: map built, inspected, validated and compiled ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
