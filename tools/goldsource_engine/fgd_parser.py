"""FGD (Forge Game Data) parser with @BaseClass inheritance resolution.

TrenchBroom and the compilers only know a class's *own* properties; the tools an
agent needs (``fgd.inspect_class``) must resolve the full ``base(...)`` chain to
answer "what keys can I set on ``func_door``, and what are their types/defaults".

Supported: ``@BaseClass``, ``@PointClass``, ``@SolidClass``, ``@KeyFrameClass``,
``@MoveClass``; the ``base(...)``, ``size(...)``, ``color(...)``, ``studio(...)``
attributes; property definitions with ``(type)``, caption, default and
description; helper classes (``@BaseClass`` used only for inheritance).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

CLASS_KINDS = {
    "@BaseClass",
    "@PointClass",
    "@SolidClass",
    "@KeyFrameClass",
    "@MoveClass",
    "@NPCClass",
    "@FilterClass",
}


@dataclass
class Property:
    name: str
    type: str
    caption: str = ""
    default: str = ""
    description: str = ""
    choices: list[tuple[str, str]] = field(default_factory=list)
    defined_in: str = ""

    def as_dict(self) -> dict:
        result = {
            "name": self.name,
            "type": self.type,
            "caption": self.caption,
            "default": self.default,
            "description": self.description,
            "defined_in": self.defined_in,
        }
        if self.choices:
            result["choices"] = [{"value": v, "label": l} for v, l in self.choices]
        return result


@dataclass
class EntityClass:
    name: str
    kind: str
    description: str = ""
    bases: list[str] = field(default_factory=list)
    size: tuple[float, float, float, float, float, float] | None = None
    color: tuple[int, int, int] | None = None
    properties: dict[str, Property] = field(default_factory=dict)

    @property
    def is_point(self) -> bool:
        return self.kind.upper() == "@POINTCLASS"

    @property
    def is_solid(self) -> bool:
        return self.kind.upper() == "@SOLIDCLASS"


class FgdParseError(ValueError):
    pass


class Fgd:
    def __init__(self, classes: dict[str, EntityClass], order: list[str]):
        self.classes = classes
        self.order = order
        self._resolved: dict[str, dict[str, Property]] = {}

    # -- public ---------------------------------------------------------

    def get(self, name: str) -> EntityClass | None:
        return self.classes.get(name)

    def names(self) -> list[str]:
        return list(self.order)

    def resolved_properties(self, name: str) -> dict[str, Property]:
        """All properties including inherited ones, child definitions winning."""
        if name in self._resolved:
            return self._resolved[name]

        result: dict[str, Property] = {}
        seen: set[str] = set()

        def walk(current: str) -> None:
            if current in seen:
                return
            seen.add(current)
            cls = self.classes.get(current)
            if cls is None:
                return
            for base in cls.bases:
                walk(base)
            for prop in cls.properties.values():
                result.setdefault(prop.name, prop)

        walk(name)
        self._resolved[name] = result
        return result

    def inspect(self, name: str) -> dict:
        cls = self.classes.get(name)
        if cls is None:
            raise KeyError(name)
        props = sorted(
            (p for p in self.resolved_properties(name).values()),
            key=lambda p: p.name.lower(),
        )
        return {
            "name": cls.name,
            "kind": cls.kind,
            "description": cls.description,
            "bases": list(cls.bases),
            "inherited_from": self._all_bases(name),
            "size": list(cls.size) if cls.size else None,
            "color": list(cls.color) if cls.color else None,
            "property_count": len(props),
            "properties": [p.as_dict() for p in props],
        }

    def _all_bases(self, name: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def walk(current: str) -> None:
            cls = self.classes.get(current)
            if cls is None:
                return
            for base in cls.bases:
                if base in seen:
                    continue
                seen.add(base)
                out.append(base)
                walk(base)

        walk(name)
        return out

    def spawnflags(self, name: str) -> list[dict]:
        """Spawnflag bits are `spawnflags(flags) = [ 1 : "label" : 0 ]`."""
        prop = self.resolved_properties(name).get("spawnflags")
        if prop is None:
            return []
        out = []
        for value, label, default in prop.choices:
            try:
                bit = int(value, 0)
            except ValueError:
                continue
            out.append({"bit": bit, "label": label, "default": default})
        return out


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

_CLASS_RE = re.compile(r"^\s*(@[A-Za-z]+Class)\b(.*)$", re.IGNORECASE | re.MULTILINE)
_ATTR_RE = re.compile(r"(\w+)\s*\(")
_PROP_RE = re.compile(
    r"^\s*([A-Za-z0-9_]+)\s*(?:\(\s*([^)]*)\s*\))?\s*(?::\s*([^:]*)"
    r"(?::\s*([^:]*)(?::\s*([^:]*))?)?)?$"
)


def _strip_comments(text: str) -> str:
    out = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            in_string = not in_string
            out.append(ch)
        elif not in_string and text.startswith("//", i):
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text


def _split_args(text: str) -> list[str]:
    """Split `a, b(c, d), e` on top-level commas."""
    parts, depth, current = [], 0, []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def _parse_size(value: str) -> tuple[float, float, float, float, float, float] | None:
    numbers = re.findall(r"-?\d+(?:\.\d+)?", value)
    if len(numbers) == 6:
        return tuple(float(n) for n in numbers)  # type: ignore[return-value]
    return None


def parse_fgd(text: str) -> Fgd:
    text = _strip_comments(text).replace("\r\n", "\n").replace("\r", "\n")
    classes: dict[str, EntityClass] = {}
    order: list[str] = []

    i = 0
    length = len(text)
    while i < length:
        match = _CLASS_RE.search(text, i)
        if not match:
            break

        kind = match.group(1)
        rest = match.group(2)

        # The body may start on the header line itself (`... "desc" []`), so
        # look for '[' from the end of the class header, not from the next line.
        # An empty class body must not swallow the *next* class's body.
        body_start = text.find("[", match.end())
        next_class = _CLASS_RE.search(text, match.end())
        if body_start < 0 or (next_class is not None and body_start > next_class.start()):
            body_start = body_end = -1
        else:
            body_end = _matching_bracket(text, body_start)
            if body_end < 0:
                raise FgdParseError(f"unterminated body for {kind}")

        name = ""
        description = ""
        bases: list[str] = []
        size = None
        color = None

        # name + description: `... = name : "description"`
        eq = rest.find("=")
        if eq >= 0:
            after = rest[eq + 1 :]
            name_part, _, desc_part = after.partition(":")
            name = name_part.strip()
            description = _unquote(desc_part)
        else:
            # `@BaseClass = Name` handled above; otherwise `= name`
            name_part, _, desc_part = rest.partition(":")
            name = name_part.strip()
            description = _unquote(desc_part)

        for attr in _ATTR_RE.finditer(rest):
            key = attr.group(1).lower()
            open_paren = rest.find("(", attr.start())
            depth, j = 0, open_paren
            while j < len(rest):
                if rest[j] == "(":
                    depth += 1
                elif rest[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            value = rest[open_paren + 1 : j]
            if key == "base":
                bases = [b.strip() for b in _split_args(value)]
            elif key == "size":
                size = _parse_size(value)
            elif key == "color":
                nums = re.findall(r"\d+", value)
                if len(nums) >= 3:
                    color = (int(nums[0]), int(nums[1]), int(nums[2]))

        entity = EntityClass(
            name=name or f"{kind}_{len(order)}",
            kind=kind.upper(),
            description=description,
            bases=bases,
            size=size,
            color=color,
        )

        # properties inside the body; nested '[' ... ']' blocks hold choice
        # lists for the preceding (flags) property
        if body_start >= 0:
            entity.properties.update(
                _parse_body(text[body_start + 1 : body_end], entity.name)
            )

        classes[entity.name] = entity
        order.append(entity.name)
        i = body_end + 1 if body_end >= 0 else match.end()

    return Fgd(classes, order)



def _matching_bracket(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _parse_body(body: str, class_name: str) -> dict[str, Property]:
    properties: dict[str, Property] = {}
    depth = 0
    current: Property | None = None

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if depth == 0 and line.startswith("["):
            depth = 1
            continue
        if depth > 0:
            if line.startswith("["):
                depth += 1
                continue
            if line.startswith("]"):
                depth -= 1
                if depth == 0:
                    current = None
                continue
            if current is not None:
                for entry in line.split(","):
                    bits = [b.strip() for b in entry.split(":")]
                    if len(bits) >= 2 and bits[0]:
                        current.choices.append((_unquote(bits[0]), _unquote(bits[1])))
            continue

        prop = _parse_property(line)
        if prop is not None:
            prop.defined_in = class_name
            properties.setdefault(prop.name, prop)
            if "flags" in prop.type.lower():
                current = prop

    return properties


def _parse_property(line: str) -> Property | None:
    if line.startswith(("base(", "size(", "color(", "studio(", "iconsprite(")):
        return None

    match = _PROP_RE.match(line)
    if not match:
        return None

    name = match.group(1)
    if name.lower() in ("base", "size", "color", "studio", "decal", "sidelist"):
        return None

    ptype = (match.group(2) or "string").strip()
    caption = _unquote(match.group(3) or "")
    default = _unquote(match.group(4) or "")
    description = _unquote(match.group(5) or "")

    prop = Property(
        name=name,
        type=ptype,
        caption=caption,
        default=default,
        description=description,
    )

    # choices / flags: `spawnflags(flags) = [ 1 : "Start on" : 0 ]` is written
    # across lines, so the caller sees the `[ ... ]` on the same logical line in
    # most FGDs; handle the inline case here.
    if "[" in line and "]" in line:
        block = line[line.index("[") + 1 : line.rindex("]")]
        for entry in block.split(","):
            bits = [b.strip() for b in entry.split(":")]
            if len(bits) >= 2:
                prop.choices.append((_unquote(bits[0]), _unquote(bits[1])))

    return prop


def parse_choices_blocks(text: str, fgd: Fgd) -> None:
    """Second pass: FGDs put `[ ... ]` choice blocks on their own lines."""
    text = _strip_comments(text)
    current_class = None
    pending_prop: Property | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        class_match = _CLASS_RE.match(line)
        if class_match:
            header = class_match.group(2)
            eq = header.find("=")
            if eq >= 0:
                current_class = header[eq + 1 :].split(":")[0].strip()
            pending_prop = None
            continue
        if current_class is None:
            continue

        if line.startswith("["):
            pending_prop = None
            continue
        if line.startswith("]"):
            pending_prop = None
            continue

        if pending_prop is None:
            candidate = _parse_property(line)
            if candidate is not None:
                entity = fgd.classes.get(current_class)
                if entity is not None:
                    prop = entity.properties.get(candidate.name)
                    if prop is not None and "flags" in prop.type.lower():
                        pending_prop = prop
            continue

        # inside a choice block
        for entry in line.split(","):
            bits = [b.strip() for b in entry.split(":")]
            if len(bits) >= 2 and bits[0]:
                pending_prop.choices.append((_unquote(bits[0]), _unquote(bits[1])))
        if line.endswith("]"):
            pending_prop = None


def load_fgd(path: str | Path) -> Fgd:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    fgd = parse_fgd(text)
    parse_choices_blocks(text, fgd)
    return fgd
