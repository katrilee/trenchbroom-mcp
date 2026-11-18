# TrenchBroom v2026.2 + MCP Bridge — Windows Build Guide

The bridge is **compiled into** TrenchBroom (`MapWindow` constructor). There is no
plugin/script API in TrenchBroom, so a stock release binary cannot be extended.
You build once, then the bridge listens on `127.0.0.1:6161` for the whole session.

> Tested for API conformance against the v2026.2 tree with GCC 11 + Qt 6.10.
> MSVC has not been exercised yet — if the compiler complains, send me the
> error text and I will fix it.

---

## 0. What this gives you

* TrenchBroom v2026.2, unmodified except three integration points.
* A JSON-RPC endpoint on `127.0.0.1:6161` with 44 methods (document, geometry,
  CSG, materials, entities, viewport capture, validation, layers, groups,
  snapshots).
* Everything runs on the GUI thread, safely; every mutation is one undo step.

It does **not** give you an MCP server. That is Tier 3 (Python), which talks to
this TCP endpoint. Any script that can open a TCP socket can drive it now.

---

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Windows | 10/11 x64 | |
| Visual Studio 2022 | 17.8+ | workload **Desktop development with C++** |
| Git for Windows | any | gives you `git` + Git Bash (`patch`) |
| CMake | **≥ 3.25** | the VS installer's bundled CMake is usually new enough |
| Qt | **6.10.x** | official installer, pick `msvc2022_64` |
| Disk | ~20 GB free | vcpkg deps are large |

Qt 6.10 via [the online installer](https://www.qt.io/download-qt-installer)
(account required) → install to e.g. `C:\Qt\6.10.3\msvc2022_64`.

---

## 2. Get the source at the right tag

```bash
git clone https://github.com/TrenchBroom/TrenchBroom.git tb
cd tb
git checkout v2026.2
git submodule update --init --recursive     # vcpkg lives here
```

`vcpkg` **must** be populated: TrenchBroom's CMake sets
`CMAKE_TOOLCHAIN_FILE` to it unconditionally.

---

## 3. Drop in the bridge

From Git Bash, with this repository available:

```bash
cd /c/src/tb
/path/to/trenchbroom-mcp/tb-bridge/apply.sh .
```

The script does exactly this (do it by hand if you prefer):

1. `git apply` `patches/0001-build-and-mapwindow-integration.patch`
   * adds `McpJson.cpp`, `McpNodeIndex.cpp`, `McpServerBridge.cpp` to
     `lib/TbUiLib/CMakeLists.txt`
   * adds `AppController& appController() const` to `MapWindow`
   * moves `MapWindowManager::createMapWindow` to `public`
   * calls `McpServerBridge::attachTo(*this)` at the end of the `MapWindow`
     constructor
2. copies the three headers into `lib/TbUiLib/include/ui/` and the three
   sources into `lib/TbUiLib/src/`.

---

## 4. Configure and build

```bash
cmake -S . -B build -G "Visual Studio 17 2022" -A x64 ^
      -DCMAKE_BUILD_TYPE=Release ^
      -DCMAKE_PREFIX_PATH="C:/Qt/6.10.3/msvc2022_64"

cmake --build build --config Release --target TrenchBroom -j
```

First configure bootstraps vcpkg and builds assimp, freeimage, openexr,
freetype, … — **this is the slow part** (30–90 min). Later builds are minutes.

The executable lands around
`build\app\TrenchBroom\Release\trenchbroom.exe`
(exact folder is printed at the end of the build).

---

## 5. Run and verify

Launch it. Then in PowerShell:

```powershell
$c = New-Object Net.Sockets.TcpClient('127.0.0.1',6161)
$s = $c.GetStream()
$w = New-Object IO.StreamWriter($s); $w.AutoFlush = $true
$r = New-Object IO.StreamReader($s)
$w.WriteLine('{"jsonrpc":"2.0","id":1,"method":"rpc.ping","params":{}}')
$r.ReadLine()
```

Expected: a JSON object with `protocolVersion: 2`, `trenchbroomVersion: "2026.2"`
and the full method list.

If the port is not open:

```cmd
netstat -ano | findstr :6161
```
and check the TrenchBroom console / log for
`"bridge is not attached"` (means `attachTo` was not called) or a bind error
(port taken).

---

## 6. Driving it

Newline-delimited JSON, one request per line:

```json
{"jsonrpc":"2.0","id":1,"method":"doc.new_or_open","params":{
  "game":"Cstrike","game_path":"C:/Half-Life/cstrike",
  "fgd":"C:/Half-Life/cstrike/halflife-cs.fgd",
  "wads":["C:/Half-Life/cstrike/cstrike.wad"],
  "map_format":"valve"}}

{"jsonrpc":"2.0","id":2,"method":"geo.create_room_hollow","params":{
  "bounds":{"min":[-256,-256,0],"max":[256,256,128]},
  "wall_thickness":16,
  "wall_tex":"C1A0_WALL","floor_tex":"C1A0_FLOOR","ceiling_tex":"C1A0_CEILING"}}

{"jsonrpc":"2.0","id":3,"method":"view.capture_3d","params":{
  "pos":[0,0,64],"look_at":[256,0,64],"path":"C:/tmp/room.png"}}
```

---

## 7. Environment variables

| variable | meaning |
|---|---|
| `TB_MCP_PORT` | listen port (default `6161`) |
| `TB_MCP_ENABLE=0` | do not start the bridge |
| `TB_MCP_TOKEN` | if set, every request must carry `"auth": "<token>"` |
| `TB_MCP_CAPTURE_DIR` | default output folder for screenshots |
| `TB_MCP_SNAPSHOT_DIR` | where `snapshot.save` writes `.map` files |

The bridge binds **loopback only**. If you want the AI to run on another machine,
that is a code change (bind address + token), not a config switch — ask first.

---

## 8. Common problems

| symptom | cause |
|---|---|
| `GLIBCXX` / `GLIBC` errors | you are running the Linux AppImage on an old distro — irrelevant on Windows |
| CMake cannot find Qt6 | `-DCMAKE_PREFIX_PATH` must point at the `msvc2022_64` folder, not `C:/Qt` |
| vcpkg bootstrap fails | submodules not initialised; re-run step 2 |
| Bridge silent, no error | `attachTo` guarded off by `TB_MCP_ENABLE=0` in the environment |
| `doc.new_or_open` says *unknown game* | `game` must match a loaded game config name (usually `Cstrike`) |
| `.fgd` not found | pass an absolute path; TrenchBroom resolves it against the game/mod dirs |
