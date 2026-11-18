# Architecture — Autonomous GoldSource Level Design System (TrenchBroom v2026.2)

Target pinned to **TrenchBroom tag `v2026.2`** (commit `4c756be`).
Toolchain: **C++20, CMake ≥ 3.25, Qt ≥ 6.10, vcpkg** (as required by that release).

Four tiers plus an enhancement layer. The LLM only ever talks to **Tier 3**.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Tier 4  MAPPING_DOCTRINE.md / KNOWN_BSP_PITFALLS.md                  │
└───────────────────────────────▲──────────────────────────────────────┘
┌───────────────────────────────┴──────────────────────────────────────┐
│ Tier 3  TrenchBroom-MCP-Server (Python, FastMCP)                     │
└───────▲───────────────────────────────────────────▲──────────────────┘
        │ NDJSON JSON-RPC 127.0.0.1:6161            │ subprocess
┌───────┴──────────────────────────┐   ┌────────────┴──────────────────┐
│ Tier 1  TrenchBroom C++ bridge   │   │ Tier 2  GoldSource engine     │
│         lib/TbUiLib              │   │         tools/goldsource_engine│
└──────────────────────────────────┘   └───────────────────────────────┘
```

---

## What changed from the 2024 layout (audit result)

The 2026 release is not a rename; the model layer was split out of the UI and
the document API became free functions.

| concept | 2024.1 | 2026.2 |
|---|---|---|
| namespaces | `TrenchBroom::View`, `TrenchBroom::Model` | `tb::ui`, `tb::mdl` |
| headers | `common/src/View/X.h` | `lib/TbUiLib/include/ui/X.h` |
| window class | `MapFrame` | **`MapWindow`** |
| document ownership | `shared_ptr<MapDocument>` | `std::unique_ptr<MapDocument>` owned by the window |
| editing API | methods on `MapDocument` | **free functions** on `mdl::Map` (`mdl/Map_Geometry.h`, `Map_Nodes.h`, …) |
| transaction | `View::Transaction{doc}` | `mdl::Transaction{map}` |
| selection | `NodeCollection` | `mdl::Selection` with public vectors |
| textures | `Assets::TextureManager` | `gl::MaterialManager` (materials, not textures) |
| face edit | `ChangeBrushFaceAttributesRequest` | `UpdateBrushFaceAttributes` + `ValueOp`/`FlagOp` variants |
| entity defs | `Assets::EntityDefinitionManager` | `mdl::EntityDefinitionManager` + `mdl::getType()` |
| map format | `world->mapFormat()` | `map.worldNode().mapFormat()` |
| clip | `MapDocument::clipBrushes` | **removed** — `mdl::BrushFace::create` + `Brush::clip` |
| save | `saveDocumentAs` | `map.saveAs()` / `map.saveTo()` / `map.save()` |
| game config | `GameFactory` | `mdl::GameManager` + `mdl::GameInfo` |
| new document | `MapFrame::newDocument` | `MapDocument::createDocument(...)` + `MapWindowManager::createMapWindow(...)` |

The bridge was rewritten from scratch against this API. Nothing from the 2024
files carried over verbatim.

---

## Tier 1 — TrenchBroom IPC bridge

**Files** (all new, all in `lib/TbUiLib`):

| file | lines | role |
|---|---|---|
| `include/ui/McpServerBridge.h` / `src/McpServerBridge.cpp` | 233 / ~2 900 | TCP server, JSON-RPC dispatch, 44 handlers |
| `include/ui/McpNodeIndex.h` / `src/McpNodeIndex.cpp` | 59 / 210 | stable node identifiers |
| `include/ui/McpJson.h` / `src/McpJson.cpp` | 62 / 120 | `QJsonValue` ⇄ `vm::vec3d` / `vm::bbox3d` |
| `patches/0001-...patch` | 86 | CMake sources + 3 integration points |

Three integration points, all minimal and documented in the patch:

1. `MapWindow` gains `AppController& appController() const` — the bridge needs
   `GameManager`/`GlManager` to create documents without UI dialogs;
2. `MapWindowManager::createMapWindow` moved from `private` to `public`;
3. `MapWindow`'s constructor calls `McpServerBridge::attachTo(*this)`.

### Threading

`QTcpServer` on the GUI thread; `readyRead` only frames and queues; every
handler runs from `QMetaObject::invokeMethod(..., Qt::QueuedConnection)`. So
`mdl::Map` and the GL contexts are touched only on the GUI thread, and a long RPC
cannot re-enter the socket parser.

### Undo

Every mutating handler opens `mdl::Transaction{map, "..."}`. Transactions nest
and merge on commit, so one RPC = one undo step even when it calls helpers that
open their own (`createPointEntity`, `csgHollow`, …).

### Node identity

`mdl::Node` has no persistent id and commands recreate nodes, so ids are derived
from content: `e:<fnv1a64(props)>`, `b:<fnv1a64(1/16-quantised verts + materials)>`,
`g:<...>`, with `-2/-3` ordinals for identical siblings. Ids survive undo/redo and
save/reload; destructive edits can retire one, and clients must handle
`id_not_found`.

### RPC surface (44 methods, protocol v2)

* **document** — `doc.new_or_open`, `doc.state`, `doc.save_map`, `doc.undo`,
  `doc.redo`, `doc.clear_selection`, `doc.select_by_id`, `doc.select_by_bbox`,
  `doc.select_all`, `doc.selection`, `doc.list_nodes`
* **geometry** — `geo.create_cuboid`, `geo.create_room_hollow`,
  `geo.csg_subtract`, `geo.csg_union`, `geo.csg_intersect`, `geo.clip_brush`,
  `geo.transform`, `geo.delete_selected`, `geo.duplicate_selected`
* **materials** — `tex.load_wad`, `tex.list_textures`,
  `tex.apply_to_brush_or_face`, `tex.set_properties`
* **entities** — `ent.create_point_entity`, `ent.create_brush_entity`,
  `ent.set_property`, `ent.list`, `prefab.paste`
* **view** — `view.capture_3d`, `view.capture_orthographic`,
  `view.load_pointfile`, `view.set_layout`, `view.camera_info`
* **validation** — `val.get_issues`
* **organisation** — `layer.create`, `layer.set_active`, `layer.list`,
  `group.create`, `group.ungroup`
* **snapshots** — `snapshot.save`, `snapshot.list`, `snapshot.restore`
* **meta** — `rpc.ping`, `rpc.methods`

Wire names keep the historical `tex.*` prefix for material operations so the
Python tier and doctrine stay stable across the 2024→2026 move.

### 2026-specific behaviour

* `doc.new_or_open` builds the document through
  `MapDocument::createDocument` / `loadDocument` and hands it to
  `MapWindowManager::createMapWindow`, then retargets the singleton bridge. No
  modal dialogs, no preference guessing: the format is explicit.
* **`csgHollow` still derives wall thickness from the grid size.** The handler
  sets `map.grid().setSize(thickness)`, hollows, restores; only powers of two
  1…128 are accepted.
* **`csgSubtract` still consumes the selected brushes as subtrahends.** The
  handler selects only the cutter.
* `geo.clip_brush` is implemented locally, because 2026 removed
  `MapDocument::clipBrushes`: build a `BrushFace` from the three points, clip
  each selected brush, replace the nodes inside one transaction.
* `layer.list` reports the active layer from
  `map.editorContext().currentLayer()` — 2026 exposes it there, not on `Map`.
* `view.capture_3d` reaches the camera through `MapViewBase*` because
  `MapView3D::camera()` is private in this release.
* Snapshots use `map.saveTo(path)` (const, does not change the document path);
  restore uses `MapDocument::load`, which clears the command processor — that is
  the point of a restore.
* Auto-reload watches the active `.map` with `QFileSystemWatcher`, debounces
  300 ms, ignores its own writes by mtime, re-arms after inode replacement, and
  reloads through `MapDocument::reload()`.

---

## Tier 2 — GoldSource spatial / compiler engine (Python)

```
tools/goldsource_engine/
  geometry.py                # built: .map parser + exact convex brush math
  physics_validator.py       # hull / step / jump / spawn clearance
  nav_and_sightline_analyzer.py
  blueprint_renderer.py      # PNG (Pillow) + SVG (exact footprints)
  vhlt_compiler.py           # hlcsg → hlbsp → hlvis → hlrad + .log/.pts parsing
  fgd.py                     # @BaseClass inheritance resolution
  engine_verify.py           # server BSP load + console log watch
```

`geometry.py` is version-independent of TrenchBroom: it parses Valve 220 `.map`
text into half-space brushes, computes vertices by plane-triple intersection,
does exact SAT for hull-vs-brush overlap, and projects convex footprints for
blueprints.

## Tier 3 — FastMCP server

Macro tools compose atomic RPCs (`build_room`,
`connect_rooms_with_corridor`, `carve_doorway`, `place_staircase`,
`place_crate_cluster`, `seal_skybox_shell`) so the model emits one call per
intent; inspection tools return images, not geometry.

## Tier 4 — Doctrine

`MAPPING_DOCTRINE.md` and `KNOWN_BSP_PITFALLS.md` are engine-side and therefore
unchanged by the TrenchBroom version.

---

## Verification status

* Every bridge translation unit is compiled with the **real v2026.2 headers**
  and **Qt 6.10.0**, with the project's warning set
  (`-Wall -Wextra -Wshadow -Wold-style-cast -Wconversion -Wsign-conversion
  -Wuseless-cast -Wduplicated-cond …`): **0 errors, 0 warnings from the bridge
  sources** (pre-existing header warnings are excluded, as they also appear when
  compiling untouched TrenchBroom files).
* The integration patch applies cleanly to a pristine `v2026.2` checkout.
* **Not verified:** a full link/run of TrenchBroom. That needs vcpkg
  (assimp/freeimage/openexr) which is a multi-hour build on this 4-core box;
  compilation and API conformance are verified, runtime behaviour is not.
