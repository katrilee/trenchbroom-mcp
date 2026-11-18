"""VHLT (Vluzacn's ZHLT v34) pipeline driver + log/leak parser.

Runs `hlcsg -> hlbsp -> hlvis -> hlrad`, captures each stage's output, and turns
the notoriously cryptic GoldSource compiler output into actionable findings:

* `LEAK in hull N`            -> reads the `.pts` pointfile, reports where the
                                 trace *exits* the map and the nearest entity.
* `MAX_MAP_CLIPNODES`         -> brush-count explosion, suggests func_wall+clip.
* `AllocBlock: full`          -> too many lightmaps, suggests scale unification.
* `Bad surface extents`       -> extreme UV scale on a face.
* `Leaf portal saw into leaf` -> coplanar/overlapping brush faces.

Engine budgets are reported as percentages so drift is visible before a failure.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

# hard BSP v30 limits
LIMITS = {
    "clipnodes": 32767,
    "nodes": 65535,
    "leafs": 8192,
    "faces": 65535,
    "verts": 65535,
    "texinfo": 8192,
    "planes": 32767,
    "lighting": 1024 * 1024,
    "visdata": 512 * 1024,
    "entstring": 64 * 1024,
}

TOOL_NAMES = ("hlcsg", "hlbsp", "hlvis", "hlrad")


@dataclass
class StageResult:
    name: str
    command: list[str]
    returncode: int
    duration: float
    stdout: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class Finding:
    severity: str
    code: str
    message: str

    def as_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass
class CompileResult:
    map_path: Path
    bsp_path: Path | None
    stages: list[StageResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    leak: bool = False
    leak_point: tuple[float, float, float] | None = None
    leak_path: list[tuple[float, float, float]] = field(default_factory=list)
    budgets: dict[str, float] = field(default_factory=dict)
    log_dir: Path | None = None

    @property
    def success(self) -> bool:
        return (
            self.bsp_path is not None
            and self.bsp_path.exists()
            and not self.leak
            and all(s.ok for s in self.stages)
        )

    def as_dict(self) -> dict:
        return {
            "success": self.success,
            "map": str(self.map_path),
            "bsp": str(self.bsp_path) if self.bsp_path else None,
            "leak": self.leak,
            "leak_point": list(self.leak_point) if self.leak_point else None,
            "stages": [
                {
                    "name": s.name,
                    "ok": s.ok,
                    "returncode": s.returncode,
                    "seconds": round(s.duration, 2),
                }
                for s in self.stages
            ],
            "findings": [f.as_dict() for f in self.findings],
            "budgets": {k: round(v, 1) for k, v in self.budgets.items()},
        }


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------


def _tool_path(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} not found in PATH (VHLT not installed?)")
    return found


def _run_stage(
    name: str, args: Sequence[str], cwd: Path, timeout: float
) -> StageResult:
    command = [_tool_path(name), *args]
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return StageResult(
            name=name,
            command=command,
            returncode=completed.returncode,
            duration=time.time() - started,
            stdout=(completed.stdout or "") + (completed.stderr or ""),
        )
    except subprocess.TimeoutExpired as expired:
        return StageResult(
            name=name,
            command=command,
            returncode=124,
            duration=time.time() - started,
            stdout=f"TIMEOUT after {timeout}s\n{expired.stdout or ''}",
        )


def compile_map(
    map_path: str | os.PathLike[str],
    mode: str = "fast",
    wad_paths: Sequence[str] = (),
    extra_args: Sequence[str] = (),
    timeout_per_stage: float = 900.0,
    log_dir: str | os.PathLike[str] | None = None,
) -> CompileResult:
    """Compile a `.map` to `.bsp`.

    mode="fast" -> hlcsg + hlbsp only (greybox / seal checking)
    mode="full" -> + hlvis + hlrad
    """
    source = Path(map_path).resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    work = source.parent
    stem = source.stem
    bsp_path = work / f"{stem}.bsp"

    result = CompileResult(map_path=source, bsp_path=bsp_path)
    result.log_dir = Path(log_dir).resolve() if log_dir else work

    common: list[str] = []
    for wad in wad_paths:
        common += ["-wadpath", str(Path(wad).resolve())]
    common += list(extra_args)

    stages = ["hlcsg", "hlbsp"]
    if mode == "full":
        stages += ["hlvis", "hlrad"]

    for name in stages:
        stage = _run_stage(name, [stem, *common], work, timeout_per_stage)
        result.stages.append(stage)

        log_file = result.log_dir / f"{stem}.{name}.log"
        try:
            log_file.write_text(stage.stdout, encoding="utf-8", errors="replace")
        except OSError:
            pass

        _analyse_stage(result, stage)

        # a leak is only meaningful to chase after CSG/BSP
        if result.leak and name in ("hlcsg", "hlbsp"):
            break
        if not stage.ok:
            break

    _read_budgets(result, work, stem)
    if bsp_path.exists() and result.leak:
        bsp_path.unlink(missing_ok=True)
        result.bsp_path = None

    return result


# ---------------------------------------------------------------------------
# log analysis
# ---------------------------------------------------------------------------

_LEAK_RE = re.compile(r"LEAK in hull (\d+)", re.IGNORECASE)
_CLIPNODE_RE = re.compile(r"MAX_MAP_CLIPNODES", re.IGNORECASE)
_ALLOCBLOCK_RE = re.compile(r"AllocBlock:\s*full", re.IGNORECASE)
_EXTENTS_RE = re.compile(r"Bad surface extents", re.IGNORECASE)
_PORTAL_RE = re.compile(r"Leaf portal saw into leaf|portalclipped", re.IGNORECASE)
_BRUSH_ERR_RE = re.compile(r"brush #(\d+)", re.IGNORECASE)


def _analyse_stage(result: CompileResult, stage: StageResult) -> None:
    text = stage.stdout

    for match in _LEAK_RE.finditer(text):
        result.leak = True
        hull = match.group(1)
        point, path = _read_pointfile(result.map_path.with_suffix(".pts"))
        result.leak_point = point
        result.leak_path = path
        where = (
            f" at ({point[0]:.0f}, {point[1]:.0f}, {point[2]:.0f})" if point else ""
        )
        result.findings.append(
            Finding(
                "error",
                "leak",
                f"LEAK in hull {hull}{where}: the map is not sealed. Load the "
                f"pointfile to see the trace; the leak is where it crosses a brush "
                f"edge, not where it starts.",
            )
        )

    if _CLIPNODE_RE.search(text):
        result.findings.append(
            Finding(
                "error",
                "max_clipnodes",
                "MAX_MAP_CLIPNODES exceeded. Convert micro-brushes (<16u) into "
                "func_wall + a single CLIP brush per volume, or delete decoration.",
            )
        )
    if _ALLOCBLOCK_RE.search(text):
        result.findings.append(
            Finding(
                "error",
                "allocblock_full",
                "AllocBlock: full — too many distinct lightmaps. Unify texture "
                "scale across large walls or split oversized faces.",
            )
        )
    if _EXTENTS_RE.search(text):
        result.findings.append(
            Finding(
                "warning",
                "bad_surface_extents",
                "Bad surface extents — a face's texture projection is out of range. "
                "Reset UV to world (reset_uv_to_world) or reduce texture scale "
                "extremes (keep 0.25..4.0).",
            )
        )
    if _PORTAL_RE.search(text):
        result.findings.append(
            Finding(
                "warning",
                "portal_saw_into_leaf",
                "Leaf portal saw into leaf — usually a coplanar overlapping brush "
                "face. Offset one brush 1u inward into solid, or miter the joint.",
            )
        )

    brush_errors = _BRUSH_ERR_RE.findall(text)
    if brush_errors and stage.name == "hlcsg":
        preview = ", ".join(brush_errors[:8])
        result.findings.append(
            Finding(
                "warning", "brush_errors", f"CSG reported problems with brushes: {preview}"
            )
        )


def _read_pointfile(
    path: Path,
) -> tuple[tuple[float, float, float] | None, list[tuple[float, float, float]]]:
    """`.pts` holds triples of points; the last one is where the trace leaves."""
    if not path.exists():
        return None, []

    points: list[tuple[float, float, float]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 3:
                try:
                    points.append((float(parts[0]), float(parts[1]), float(parts[2])))
                except ValueError:
                    continue
    except OSError:
        return None, []

    if not points:
        return None, []
    return points[-1], points


# ---------------------------------------------------------------------------
# budgets
# ---------------------------------------------------------------------------

_BUDGET_PATTERNS = {
    "brushes": re.compile(r"(\d+)\s+brushes", re.IGNORECASE),
    "entities": re.compile(r"(\d+)\s+entities", re.IGNORECASE),
    "planes": re.compile(r"(\d+)\s+plane", re.IGNORECASE),
    "clipnodes": re.compile(r"(\d+)\s+clipnodes", re.IGNORECASE),
    "nodes": re.compile(r"(\d+)\s+nodes", re.IGNORECASE),
    "leafs": re.compile(r"(\d+)\s+leafs", re.IGNORECASE),
    "faces": re.compile(r"(\d+)\s+faces", re.IGNORECASE),
    "verts": re.compile(r"(\d+)\s+vertexes", re.IGNORECASE),
    "texinfo": re.compile(r"(\d+)\s+texinfo", re.IGNORECASE),
}


def _read_budgets(result: CompileResult, work: Path, stem: str) -> None:
    text = "\n".join(stage.stdout for stage in result.stages)
    for name, pattern in _BUDGET_PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            used = max(int(m) for m in matches)
            limit = LIMITS.get(name)
            result.budgets[name] = (used / limit * 100.0) if limit else float(used)
            result.budgets[f"{name}_used"] = float(used)

    # lightdata / visdata come from the BSP itself
    bsp = work / f"{stem}.bsp"
    if not bsp.exists():
        return
    try:
        import struct

        data = bsp.read_bytes()
        if len(data) < 4 + 30 * 4:
            return
        # BSP v30 header: int version; then 15 lumps of {int offset; int length}
        lumps = struct.unpack_from("<30i", data, 4)

        def lump_length(index: int) -> int:
            return lumps[index * 2 + 1]

        # 0 entities, 1 planes, 2 miptex, 3 vertexes, 4 visibility, 5 nodes,
        # 6 texinfo, 7 faces, 8 lighting, 9 clipnodes, 10 leafs, ...
        visibility = lump_length(4)
        lighting = lump_length(8)
        result.budgets["visdata"] = visibility / LIMITS["visdata"] * 100.0
        result.budgets["visdata_used"] = float(visibility)
        result.budgets["lighting"] = lighting / LIMITS["lighting"] * 100.0
        result.budgets["lighting_used"] = float(lighting)
    except (OSError, struct.error):
        return


def format_result(result: CompileResult) -> str:
    lines = []
    for stage in result.stages:
        status = "ok" if stage.ok else f"FAILED (rc={stage.returncode})"
        lines.append(f"  {stage.name:<7} {status:<18} {stage.duration:6.1f}s")
    if result.leak:
        point = result.leak_point
        where = f" at ({point[0]:.0f}, {point[1]:.0f}, {point[2]:.0f})" if point else ""
        lines.append(f"  LEAK{where}")
    for finding in result.findings:
        lines.append(f"  [{finding.severity}] {finding.code}: {finding.message}")
    if result.budgets:
        budget = ", ".join(
            f"{k.replace('_used', '')}={result.budgets[k]:.0f}%"
            for k in sorted(result.budgets)
            if not k.endswith("_used")
        )
        lines.append(f"  budgets: {budget}")
    lines.append(f"  => {'SUCCESS' if result.success else 'FAILED'}")
    return "\n".join(lines)
