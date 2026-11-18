"""Tactical navigation and sightline analysis for GoldSource maps.

Voxelises the walkable interior on a 32-unit grid, runs A* at knife-run speed
(250 u/s) to time each spawn group's route to each objective, and raycasts at eye
level to find sightlines.

The numbers this produces are the ones level designers argue about: "do CTs get
to A 2-4 seconds before Ts", "is every spawn connected to every site", "is there
an accidental spawn-to-spawn sniper lane".
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from geometry import Brush, GoldMap, Vec3, dot, normalize, sub
from physics_validator import HULL_INSET, MAX_STEP

RUN_SPEED = 250.0            # knife-out run speed, units/second
GRID = 32.0
MAX_CLIMB = MAX_STEP         # walkable height change between cells
JETPACK_LIMIT = 62.0         # crouch-jump ceiling
SIGHTLINE_FLAG = 900.0       # >= this is reported as a long lane


# ---------------------------------------------------------------------------
# ray casting (exact convex-brush intersection)
# ---------------------------------------------------------------------------


def ray_brush_distance(
    brush: Brush, origin: Vec3, direction: Vec3, max_distance: float
) -> float | None:
    """Slab clipping of a ray against the brush's half-space set."""
    t_enter, t_exit = 0.0, max_distance
    for plane in brush.planes:
        denom = dot(plane.normal, direction)
        dist = plane.dist - dot(plane.normal, origin)
        if abs(denom) < 1e-9:
            if dist < 0:            # parallel and outside
                return None
            continue
        t = dist / denom
        if denom < 0:
            if t > t_enter:
                t_enter = t
        elif t < t_exit:
            t_exit = t
        if t_enter > t_exit:
            return None
    return t_enter if t_enter >= 0 else None


def trace(
    solids: Sequence[Brush], start: Vec3, end: Vec3
) -> tuple[bool, float, Brush | None]:
    """Returns (blocked, distance_to_first_hit, brush)."""
    delta = sub(end, start)
    distance = math.sqrt(dot(delta, delta))
    if distance < 1e-6:
        return False, 0.0, None
    direction = (delta[0] / distance, delta[1] / distance, delta[2] / distance)

    best_t: float | None = None
    best_brush: Brush | None = None
    for brush in solids:
        t = ray_brush_distance(brush, start, direction, distance)
        if t is not None and (best_t is None or t < best_t):
            best_t, best_brush = t, brush

    if best_t is None:
        return False, distance, None
    return True, best_t, best_brush


# ---------------------------------------------------------------------------
# walkable grid
# ---------------------------------------------------------------------------


@dataclass
class Cell:
    x: float
    y: float
    floor: float

    @property
    def key(self) -> tuple[int, int]:
        return (int(round(self.x / GRID)), int(round(self.y / GRID)))


@dataclass
class NavResult:
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)
    timings: dict[str, dict[str, float | None]] = field(default_factory=dict)
    unreachable: list[str] = field(default_factory=list)
    sightlines: list[dict] = field(default_factory=list)
    spawn_to_spawn: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "walkable_cells": len(self.cells),
            "timings": self.timings,
            "unreachable": self.unreachable,
            "long_sightlines": self.sightlines,
            "spawn_to_spawn_lanes": self.spawn_to_spawn,
        }


def _floor_at(solids: Sequence[Brush], x: float, y: float, z_top: float, z_bottom: float) -> float | None:
    """Highest solid surface under (x, y) inside [z_bottom, z_top]."""
    best: float | None = None
    for brush in solids:
        lo, hi = brush.bbox
        if not (lo[0] - 1e-3 <= x <= hi[0] + 1e-3 and lo[1] - 1e-3 <= y <= hi[1] + 1e-3):
            continue
        top = hi[2]
        if top > z_top + 1e-3 or top < z_bottom:
            continue
        probe = (x, y, top - 0.5)
        if brush.contains_point(probe) and (best is None or top > best):
            best = top
    return best


def _hull_fits(solids: Sequence[Brush], x: float, y: float, floor: float) -> bool:
    mins = (x - 16.0, y - 16.0, floor + 0.5)
    maxs = (x + 16.0, y + 16.0, floor + 0.5 + 71.0)
    for brush in solids:
        if brush.intersects_box(mins, maxs, inset=HULL_INSET):
            return False
    return True


def build_nav(map_: GoldMap, solids: Sequence[Brush], step: float = GRID) -> dict[tuple[int, int], Cell]:
    lo, hi = map_.bounds()
    cells: dict[tuple[int, int], Cell] = {}

    x = math.floor(lo[0] / step) * step
    while x <= hi[0]:
        y = math.floor(lo[1] / step) * step
        while y <= hi[1]:
            floor = _floor_at(solids, x, y, hi[2] - 1.0, lo[2])
            if floor is not None and _hull_fits(solids, x, y, floor):
                cells[(int(round(x / step)), int(round(y / step)))] = Cell(x, y, floor)
            y += step
        x += step
    return cells


# ---------------------------------------------------------------------------
# A*
# ---------------------------------------------------------------------------


def _neighbours(key: tuple[int, int]) -> Iterable[tuple[tuple[int, int], float]]:
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        cost = math.sqrt(2.0) if dx and dy else 1.0
        yield ((key[0] + dx, key[1] + dy), cost)


def astar(
    cells: dict[tuple[int, int], Cell],
    start: tuple[int, int],
    goal: tuple[int, int],
) -> list[tuple[int, int]] | None:
    if start not in cells or goal not in cells:
        return None

    def heuristic(a: tuple[int, int], b: tuple[int, int]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    frontier: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    cost_so_far = {start: 0.0}

    while frontier:
        _, current = heapq.heappop(frontier)
        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            return list(reversed(path))

        for nxt, step_cost in _neighbours(current):
            if nxt not in cells:
                continue
            rise = abs(cells[nxt].floor - cells[current].floor)
            if rise > MAX_CLIMB:
                continue                      # requires a jump; excluded from walking
            new_cost = cost_so_far[current] + step_cost * GRID
            if nxt not in cost_so_far or new_cost < cost_so_far[nxt]:
                cost_so_far[nxt] = new_cost
                priority = new_cost + heuristic(nxt, goal) * GRID
                heapq.heappush(frontier, (priority, nxt))
                came_from[nxt] = current
    return None


def nearest_cell(cells: dict[tuple[int, int], Cell], point: Vec3, radius: int = 4) -> tuple[int, int] | None:
    base = (int(round(point[0] / GRID)), int(round(point[1] / GRID)))
    best: tuple[int, int] | None = None
    best_d = 1e18
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            key = (base[0] + dx, base[1] + dy)
            if key not in cells:
                continue
            cell = cells[key]
            d = math.hypot(cell.x - point[0], cell.y - point[1]) + abs(cell.floor - point[2]) * 4
            if d < best_d:
                best_d, best = d, key
    return best


# ---------------------------------------------------------------------------
# objectives / spawns
# ---------------------------------------------------------------------------


def _team_of(classname: str) -> str | None:
    if classname in ("info_player_terrorist", "info_player_deathmatch"):
        return "T"
    if classname in ("info_player_counterterrorist", "info_player_start"):
        return "CT"
    return None


def objectives(map_: GoldMap) -> dict[str, Vec3]:
    out: dict[str, Vec3] = {}
    for entity in map_.entities:
        if entity.classname in ("func_bomb_target", "info_bomb_target"):
            centre = _entity_centre(entity)
            if centre:
                out.setdefault(f"Bombsite{chr(ord('A') + len(out))}", centre)
    return out


def _entity_centre(entity) -> Vec3 | None:
    if entity.brushes:
        lo = [min(b.bbox[0][i] for b in entity.brushes) for i in range(3)]
        hi = [max(b.bbox[1][i] for b in entity.brushes) for i in range(3)]
        return (tuple((lo[i] + hi[i]) / 2 for i in range(3)))  # type: ignore[return-value]
    return entity.origin()


def analyse(map_: GoldMap) -> NavResult:
    from physics_validator import solids_at

    solids = solids_at(map_)
    result = NavResult()
    result.cells = build_nav(map_, solids)

    spawns: dict[str, list[Vec3]] = {"T": [], "CT": []}
    for entity in map_.entities:
        team = _team_of(entity.classname)
        origin = entity.origin()
        if team and origin:
            spawns[team].append(origin)

    sites = objectives(map_)
    for team, points in spawns.items():
        for site, centre in sites.items():
            best: float | None = None
            reachable = False
            for point in points:
                start = nearest_cell(result.cells, point)
                goal = nearest_cell(result.cells, centre)
                if start is None or goal is None:
                    continue
                path = astar(result.cells, start, goal)
                if path is None:
                    continue
                reachable = True
                length = 0.0
                for a, b in zip(path, path[1:]):
                    ca, cb = result.cells[a], result.cells[b]
                    length += math.hypot(cb.x - ca.x, cb.y - ca.y, cb.floor - ca.floor)
                seconds = length / RUN_SPEED
                best = seconds if best is None else min(best, seconds)
            result.timings.setdefault(site, {})[team] = best
            if not reachable:
                result.unreachable.append(f"{team} -> {site}")

    result.sightlines, result.spawn_to_spawn = _sightlines(solids, spawns, sites)
    return result


def _sightlines(
    solids: Sequence[Brush],
    spawns: dict[str, list[Vec3]],
    sites: dict[str, Vec3],
) -> tuple[list[dict], list[dict]]:
    long_lanes: list[dict] = []
    spawn_lanes: list[dict] = []

    def eye(point: Vec3) -> Vec3:
        return (point[0], point[1], point[2] + 28.0)

    # spawn -> objective lanes
    for team, points in spawns.items():
        for site, centre in sites.items():
            for index, point in enumerate(points):
                blocked, distance, _ = trace(solids, eye(point), eye(centre))
                if not blocked and distance >= SIGHTLINE_FLAG:
                    long_lanes.append(
                        {
                            "from": f"{team}#{index}",
                            "to": site,
                            "distance": round(distance, 1),
                            "seconds_to_cross": round(distance / RUN_SPEED, 2),
                        }
                    )

    # spawn -> enemy spawn (accidental sniper lane)
    for a_index, a in enumerate(spawns.get("T", [])):
        for b_index, b in enumerate(spawns.get("CT", [])):
            blocked, distance, _ = trace(solids, eye(a), eye(b))
            if not blocked and distance >= SIGHTLINE_FLAG:
                spawn_lanes.append(
                    {
                        "from": f"T#{a_index}",
                        "to": f"CT#{b_index}",
                        "distance": round(distance, 1),
                    }
                )

    return long_lanes, spawn_lanes


def format_result(result: NavResult) -> str:
    lines = [f"nav: {len(result.cells)} walkable cells"]
    for site, teams in sorted(result.timings.items()):
        parts = []
        for team in ("T", "CT"):
            value = teams.get(team)
            parts.append(f"{team}={value:.1f}s" if value is not None else f"{team}=unreachable")
        delta = ""
        t, ct = teams.get("T"), teams.get("CT")
        if t is not None and ct is not None:
            delta = f"  (CT-T = {ct - t:+.1f}s)"
        lines.append(f"  {site}: " + ", ".join(parts) + delta)
    if result.unreachable:
        lines.append("  UNREACHABLE: " + ", ".join(result.unreachable))
    for lane in result.sightlines[:5]:
        lines.append(
            f"  sightline {lane['from']} -> {lane['to']}: {lane['distance']}u"
            f" ({lane['seconds_to_cross']}s to cross)"
        )
    for lane in result.spawn_to_spawn[:3]:
        lines.append(f"  SPAWN LANE {lane['from']} -> {lane['to']}: {lane['distance']}u")
    return "\n".join(lines)
