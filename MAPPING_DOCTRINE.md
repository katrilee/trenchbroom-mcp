# MAPPING DOCTRINE — Counter-Strike 1.6 / GoldSource (mandatory)

> Read before the first brush. Every rule here exists because breaking it produces
> a specific, reproducible failure in the BSP compiler or the engine.

---

## 0. Non-negotiables

1. **One writer.** Geometry is created only through MCP RPCs. Never hand-edit the
   `.map` file — the file watcher will reload it and you will lose the undo chain.
2. **Integer vertices.** Every structural brush vertex must land on an integer
   coordinate. Detail may go to `0.5`, never below.
3. **Seal before detail.** Do not texture, light or decorate a map that leaks.
4. **Look before you compile.** A greybox screenshot costs one RPC; a full `hlrad`
   costs minutes.
5. **One change, one transaction.** If an RPC fails, `doc.undo` returns you to a
   known-good state. Do not "fix forward" on a broken document.

---

## 1. Grid discipline

| Element | Grid | Why |
|---|---|---|
| Structural walls, floors, ceilings, corridor widths | **16** | Keeps AABB/hull alignment and texel density uniform; prevents slivers between adjacent brushes. |
| Door frames, window sills, stair treads | **8** | Tread rise must divide evenly into the 18-unit step limit. |
| Detail (trim, railings, crates) | **4** | Fine enough to read, coarse enough to stay integral after CSG. |
| `func_detail` / `func_wall` micro-brushes | 1 allowed | Only inside an entity that is clipped out of the BSP (see §6). |

**Never** use a 1-unit grid for structural geometry. A 1-unit offset on a wall
turns a clean 4-plane cut into a 12-plane bevel that survives into `clipnodes`
and cannot be sealed against its neighbour.

Rule of thumb: **if two brushes are meant to touch, they must share the exact
same coordinate** on the touching axis — not a value that rounds to it.

---

## 2. Sealing: flush, mitered, never overlapped

A map is sealed when there is no continuous path from the inside to the void
outside the world. Three legal ways to join two walls:

1. **Flush (butt joint)** — the side wall ends exactly on the face plane of the
   main wall. Cheapest, most common.
2. **Miter** — both walls are clipped at the 45° bisector. Use at corners where
   two thick walls meet, so the corner does not double-thickness.
3. **Overlap into solid** — the side wall penetrates the main wall and stops
   inside its solid volume. Safe for sealing, but the hidden faces must be
   removed or converted to detail (§6) or they cost `wpolys` and `clipnodes`.

**Illegal:** a *gap* (two brushes that "almost" touch — the classic leak), and
**coplanar overlap** (two brushes sharing a face plane with overlapping area,
which causes z-fighting and unpredictable face splitting).

### Do not box the whole map

Wrapping the entire level in one giant hollow cube "to be safe" is the single
most damaging habit available:

* every exterior face is a full `wpoly` in every leaf that can see it;
* VIS cannot cull anything, so `visdata` and render cost explode;
* `clipnodes` hit `MAX_MAP_CLIPNODES` (32767) long before geometry looks complex;
* any leak you *do* have is now invisible, because the skybox hides it.

Seal per region: each room's walls are the seal. There is no global shell.

---

## 3. Layer discipline (mandatory structure)

Every map region lives in a semantic layer so it can be hidden, queried and
transformed independently. Create these before the first brush:

```
Layer_TSpawn        Layer_CTSpawn      Layer_BombsiteA    Layer_BombsiteB
Layer_Mid           Layer_Connectors   Layer_Skybox       Layer_Lighting
Layer_Detail        Layer_Triggers     Layer_Logic
```

Rules:

* `layer.create` for each, then `layer.set_active` before building into it.
* A layer must be **self-contained**: no brush in `Layer_Mid` may also be the
  seal for `Layer_BombsiteA`.
* Spawn logic, buyzones and triggers live in `Layer_Triggers` / `Layer_Logic`,
  never mixed into geometry layers — that is what makes `select_by_bbox` and
  bulk transforms safe.
* Groups (`group.create`) are for *reusable assemblies* (a crate cluster, a
  door prop), not for regions. Region = layer, assembly = group.
* `Layer_Skybox` contains only the sky brushes; it is the last layer you build
  and the first one you check when diagnosing a leak.

---

## 4. The four-phase workflow

### Phase 1 — 2D tactical layout and timing

Build the *floor plan only*: room footprints, corridors, spawn points, bomb
sites. No heights beyond a single uniform room height (128 or 160).

Exit criteria (all via `render_tactical_blueprint` + `run_goldsource_audit`):

* every spawn reaches both sites (100 % A* reachability);
* `T_spawn → site` exceeds `CT_spawn → site` by **2–4 s**;
* no spawn-to-spawn sightline longer than 900 units;
* corridor widths ≥ 64 (single) and the main rotation route ≥ 128.

Do not proceed while a timing is wrong. Fixing timings later means moving walls,
which invalidates every seal you built.

### Phase 2 — 3D greybox and seal verification

Raise floors/ceilings, add doors and windows, run **`hlcsg` + `hlbsp` only**
(`mode="fast"`).

Exit criteria:

* `=== LEAK in hull 2 ===` absent from the log;
* if a leak exists, load the `.pts` pointfile (`view.load_pointfile`) and look at
  the red line in 3D — the leak is *always* where the line crosses a brush edge;
* standing hull (32×32×72) fits every corridor and doorway;
* no spawn origin intersects a solid brush.

Repeat until zero leaks. Never run `hlrad` on a leaking map.

### Phase 3 — Cover, verticality, textures

Add crates, stairs, ladders, ledges. Apply semantic WAD textures (floor vs wall
vs ceiling by face normal). Inspect every room with `inspect_room_4_angles`.

Exit criteria:

* every vertical transition is legal: ≤ 18 step, ≤ 45 jump, ≤ 62 crouch-jump;
* crates are 32/64 grid aligned so they read as cover, not as clutter;
* no face uses a trigger texture (`AAATRIGGER`, `CLIP`, `SKY`) unless it is
  supposed to be invisible/impassable;
* texture scale is 0.25–4.0 (outside that range → `Bad surface extents`).

### Phase 4 — Lighting and final polish

`light_environment` for sun/sky, plus point/surface lights indoors. Run the full
pipeline (`hlvis` + `hlrad`).

Exit criteria:

* `wpoly < 800`, `epoly < 4000`;
* `clipnodes < 80 %` of 32767;
* `MAX_MAP_LIGHTING` not exceeded (`AllocBlock: full` absent);
* every screenshot is readable — a dark corner is a bug, not an aesthetic;
* no light leaks through a `func_wall` (see pitfalls §4).

---

## 5. Verticality numbers (memorise these)

| Transition | Max | Note |
|---|---|---|
| Walkable step | **18** | 16-unit stairs are the only reliable choice. |
| Normal jump | **45** | Crouch-jump reaches ~62. |
| Crouch-jump | **62** | Design intent: 48 = "needs a jump", 56 = "needs a crouch-jump". |
| Walkable slope | **45°** | Steeper needs a ladder or becomes a slide. |
| Corridor | **64** wide / **80–96** tall | 128 wide for two-way rotation. |
| Ceiling over a jump | 72 + 45 ≈ **120** | Otherwise the jump is capped. |

Flag any intended traversal in the 19–44 band as "requires jump" and 46–62 as
"requires crouch-jump" — do not let the player discover it by accident.

---

## 6. Detail brushes and clipnodes

A brush that is not part of the seal must not be in the BSP as structure.

* Small (< 16 units) decorative geometry → `func_wall` or `func_illusionary`,
  or `func_detail` where the game supports it.
* Every `func_wall` needs a matching **clip brush** if the player must not walk
  through it: the visual brush is non-solid, the clip brush is invisible.
* `MAX_MAP_CLIPNODES` (32767) is reached by *brush count*, not by visual
  complexity. 4000 tiny brushes will kill a map that looks simple.

---

## 7. Texture and entity rules

* `AAATRIGGER` — never visible. Any visible face with it is a bug.
* `CLIP` — invisible, solid. Use for player blocking, never for visuals.
* `SKY` — only on `Layer_Skybox` brushes, and only as an outward seal.
* `NULL` / `nodraw` on faces that are completely enclosed (inside a wall).
* `func_detail` creatures and models need `mp_consistency 0` when custom models
  are used.
* `light_environment` needs `_sunlight`, `_sunlight_color`, `angles` and a
  `_light` entity for indoor fill. A map with only `light_environment` will have
  black interiors.

---

## 8. Failure handling

| Symptom | Do this |
|---|---|
| Leak | `view.load_pointfile`, screenshot the red line, fix the exact gap, re-fast-compile. |
| `Bad surface extents` | Texture scale on that face is extreme; reset UV to world (`reset_uv_to_world`) or fix brush size. |
| `AllocBlock: full` | Too many distinct lightmaps; increase texture scale or simplify subdivision. |
| `MAX_MAP_CLIPNODES` | Convert micro-brushes to entities + clip brushes. |
| Non-convex brush | TrenchBroom's issue list (`val.get_issues`) names it; revert the clip/vertex edit that caused it. |
| Entity missing from game | `.fgd` not loaded or classname typo; `fgd.inspect_class` to confirm. |

---

## 9. Definition of done

A map ships only when: 0 leaks, 0 issues from `val.get_issues` that are not
explicitly waived, timings within the CT/T band, all four phase criteria met,
`wpoly < 800`, `clipnodes < 80 %`, and `engine.launch_and_verify` reports a clean
`qconsole.log`.
