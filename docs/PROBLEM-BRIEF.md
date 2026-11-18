# Problem Brief — Autonomous CS 1.6 Map Generation via TrenchBroom + MCP

## 1. Goal

Build a system where an AI agent autonomously creates playable Counter-Strike 1.6
(GoldSource / BSP v30) maps: geometry, texturing, lighting, validation, visual
inspection and compilation — end to end.

Intended architecture (4 tiers):

1. **Tier 1** — a C++ JSON-RPC bridge compiled *into* TrenchBroom (44 methods:
   document, CSG, materials, entities, viewport capture, validation, layers,
   groups, snapshots). Listens on 127.0.0.1:6161.
2. **Tier 2** — a Python "GoldSource engine": `.map` parsing, player-hull physics
   validation, A* navigation + sightlines, 2D blueprint/SVG export, VHLT compiler
   driver with `.log`/`.pts` leak parsing, FGD parser.
3. **Tier 3** — a Python FastMCP server exposing high-level tools to the LLM.
4. **Tier 4** — doctrine files (`MAPPING_DOCTRINE.md`, `KNOWN_BSP_PITFALLS.md`).

## 2. What already exists and is verified

* **Tier 1 bridge rewritten for TrenchBroom `v2026.2`** (the API changed
  radically between 2024 and 2026: `MapFrame`→`MapWindow`, methods→free
  functions on `mdl::Map`, `TextureManager`→`MaterialManager`, namespaces
  `TrenchBroom::View`→`tb::ui`). ~3,600 lines in `lib/TbUiLib/`.
  * Compiles with **0 errors / 0 warnings from our sources** against the real
    v2026.2 headers and **Qt 6.10.0**, using the project's own warning set
    (`-Wall -Wextra -Wold-style-cast -Wconversion -Wsign-conversion
    -Wuseless-cast -Wduplicated-cond …`).
  * Integration is a 3-hunk patch (86 lines): CMake sources, an
    `AppController&` getter on `MapWindow`, `createMapWindow` made public, and
    `attachTo(*this)` in the constructor.
  * Patch applies cleanly to a pristine v2026.2 checkout (`patch --dry-run`).
  * **Not done:** a full link/run. Compilation and API conformance only.
* **Tier 2 started:** `geometry.py` (632 lines) — Valve-220 `.map` parser,
  half-space brush solids, exact SAT hull-vs-brush overlap (with an `inset`
  parameter separating "touching" from "embedded"), convex footprints, cuboid
  construction, `.map` round-trip. Tested and working.
* **Tier 4 written:** `MAPPING_DOCTRINE.md`, `KNOWN_BSP_PITFALLS.md`.
* **Tier 3 not started.**

## 3. The blocker

**TrenchBroom cannot be built or run on the target machine in reasonable time.**

### 3.1 Environment (measured, not assumed)

| item | value |
|---|---|
| OS | Ubuntu 22.04 (VDS, VMware) |
| glibc | 2.35 |
| GCC / libstdc++ | 11.4 / `GLIBCXX_3.4.30` |
| CPU / RAM / disk | 4 cores / 3.9 GB / 18 GB free |
| Qt | 6.2 (apt) — too old; **6.10.0 installed separately** via `aqtinstall` to `/opt/Qt/6.10.0/gcc_64` |
| CMake | 3.22 (apt), **4.4.5** installed via pip |
| assimp / fmt | 5.2 / 8.1 from apt; **fmt 11** headers cloned from source |
| CS 1.6 server | ReHLDS running at `/srv/cs16/mentzer` with `halflife-cs.fgd` + hundreds of WADs |
| VHLT/ZHLT compilers | **missing** (must be downloaded) |
| `hl.exe` client | **missing** (no Windows client on this host) |
| Docker / Podman | **not installed**; `proot` and `debootstrap` are available in apt |

### 3.2 The official v2026.2 Linux binary does not run here

Downloaded `TrenchBroom-Linux-x86_64-v2026.2-Release.zip` (60.8 MB, AppImage):

```
GLIBCXX_3.4.31 not found   /   GLIBCXX_3.4.32 not found
```

The AppImage does not bundle `libstdc++`. Substituting Ubuntu 24.04's
`libstdc++` (GLIBCXX 3.4.33) then fails with:

```
GLIBC_2.38 not found
```

So the official 2026.2 build requires a newer distro than Ubuntu 22.04.
(For reference, the v2024.1 release *does* ship an `ubuntu-22.04` `.deb`; it runs
on this machine — verified, window opens under Xvfb. But it is the wrong
version for our code.)

### 3.3 Even if it ran, it could not host the bridge

TrenchBroom has **no plugin or scripting API** (checked the v2026.2 tree: no
Lua/scripting/plugin system). The bridge is a compiled-in class inside
`MapWindow`. A stock binary cannot be extended.

### 3.4 Building from source on this host

vcpkg is used by upstream and is a git submodule; its dependencies
(assimp, freeimage, openexr, freetype, cpptrace, ctre, miniz, stduuid, fmt,
tinyxml2, Catch2) must be built from source. On 4 cores / 3.9 GB RAM this is
estimated at **2–4 hours** with OOM risk on heavy translation units.

Bypassing vcpkg with system libraries was attempted. Results:
* CMake configure now proceeds past `pandoc` (bypassed with
  `-DPANDOC_PATH=/bin/true`) and stops at **cpptrace**.
* Missing CMake package configs: `cpptrace`, `ctre`, `miniz`, `stduuid`,
  `freeimage`, `fmt` (>= 10), `Catch2`; `freetype`/`tinyxml2` shims exist in
  `cmake/packages/` but are **not referenced** by the 2026 build.
* All of these are small and vendorable, but the total shim surface is ~10
  packages, not the 4 originally estimated.

## 4. The design question

The bridge is **convenience, not necessity**. An MCP server can operate directly
on `.map` files on disk instead of driving a live TrenchBroom.

| capability | with bridge | without bridge (file-based) |
|---|---|---|
| produce/parse `.map` | yes | yes (already written) |
| CSG hollow / subtract / clip | TrenchBroom's battle-tested half-space math | must be reimplemented in Python |
| undo | one RPC = one undo step | versioned `.map_history` snapshots |
| 3D visual feedback | real textured viewport capture | self-written greybox renderer (~300 lines) |
| BSP compile (VHLT) | yes (independent) | yes (independent) |
| physics / A* / sightlines | yes (independent) | yes (independent) |

Proposed refinement: give the MCP server two interchangeable backends
(`TrenchBroomBackend` over JSON-RPC, `FileBackend` in pure Python) behind one
`LevelEditor` interface, so the system works today and gains TrenchBroom quality
later without changing tool contracts.

## 5. Questions for review

1. Is reimplementing CSG (hollow, subtract, clip, convex merge) in Python on
   half-space brushes realistic and safe enough for GoldSource maps, or is
   TrenchBroom's geometry engine effectively mandatory for correctness
   (sliver brushes, non-convex results, `MAX_MAP_CLIPNODES` blowups)?
2. Is there a better way to get TrenchBroom 2026.2 running on Ubuntu 22.04
   than (a) building from source with vcpkg, (b) building in an Ubuntu 24.04
   rootfs via `proot`/`chroot`, or (c) building on a Windows machine?
   (No Docker available; `debootstrap`/`proot` are installable.)
3. Can vcpkg binary caching (public/self-hosted feeds) realistically cut the
   multi-hour dependency build to minutes here?
4. Is a self-written greybox renderer an acceptable substitute for real
   viewport screenshots as the agent's visual feedback loop, or does that
   leave a quality gap that will surface as unplayable maps?
5. Anything structurally wrong with the 4-tier split, or with compiling an RPC
   bridge into `MapWindow` instead of driving TrenchBroom externally (e.g.
   Qt automation, or a separate process using the same libraries)?

## 6. Constraints

* Everything must run on this single VDS where possible; a Windows machine is
  available but only as a secondary option.
* The AI must not write raw plane equations or corrupt `.map` files.
* Reports must be honest: no claim of runtime verification without evidence.
