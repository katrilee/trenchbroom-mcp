# KNOWN BSP PITFALLS — GoldSource BSP v30 error memory

Format: **symptom** → cause → *automated fix* (what the engine/agent does) →
`manual fix` when automation cannot decide.

Engine budgets referenced below (BSP v30 hard limits):

| Resource | Limit |
|---|---|
| `MAX_MAP_CLIPNODES` | 32 767 |
| `MAX_MAP_NODES` | 65 535 |
| `MAX_MAP_LEAFS` | 8 192 |
| `MAX_MAP_FACES` | 65 535 |
| `MAX_MAP_VERTS` | 65 535 |
| `MAX_MAP_TEXINFO` | 8 192 |
| `MAX_MAP_PLANES` | 32 767 |
| `MAX_MAP_LIGHTING` | 1 MiB |
| `MAX_MAP_VISDATA` | 512 KiB |
| `MAX_MAP_ENTSTRING` | 64 KiB |

The audit reports every one of these as a percentage, not as a pass/fail, so
budget drift is visible long before a compile fails.

---

## 1. Leaks and hulls

### 1.1 `=== LEAK in hull 2 ===`

**Cause.** Hull 2 is the *ducking* hull (32×32×36). A gap wide enough for a
crouched player but not a standing one leaks **only** in hull 2. This is the most
common "but it looks sealed" bug: a 20-unit slit under a `func_wall`, a doorway
whose frame does not reach the floor, or a wall that ends 8 units above the
floor.

**Automated fix.** Parse the emitted `.pts`, take the first point **outside** the
documented world bounds, snap it to the nearest brush face plane, and report
`nearest_entity`, distance and the face it exited through. The agent loads it via
`view.load_pointfile` and screenshots along the line.

**Manual fix.** Close the gap with a brush that shares the *exact* coordinate of
its neighbour. Do not "widen the skybox".

### 1.2 Leak in hull 1 but not hull 0

**Cause.** Hull 0 is a point trace. It can pass through a 1-unit gap that hull 1
(32×32×72) cannot — but the inverse also happens: hull 0 finds a hole in a corner
where hulls 1/2 are blocked, because hull 0 tests the *exact* geometry while the
player hull is expanded by 16 units on each side.

**Consequence.** A map that is "sealed" per `hlbsp` can still let the player fall
out of the world at a corner if the corner is thinner than the player hull.

**Automated fix.** `physics_validator` traces the *expanded* standing hull
through every doorway and corner and reports any place where the 32×32×72 box
cannot pass but the geometry appears open.

### 1.3 `Leaf portal saw into leaf`

**Cause.** A brush face lies exactly on a BSP split plane, or two brushes share a
coplanar overlapping face. The compiler's portalisation becomes ambiguous.

**Automated fix.** Detect coplanar overlap between brush face pairs (same plane
normal + distance, overlapping projected polygons) and report both brush ids.

**Manual fix.** Offset one brush by 1 unit *inward into solid* (never outward), or
miter the joint.

### 1.4 Map "sealed" but entities fall into the void

**Cause.** The floor brush is a `func_wall` (non-solid to the BSP seal) or a
`func_illusionary`. Only **worldspawn** brushes seal.

**Automated fix.** `physics_validator` requires every downward-facing structural
face under a spawn or path node to belong to worldspawn, not to an entity.

---

## 2. Surface and lightmap errors

### 2.1 `Bad surface extents`

**Cause.** A face's texture projection spans more than the engine's per-face
texture extent budget. Almost always caused by a texture scale far below 1.0
(stretching one tile over a 1024-unit wall) or by a rotated projection on a very
large face.

**Automated fix.** Walk every brush face, evaluate `worldextent / scale`; flag
faces whose projected extent exceeds the budget and emit the exact brush id +
face index. Offer `tex.apply_to_brush_or_face(..., reset_uv_to_world=true)`.

**Manual fix.** Split the wall into ≤ 256-unit segments, or raise the scale to
0.25–4.0.

### 2.2 `AllocBlock: full` / `MAX_MAP_LIGHTING` exceeded

**Cause.** Too many distinct lightmaps, not too much light. Every face with a
unique scale/offset needs its own allocation slot.

**Automated fix.** Report the top-10 brushes by lightmap count. Suggest, in
order: unify texture scale across one wall, split oversized faces, convert
non-structural detail to `func_detail` (lightmaps are still allocated, but the
face count drops).

### 2.3 Black interiors despite `light_environment`

**Cause.** `light_environment` only lights surfaces that face the sky; indoor
faces are lit only by entity lights. A map with no `light` entities has black
rooms.

**Automated fix.** `val.get_issues` + a scene query: any leaf with zero light
entities within N units and no sky-facing surface is reported as unlit.

---

## 3. Clipnodes and brush count

### 3.1 `MAX_MAP_CLIPNODES` exceeded

**Cause.** Clipnodes are generated per brush per hull — roughly `faces × hulls`.
Micro-brushes (trim, railings, bolts) generate as many clipnodes as a wall.

**Automated fix.** Report brush-count and estimated clipnode budget. Convert
brushes whose bbox is smaller than 16 units into `func_wall` + a single clip
brush, or delete if decorative.

### 3.2 Compile succeeds but the map is unplayable (invisible walls)

**Cause.** `func_wall` with no clip brush: the visual brush exists, but the BSP
generated no collision for it. Conversely a `CLIP` brush with a visible texture
shows the `CLIP` texture in game.

**Automated fix.** For every `func_wall` / `func_illusionary`, require a
co-located `CLIP`-textured brush in `Layer_Triggers` when the doctrine marks the
volume as blocking.

---

## 4. Lighting bleed through `func_wall`

**Cause.** Light in GoldSource is computed per *surface*, but passes through
non-structural brushes when their faces are not properly closed. A `func_wall`
sitting flush against a wall behaves as if it were not there for `hlrad`.

**Automated fix.** Require ≥ 1 unit clearance between a `func_wall` and the
structural wall behind it, and flag flush placements.

**Manual fix.** Pull the `func_wall` 1–2 units off the wall, or make it structural.

---

## 5. Texture-flag rules

| Texture | Behaviour | Rule |
|---|---|---|
| `AAATRIGGER` | Never rendered | Any *visible* face using it is a bug; only on `trigger_*` volumes. |
| `CLIP` | Invisible, solid | Collision only. Never on a rendered surface. |
| `SKY` | Skybox, seals | Only on `Layer_Skybox`, outward-facing. |
| `NULL` | Discarded | Faces fully enclosed inside solid geometry. |
| `{`-prefixed | Masked/transparent | Must not be used where z-fighting matters. |
| `+0`/`+A`-suffixed | Animated (`+0frame`) | All frames must exist in the WAD or the face renders as `missing`. |

**Automated check.** `tex.apply_to_brush_or_face` validates the texture exists in
a mounted WAD before applying; `val.get_issues` reports faces with
`AAATRIGGER` on outward-facing surfaces.

---

## 6. Entity and FGD pitfalls

### 6.1 Entity silently missing in game

**Cause.** Classname not in the loaded `.fgd`, or the `.fgd` was not loaded at
all.

**Automated fix.** `fgd.inspect_class` resolves `@BaseClass` chains and returns
the merged property dictionary; `ent.create_point_entity` refuses unknown
classnames with the loaded FGD's class list in the error message.

### 6.2 `light_environment` has no effect

**Cause.** Missing `_sunlight` / `angles`, or the sky surface is not actually on
the world (a `func_wall` sky brush does not light the map).

### 6.3 `info_player_start` stuck in floor

**Cause.** Origin placed at the brush surface instead of 36 units above it; the
player hull is 32×32×72 with the origin at the centre, so 36 units of clearance
are needed below the origin.

**Automated fix.** `physics_validator` checks every spawn origin against the
solid set with the expanded hull and requires ≥ 36 units of free space below.

---

## 7. CSG-specific traps

| Trap | Symptom | Fix |
|---|---|---|
| Hollow with the wrong grid | Wall thickness = grid size, not a parameter | `geo.create_room_hollow` sets the grid explicitly; only powers of two 1–128 accepted. |
| Subtract with the target selected | Everything disappears | Select **only** the cutter; `csgSubtract` expands to touching brushes itself. |
| Union of non-touching brushes | One brush vanishes | Remove the gap first; convex merge needs overlapping/touching volumes. |
| Clip with collinear points | Silent no-op / corrupt brush | Reject with `clip points are collinear`. |
| Non-integer vertices after CSG | `NonIntegerVertices` validator fires | Snap with `geo.transform(grid=16)`. |

---

## 8. Automation map (what the agent does without asking)

| Trigger | Automatic action |
|---|---|
| Leak in any hull | Parse `.pts`, return exit point + nearest entity + fix hint |
| `Bad surface extents` | Identify face, offer world-aligned UV reset |
| `AllocBlock: full` | Top-10 lightmap offenders, suggest scale unification |
| Clipnodes > 80 % | List micro-brushes, offer `func_wall` + clip conversion |
| Non-convex brush | Name node id from `val.get_issues`, refuse further CSG on it |
| Unknown classname | Return resolved FGD class list |
| Spawn intersecting solid | Report exact spawn id + clearance deficit |

Anything not in this table is escalated to the user rather than guessed.
