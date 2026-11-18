"""GoldSource `.map` geometry: parsing and exact convex-brush math.

Everything the rest of the engine does (physics validation, navigation,
blueprints, CSG sanity checks) rests on this module, so it is deliberately
exact rather than approximate:

* a brush is the intersection of half-spaces ``dot(n, p) <= d``;
* its vertices are the feasible intersection points of plane triples;
* footprint projection is the convex hull of those vertices;
* hull-vs-brush overlap is a full SAT test (all face normals, the three box
  axes, and every vertex-pair cross product).

Supported formats: Standard (Quake) and Valve 220 texturing, which is what
TrenchBroom writes for Counter-Strike 1.6.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

EPS = 1e-4

# ---------------------------------------------------------------------------
# vectors
# ---------------------------------------------------------------------------

Vec3 = tuple[float, float, float]


def v(x: float, y: float, z: float) -> Vec3:
    return (float(x), float(y), float(z))


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def mul(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def length(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def normalize(a: Vec3) -> Vec3:
    n = length(a)
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


# ---------------------------------------------------------------------------
# tokenizer / parser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|[{}()\[\]]|[^\s{}()\[\]"]+')


def _strip_comments(text: str) -> str:
    out = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            i += 1
        elif not in_string and text.startswith("//", i):
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(_strip_comments(text))


@dataclass(frozen=True)
class Plane:
    normal: Vec3
    dist: float

    def signed_distance(self, p: Vec3) -> float:
        return dot(self.normal, p) - self.dist

    def is_inside(self, p: Vec3, eps: float = EPS) -> bool:
        return self.signed_distance(p) <= eps


@dataclass
class Face:
    points: tuple[Vec3, Vec3, Vec3]
    plane: Plane
    texture: str
    uaxis: Vec3 = (1.0, 0.0, 0.0)
    vaxis: Vec3 = (0.0, 1.0, 0.0)
    uoffset: float = 0.0
    voffset: float = 0.0
    rotation: float = 0.0
    xscale: float = 1.0
    yscale: float = 1.0
    valve220: bool = False


@dataclass
class Brush:
    faces: list[Face]
    entity_class: str = "worldspawn"
    entity_id: int = 0
    line: int = 0

    _vertices: list[Vec3] | None = field(default=None, repr=False, compare=False)
    _bbox: tuple[Vec3, Vec3] | None = field(default=None, repr=False, compare=False)

    # -- derived geometry ---------------------------------------------------

    @property
    def planes(self) -> list[Plane]:
        return [f.plane for f in self.faces]

    @property
    def vertices(self) -> list[Vec3]:
        if self._vertices is None:
            self._vertices = _brush_vertices(self.planes)
        return self._vertices

    @property
    def bbox(self) -> tuple[Vec3, Vec3]:
        if self._bbox is None:
            pts = self.vertices
            if not pts:
                self._bbox = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
            else:
                self._bbox = (
                    (
                        min(p[0] for p in pts),
                        min(p[1] for p in pts),
                        min(p[2] for p in pts),
                    ),
                    (
                        max(p[0] for p in pts),
                        max(p[1] for p in pts),
                        max(p[2] for p in pts),
                    ),
                )
        return self._bbox

    @property
    def is_valid(self) -> bool:
        return len(self.vertices) >= 4 and len(self.faces) >= 4

    def contains_point(self, p: Vec3, eps: float = EPS) -> bool:
        return all(pl.is_inside(p, eps) for pl in self.planes)

    def intersects_box(
        self, mins: Vec3, maxs: Vec3, inset: float = 0.0
    ) -> bool:
        """Exact convex brush vs axis-aligned box overlap (SAT).

        `inset` shrinks the box on all six sides before testing. GoldSource keeps
        the player a hair above surfaces it is standing on, so "touching" must not
        count as "stuck in solid": pass `HULL_INSET` (1/32 unit) for spawn and
        clearance checks. With the default 0.0, coplanar contact counts as an
        overlap, which is the right answer for sealing tests.
        """
        if inset > 0.0:
            lo = (mins[0] + inset, mins[1] + inset, mins[2] + inset)
            hi = (maxs[0] - inset, maxs[1] - inset, maxs[2] - inset)
            if lo[0] >= hi[0] or lo[1] >= hi[1] or lo[2] >= hi[2]:
                return False
            return _sat_brush_box(self, lo, hi)
        return _sat_brush_box(self, mins, maxs)

    def footprint(self, axis: str = "z") -> list[tuple[float, float]]:
        """Convex hull of the projected vertices (true 2D footprint)."""
        pts = self.vertices
        if axis == "z":
            proj = [(p[0], p[1]) for p in pts]
        elif axis == "y":
            proj = [(p[0], p[2]) for p in pts]
        else:
            proj = [(p[1], p[2]) for p in pts]
        return convex_hull(proj)

    def center(self) -> Vec3:
        lo, hi = self.bbox
        return ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)

    def volume_hint(self) -> float:
        lo, hi = self.bbox
        return (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])


@dataclass
class Entity:
    properties: dict[str, str]
    brushes: list[Brush] = field(default_factory=list)
    entities: list["Entity"] = field(default_factory=list)
    line: int = 0

    @property
    def classname(self) -> str:
        return self.properties.get("classname", "")

    def origin(self) -> Vec3 | None:
        raw = self.properties.get("origin")
        if raw is None:
            return None
        parts = raw.split()
        if len(parts) != 3:
            return None
        try:
            return (float(parts[0]), float(parts[1]), float(parts[2]))
        except ValueError:
            return None

    def key_values(self) -> Iterator[tuple[str, str]]:
        yield from self.properties.items()


@dataclass
class GoldMap:
    entities: list[Entity]

    @property
    def worldspawn(self) -> Entity:
        for e in self.entities:
            if e.classname == "worldspawn":
                return e
        return Entity({"classname": "worldspawn"})

    def all_brushes(self) -> Iterator[Brush]:
        def walk(entity: Entity) -> Iterator[Brush]:
            yield from entity.brushes
            for child in entity.entities:
                yield from walk(child)

        for e in self.entities:
            yield from walk(e)

    def structural_brushes(self) -> list[Brush]:
        """Worldspawn brushes — the only brushes that seal the map."""
        return list(self.worldspawn.brushes)

    def by_class(self, classname: str) -> list[Entity]:
        out = []

        def walk(entity: Entity) -> None:
            if entity.classname == classname:
                out.append(entity)
            for child in entity.entities:
                walk(child)

        for e in self.entities:
            walk(e)
        return out

    def bounds(self) -> tuple[Vec3, Vec3]:
        brushes = [b for b in self.structural_brushes() if b.is_valid]
        if not brushes:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        mins = [min(b.bbox[0][i] for b in brushes) for i in range(3)]
        maxs = [max(b.bbox[1][i] for b in brushes) for i in range(3)]
        return (tuple(mins), tuple(maxs))  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# brush math
# ---------------------------------------------------------------------------


def plane_from_points(p1: Vec3, p2: Vec3, p3: Vec3) -> Plane:
    """GoldSource winding: the three points are clockwise seen from outside."""
    n = normalize(cross(sub(p1, p2), sub(p3, p2)))
    if length(n) < 1e-9:
        # Degenerate face; give it a harmless but valid plane.
        return Plane((0.0, 0.0, 1.0), 0.0)
    return Plane(n, dot(n, p1))


def _brush_vertices(planes: Sequence[Plane]) -> list[Vec3]:
    """Feasible vertices = plane triples whose intersection satisfies all planes."""
    out: list[Vec3] = []
    n = len(planes)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                p = _plane_intersection(planes[i], planes[j], planes[k])
                if p is None:
                    continue
                if all(pl.is_inside(p, 0.01) for pl in planes):
                    if not any(length(sub(p, q)) < 0.01 for q in out):
                        out.append(p)
    return out


def _plane_intersection(a: Plane, b: Plane, c: Plane) -> Vec3 | None:
    n1, n2, n3 = a.normal, b.normal, c.normal
    cross23 = cross(n2, n3)
    det = dot(n1, cross23)
    if abs(det) < 1e-9:
        return None
    return mul(
        add(
            add(mul(cross23, a.dist), mul(cross(n3, n1), b.dist)),
            mul(cross(n1, n2), c.dist),
        ),
        1.0 / det,
    )


def _sat_brush_box(brush: Brush, mins: Vec3, maxs: Vec3) -> bool:
    """Separating-axis test between a convex brush and an AABB.

    Axes tested: every brush face normal, the three box axes, and the cross
    product of every vertex pair with every box axis. The last group is a
    superset of the real brush edges, which is safe: an axis that separates two
    sets proves they are disjoint regardless of whether it is a true edge axis,
    and a superset can only make the test *more* likely to report separation,
    never less.
    """
    verts = brush.vertices
    if not verts:
        return False

    box_axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    axes: list[Vec3] = [pl.normal for pl in brush.planes] + list(box_axes)

    for i in range(len(verts)):
        for j in range(i + 1, len(verts)):
            edge = sub(verts[i], verts[j])
            if length(edge) < 1e-6:
                continue
            for axis in box_axes:
                c = cross(edge, axis)
                if length(c) > 1e-6:
                    axes.append(normalize(c))

    box_corners = [
        (x, y, z)
        for x in (mins[0], maxs[0])
        for y in (mins[1], maxs[1])
        for z in (mins[2], maxs[2])
    ]

    for axis in axes:
        bp = [dot(axis, p) for p in verts]
        cp = [dot(axis, p) for p in box_corners]
        if max(bp) < min(cp) - EPS or max(cp) < min(bp) - EPS:
            return False
    return True


def convex_hull(points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    """Monotone-chain convex hull (counter-clockwise)."""
    pts = sorted(set((round(x, 4), round(y, 4)) for x, y in points))
    if len(pts) <= 1:
        return pts

    def turn(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


# ---------------------------------------------------------------------------
# map parsing
# ---------------------------------------------------------------------------


class MapParseError(ValueError):
    pass


def parse_map(text: str) -> GoldMap:
    tokens = tokenize(text)
    pos = 0
    line_estimate = 1

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        if pos >= len(tokens):
            raise MapParseError("unexpected end of map")
        t = tokens[pos]
        pos += 1
        return t

    def take_vec3() -> Vec3:
        return (float(take()), float(take()), float(take()))

    def parse_entities(top_level: bool) -> list[Entity]:
        result: list[Entity] = []
        while True:
            t = peek()
            if t is None:
                if top_level:
                    return result
                raise MapParseError("unterminated entity block")
            if t == "}":
                take()
                return result
            if t != "{":
                raise MapParseError(f"expected '{{' at token {pos}, got {t!r}")
            take()
            result.append(parse_entity())

    def parse_entity() -> Entity:
        entity = Entity({})
        while True:
            t = peek()
            if t is None:
                raise MapParseError("unterminated entity")
            if t == "}":
                take()
                return entity
            if t == "{":
                take()
                entity.brushes.append(parse_brush())
                continue
            if t.startswith('"'):
                key = _unquote(take())
                value = _unquote(take())
                entity.properties[key] = value
                continue
            raise MapParseError(f"unexpected token in entity: {t!r}")

    def parse_brush() -> Brush:
        faces: list[Face] = []
        while True:
            t = peek()
            if t is None:
                raise MapParseError("unterminated brush")
            if t == "}":
                take()
                if not faces:
                    raise MapParseError("empty brush")
                return Brush(faces=faces)
            faces.append(parse_face())

    def parse_face() -> Face:
        token = take()
        if token != "(":
            raise MapParseError(f"expected '(' at face start, got {token!r}")
        p1 = take_vec3()
        if take() != ")":
            raise MapParseError("expected ')'")
        if take() != "(":
            raise MapParseError("expected '('")
        p2 = take_vec3()
        if take() != ")":
            raise MapParseError("expected ')'")
        if take() != "(":
            raise MapParseError("expected '('")
        p3 = take_vec3()
        if take() != ")":
            raise MapParseError("expected ')'")

        texture = take()
        plane = plane_from_points(p1, p2, p3)

        nxt = peek()
        if nxt == "[":
            take()
            u = (float(take()), float(take()), float(take()))
            uoff = float(take())
            if take() != "]":
                raise MapParseError("expected ']'")
            if take() != "[":
                raise MapParseError("expected '['")
            vax = (float(take()), float(take()), float(take()))
            voff = float(take())
            if take() != "]":
                raise MapParseError("expected ']'")
            rot = float(take())
            xs = float(take())
            ys = float(take())
            return Face(
                points=(p1, p2, p3),
                plane=plane,
                texture=texture,
                uaxis=u,
                vaxis=vax,
                uoffset=uoff,
                voffset=voff,
                rotation=rot,
                xscale=xs,
                yscale=ys,
                valve220=True,
            )

        xoff = float(take())
        yoff = float(take())
        rot = float(take())
        xs = float(take())
        ys = float(take())
        return Face(
            points=(p1, p2, p3),
            plane=plane,
            texture=texture,
            uoffset=xoff,
            voffset=yoff,
            rotation=rot,
            xscale=xs,
            yscale=ys,
            valve220=False,
        )

    entities = parse_entities(top_level=True)
    if not entities:
        raise MapParseError("map contains no entities")

    _index_brushes(entities)
    return GoldMap(entities)


def _unquote(token: str) -> str:
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        return token[1:-1].replace('\\"', '"')
    return token


def _index_brushes(entities: Sequence[Entity], counter: list[int] | None = None) -> None:
    counter = counter or [0]

    def walk(entity: Entity) -> None:
        for brush in entity.brushes:
            counter[0] += 1
            brush.entity_id = counter[0]
            brush.entity_class = entity.classname

    for entity in entities:
        walk(entity)
        for child in entity.entities:
            walk(child)


def load_map(path: str) -> GoldMap:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse_map(fh.read())


# ---------------------------------------------------------------------------
# construction (fixtures, prefabs, round-trip tests)
# ---------------------------------------------------------------------------


def make_cuboid(
    mins: Vec3, maxs: Vec3, texture: str = "AAATRIGGER", classname: str = "worldspawn"
) -> Brush:
    """Axis-aligned brush built from exact planes, never from hand-written windings."""
    (x0, y0, z0), (x1, y1, z1) = mins, maxs
    # Per-face UV axes: u/v must be tangent to the face, otherwise the compiler
    # reports "Texture axis perpendicular to face" and the projection is garbage.
    axes = {
        (1.0, 0.0, 0.0): ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        (-1.0, 0.0, 0.0): ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
        (0.0, 1.0, 0.0): ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        (0.0, -1.0, 0.0): ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        (0.0, 0.0, 1.0): ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
        (0.0, 0.0, -1.0): ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    }
    planes = [
        Plane((1.0, 0.0, 0.0), x1),
        Plane((-1.0, 0.0, 0.0), -x0),
        Plane((0.0, 1.0, 0.0), y1),
        Plane((0.0, -1.0, 0.0), -y0),
        Plane((0.0, 0.0, 1.0), z1),
        Plane((0.0, 0.0, -1.0), -z0),
    ]
    faces = []
    for plane in planes:
        u, v = axes[plane.normal]
        faces.append(
            Face(
                points=_face_points(plane),
                plane=plane,
                texture=texture,
                uaxis=u,
                vaxis=v,
                valve220=True,
            )
        )
    return Brush(faces=faces, entity_class=classname)


def _face_points(plane: Plane) -> tuple[Vec3, Vec3, Vec3]:
    """Three points whose winding yields `plane.normal` (GoldSrc convention)."""
    n = plane.normal
    # pick a helper axis not parallel to n
    helper = (0.0, 0.0, 1.0) if abs(n[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = normalize(cross(helper, n))
    v = cross(n, u)
    origin = mul(n, plane.dist)
    return (add(origin, u), origin, add(origin, v))


def brush_to_map_text(brush: Brush) -> str:
    """Serialises a brush in Valve 220 form (round-trip / fixture generation)."""
    lines = ["{"]
    for face in brush.faces:
        (p1, p2, p3) = face.points
        u = face.uaxis
        v = face.vaxis
        lines.append(
            "( {:.6g} {:.6g} {:.6g} ) ( {:.6g} {:.6g} {:.6g} ) ( {:.6g} {:.6g} {:.6g} ) "
            "{} [ {:.6g} {:.6g} {:.6g} {:.6g} ] [ {:.6g} {:.6g} {:.6g} {:.6g} ] "
            "{:.6g} {:.6g} {:.6g}".format(
                *p1, *p2, *p3, face.texture,
                u[0], u[1], u[2], face.uoffset,
                v[0], v[1], v[2], face.voffset,
                face.rotation, face.xscale, face.yscale,
            )
        )
    lines.append("}")
    return "\n".join(lines)
