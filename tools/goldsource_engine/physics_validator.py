"""GoldSource player-hull physics validation.

Checks a parsed `.map` against the movement rules the engine actually enforces,
so that "it compiles" is never confused with "it is playable".

Constants are the retail CS 1.6 values; every check reports the exact position
and the brush/entity responsible, never just a boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from geometry import Brush, GoldMap, Vec3, dot, sub

# ---------------------------------------------------------------------------
# engine constants
# ---------------------------------------------------------------------------

HULL_WIDTH = 32.0
HULL_STANDING_HEIGHT = 72.0
HULL_DUCKING_HEIGHT = 36.0

# Origin sits at the centre of the hull in X/Y and at its middle in Z, so the
# feet are 36 units below the origin and the eyes 28 above it (64 from floor).
STANDING_MINS = (-16.0, -16.0, -36.0)
STANDING_MAXS = (16.0, 16.0, 36.0)
DUCKING_MINS = (-16.0, -16.0, -18.0)
DUCKING_MAXS = (16.0, 16.0, 18.0)

EYE_HEIGHT = 28.0          # above origin
EYE_FROM_FLOOR = 64.0      # 36 + 28

MAX_STEP = 18.0
MAX_JUMP = 45.0
MAX_CROUCH_JUMP = 62.0
MAX_SLOPE_DEGREES = 45.0

MIN_CORRIDOR_SINGLE = 64.0
MIN_CORRIDOR_DOUBLE = 128.0
MIN_DOOR_HEIGHT = 80.0

# The engine keeps a standing player a hair above the surface it rests on, so
# "touching" must not be reported as "stuck in solid".
HULL_INSET = 1.0 / 32.0

SPAWN_CLASSES = (
    "info_player_start",
    "info_player_deathmatch",
    "info_player_terrorist",
    "info_player_counterterrorist",
)


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Issue:
    severity: Severity
    code: str
    message: str
    position: Vec3 | None = None
    entity_id: int | None = None

    def as_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "position": list(self.position) if self.position else None,
            "entity_id": self.entity_id,
        }


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    spawns_checked: int = 0
    doors_checked: int = 0

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "ok": self.ok(),
            "spawns_checked": self.spawns_checked,
            "doors_checked": self.doors_checked,
            "errors": [i.as_dict() for i in self.errors],
            "warnings": [i.as_dict() for i in self.warnings],
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def hull_at(origin: Vec3, ducking: bool = False) -> tuple[Vec3, Vec3]:
    mins, maxs = (DUCKING_MINS, DUCKING_MAXS) if ducking else (STANDING_MINS, STANDING_MAXS)
    return (
        (origin[0] + mins[0], origin[1] + mins[1], origin[2] + mins[2]),
        (origin[0] + maxs[0], origin[1] + maxs[1], origin[2] + maxs[2]),
    )


def solids_at(map_: GoldMap) -> list[Brush]:
    """Brushes that block movement.

    Worldspawn plus solid brush entities (func_wall, func_door, ...). Trigger
    volumes and illusionaries are excluded — a `trigger_multiple` must never be
    reported as "player stuck".
    """
    solid: list[Brush] = []
    for entity in map_.entities:
        if entity.classname == "worldspawn":
            solid.extend(b for b in entity.brushes if b.is_valid)
            continue
        if entity.classname.startswith(("trigger_", "func_illusionary", "func_detail")):
            continue
        solid.extend(b for b in entity.brushes if b.is_valid)
    return solid


def _touching(solids: Sequence[Brush], mins: Vec3, maxs: Vec3) -> list[Brush]:
    return [b for b in solids if b.intersects_box(mins, maxs, inset=HULL_INSET)]


def floor_height_under(solids: Sequence[Brush], x: float, y: float, z: float) -> float | None:
    """Highest solid surface at or below `z` whose footprint covers (x, y)."""
    best: float | None = None
    for brush in solids:
        lo, hi = brush.bbox
        if not (lo[0] - 1e-3 <= x <= hi[0] + 1e-3 and lo[1] - 1e-3 <= y <= hi[1] + 1e-3):
            continue
        if hi[2] <= z + 1e-3 and (best is None or hi[2] > best):
            # footprint check: is (x, y) actually inside the brush's solid, not
            # just its bounding box?
            probe = (x, y, min(hi[2] - 0.5, z - 0.5))
            if brush.contains_point(probe):
                best = hi[2]
    return best


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def validate(map_: GoldMap) -> Report:
    report = Report()
    solids = solids_at(map_)

    if not solids:
        report.issues.append(
            Issue(Severity.ERROR, "empty_map", "map has no solid brushes")
        )
        return report

    _check_spawns(map_, solids, report)
    _check_doors(map_, report)
    _check_world_bounds(map_, report)
    return report


def _check_spawns(map_: GoldMap, solids: Sequence[Brush], report: Report) -> None:
    spawns: list[tuple[int, Vec3]] = []
    for idx, entity in enumerate(map_.entities):
        if entity.classname in SPAWN_CLASSES:
            origin = entity.origin()
            if origin is None:
                report.issues.append(
                    Issue(
                        Severity.ERROR,
                        "spawn_no_origin",
                        f"{entity.classname} has no valid origin",
                        entity_id=idx,
                    )
                )
                continue
            spawns.append((idx, origin))

    if not spawns:
        report.issues.append(
            Issue(
                Severity.ERROR,
                "no_spawns",
                "map defines no player spawn point (info_player_start / "
                "info_player_deathmatch / info_player_terrorist / "
                "info_player_counterterrorist)",
            )
        )
        return

    report.spawns_checked = len(spawns)

    for idx, origin in spawns:
        mins, maxs = hull_at(origin)
        blockers = _touching(solids, mins, maxs)
        if blockers:
            report.issues.append(
                Issue(
                    Severity.ERROR,
                    "spawn_in_solid",
                    f"standing hull (32x32x72) intersects {len(blockers)} solid "
                    f"brush(es) at spawn; the player would be stuck",
                    position=origin,
                    entity_id=idx,
                )
            )
            continue

        floor = floor_height_under(solids, origin[0], origin[1], origin[2])
        if floor is None:
            report.issues.append(
                Issue(
                    Severity.ERROR,
                    "spawn_floating",
                    "no floor below this spawn (the player would fall out of the "
                    "world)",
                    position=origin,
                    entity_id=idx,
                )
            )
            continue

        clearance = origin[2] - floor
        if clearance < -STANDING_MINS[2] - 1e-3:
            report.issues.append(
                Issue(
                    Severity.ERROR,
                    "spawn_below_floor",
                    f"spawn origin is {clearance:.1f}u above the floor, less than the "
                    f"required 36u (hull half-height) — the player spawns embedded",
                    position=origin,
                    entity_id=idx,
                )
            )
        elif clearance > 36.0 + MAX_STEP:
            report.issues.append(
                Issue(
                    Severity.WARNING,
                    "spawn_high_drop",
                    f"spawn origin is {clearance:.1f}u above the floor; the player "
                    f"drops on spawn (up to {clearance - 36:.0f}u)",
                    position=origin,
                    entity_id=idx,
                )
            )

        ceiling_mins, ceiling_maxs = hull_at((origin[0], origin[1], origin[2] + 1.0))
        above = _touching(solids, ceiling_mins, ceiling_maxs)
        if above:
            # headroom: the hull already covers the full 72 units above the feet,
            # so an overlap here means the spawn is squeezed.
            report.issues.append(
                Issue(
                    Severity.ERROR,
                    "spawn_no_headroom",
                    "not enough vertical clearance for a standing player",
                    position=origin,
                    entity_id=idx,
                )
            )


def _check_doors(map_: GoldMap, report: Report) -> None:
    for idx, entity in enumerate(map_.entities):
        if entity.classname not in ("func_door", "func_door_rotating"):
            continue
        if not entity.brushes:
            continue

        report.doors_checked += 1
        lo = [min(b.bbox[0][i] for b in entity.brushes) for i in range(3)]
        hi = [max(b.bbox[1][i] for b in entity.brushes) for i in range(3)]
        width = min(hi[0] - lo[0], hi[1] - lo[1])
        height = hi[2] - lo[2]

        if height < MIN_DOOR_HEIGHT:
            report.issues.append(
                Issue(
                    Severity.ERROR,
                    "door_too_short",
                    f"door opening is {height:.0f}u high; a standing player needs "
                    f">= {MIN_DOOR_HEIGHT:.0f}u",
                    position=tuple((lo[i] + hi[i]) / 2 for i in range(3)),  # type: ignore[arg-type]
                    entity_id=idx,
                )
            )
        if width < MIN_CORRIDOR_SINGLE:
            report.issues.append(
                Issue(
                    Severity.ERROR,
                    "door_too_narrow",
                    f"door opening is {width:.0f}u wide; the player hull is "
                    f"{HULL_WIDTH:.0f}u",
                    position=tuple((lo[i] + hi[i]) / 2 for i in range(3)),  # type: ignore[arg-type]
                    entity_id=idx,
                )
            )


def _check_world_bounds(map_: GoldMap, report: Report) -> None:
    lo, hi = map_.bounds()
    for i, axis in enumerate("xyz"):
        if lo[i] < -4096 or hi[i] > 4096:
            report.issues.append(
                Issue(
                    Severity.WARNING,
                    "world_bounds",
                    f"geometry extends beyond +-4096 on {axis} ({lo[i]:.0f}..{hi[i]:.0f}); "
                    f"GoldSource world bounds are +-4096",
                )
            )


def step_classification(rise: float) -> str:
    """Classify a vertical transition the way a player experiences it."""
    if rise <= MAX_STEP:
        return "walkable"
    if rise <= MAX_JUMP:
        return "requires_jump"
    if rise <= MAX_CROUCH_JUMP:
        return "requires_crouch_jump"
    return "unreachable"


def format_report(report: Report) -> str:
    lines = [
        f"physics: {report.spawns_checked} spawn(s), {report.doors_checked} door(s), "
        f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)"
    ]
    for issue in report.issues:
        where = ""
        if issue.position:
            where = " @ ({:.0f}, {:.0f}, {:.0f})".format(*issue.position)
        lines.append(f"  [{issue.severity.value}] {issue.code}{where}: {issue.message}")
    return "\n".join(lines)
