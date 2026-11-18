# TrenchBroom MCP

An autonomous GoldSource & **TrenchBroom v2026.2** level design system built on **FastMCP**, a native **C++/Qt TCP JSON-RPC bridge** inside TrenchBroom, and a pure-Python **GoldSource `.map` / VHLT** analysis & compilation engine.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ Tier 4  docs/MAPPING_DOCTRINE.md & docs/KNOWN_BSP_PITFALLS.md        │
└───────────────────────────────▲──────────────────────────────────────┘
┌───────────────────────────────┴──────────────────────────────────────┐
│ Tier 3  mcp_server/server.py (FastMCP) & mcp_server/tbcli.py         │
└───────▲───────────────────────────────────────────▲──────────────────┘
        │ NDJSON JSON-RPC (127.0.0.1:6161)          │ Python / subprocess
┌───────┴──────────────────────────┐   ┌────────────┴──────────────────┐
│ Tier 1  tb-bridge/               │   │ Tier 2  tools/                │
│         TrenchBroom C++ bridge   │   │         goldsource_engine/      │
│         (lib/TbUiLib)            │   │         wadgen.py             │
└──────────────────────────────────┘   └───────────────────────────────┘
```

- **Tier 1 (`tb-bridge/`)** — Native C++20/Qt6 TCP bridge (`127.0.0.1:6161`) patched into TrenchBroom `v2026.2`. Exposes 44+ RPC methods for document management, layers, CSG, vertex/edge/face sculpting, UV/texture alignment, entity creation, viewport camera control, and live OpenGL screenshot capture—all wrapped in `mdl::Transaction` for full undo/redo support.
- **Tier 2 (`tools/goldsource_engine/`, `tools/wadgen.py`)** — Pure-Python Valve 220 `.map` parser/validator, FGD parser, hull/step/clearance physics validator, A\* navigation & sightline analyzer, SVG/PNG blueprint renderer, VHLT (`hlcsg` → `hlbsp` → `hlvis` → `hlrad`) pipeline driver with leak/budget parsing, and procedural WAD2 texture generator.
- **Tier 3 (`mcp_server/`)** — FastMCP server (`server.py`) and standalone CLI (`tbcli.py`) exposing high-level level-design tools and raw RPC passthrough to any MCP-compatible LLM client.
- **Tier 4 (`docs/`)** — Mapping doctrine, BSP pitfall reference, architecture notes, and build guides.

---

## Repository Layout

```
├── .github/workflows/build-ubuntu22.yml   # CI: builds TrenchBroom v2026.2 + MCP bridge
├── docs/
│   ├── ARCHITECTURE.md                    # 4-tier design & TrenchBroom 2026.2 API notes
│   ├── KNOWN_BSP_PITFALLS.md              # GoldSource/VHLT limits & failure modes
│   ├── MAPPING_DOCTRINE.md                # Proportions, lighting, terrain & workflow rules
│   ├── PROBLEM-BRIEF.md                   # Original engineering brief
│   └── WINDOWS-BUILD.md                   # Windows build notes
├── mcp_server/
│   ├── server.py                          # FastMCP server
│   ├── tb_client.py                       # NDJSON TCP client for the C++ bridge
│   └── tbcli.py                           # Standalone CLI (`tbcli`)
├── tb-bridge/
│   ├── apply.sh                           # Applies patch + C++ sources to TrenchBroom v2026.2
│   ├── lib/TbUiLib/                       # McpServerBridge, McpNodeIndex, McpJson
│   └── patches/                           # CMake + MapWindow/AppController integration patch
├── tests/
│   ├── test_e2e_autonomous_map.py         # End-to-end map creation test
│   └── test_native_sculpting_dome.py      # Native sculpting & dome array test
├── tools/
│   ├── goldsource_engine/                 # Geometry, FGD, physics, nav, blueprint, VHLT
│   └── wadgen.py                          # Procedural GoldSource WAD2 generator
├── pyproject.toml
├── requirements.txt
└── LICENSE
```

---

## Quick Start

### 1. Build TrenchBroom with the MCP Bridge

You can either download the prebuilt artifact from the GitHub Actions workflow (`.github/workflows/build-ubuntu22.yml`) or build from source against **TrenchBroom `v2026.2`**:

```bash
git clone --branch v2026.2 --depth 1 https://github.com/TrenchBroom/TrenchBroom.git tb
cd tb && git submodule update --init --recursive && cd ..

./tb-bridge/apply.sh ./tb

cmake -S ./tb -B ./tb/build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="cmake/packages;/opt/Qt/6.10.0/gcc_64"
cmake --build ./tb/build --target TrenchBroom -j"$(nproc)"
```

Launch TrenchBroom with the bridge enabled (listens on `127.0.0.1:6161` by default):

```bash
TB_MCP_PORT=6161 ./TrenchBroom
```

### 2. Install the Python Package

Requires Python **3.10+**:

```bash
pip install -e .
# Optional: include Pillow for tools/wadgen.py
pip install -e ".[wad]"
```

Or with `requirements.txt`:

```bash
pip install -r requirements.txt
```

### 3. Configure Your MCP Client

Add `trenchbroom-mcp` to your MCP client configuration (Claude Desktop, Cursor, OpenCode, Antigravity, etc.):

```json
{
  "mcpServers": {
    "trenchbroom": {
      "command": "trenchbroom-mcp",
      "env": {
        "TB_MCP_HOST": "127.0.0.1",
        "TB_MCP_PORT": "6161",
        "TB_MCP_WORKDIR": "/tmp/tb_mcp",
        "TB_FGD": "/path/to/halflife-cs.fgd"
      }
    }
  }
}
```

### 4. Use the CLI (`tbcli`)

```bash
tbcli ping
tbcli state
tbcli open --game Half-Life --fgd /path/to/halflife-cs.fgd --wad /path/to/cstrike.wad
tbcli room -256,-256,0 256,256,128 --thickness 16
tbcli cap --pos 200,-200,160 --look 0,0,48 --out /tmp/preview.png
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TB_MCP_HOST` | `127.0.0.1` | Bridge TCP host |
| `TB_MCP_PORT` | `6161` | Bridge TCP port |
| `TB_MCP_ENABLE` | `1` | Set `0` to disable the bridge inside TrenchBroom |
| `TB_MCP_TOKEN` | *(unset)* | Optional shared auth token for bridge RPCs |
| `TB_MCP_WORKDIR` | `/tmp/tb_mcp` | Working directory for saved `.map` files and screenshots |
| `TB_FGD` | *(unset)* | Path to your `.fgd` entity definition file |
| `TB_WADS` | *(unset)* | Colon-separated `.wad` paths used by tests |
| `CS16_DIR` | *(unset)* | Optional HLDS/ReHLDS root for `verify_bsp_in_rehlds` |

---

## License

[GPL-3.0-or-later](LICENSE) (consistent with upstream TrenchBroom).
