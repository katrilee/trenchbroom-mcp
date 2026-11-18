#!/usr/bin/env bash
# Applies the TrenchBroom MCP bridge to a TrenchBroom v2026.2 source tree.
#
#   ./apply.sh /path/to/TrenchBroom        # must be checked out at v2026.2
#
# Requirements for a full build:
#   * Qt >= 6.10      (apt on Ubuntu 22.04 only has 6.2 — use aqtinstall:
#                      pip install aqtinstall
#                      python3 -m aqt install-qt linux desktop 6.10.0 linux_gcc_64 -O /opt/Qt)
#   * CMake >= 3.25
#   * vcpkg submodule (git submodule update --init --recursive) — the build
#     fetches assimp/freeimage/openexr and that step is long.
set -euo pipefail

TB_ROOT="${1:-}"
if [[ -z "$TB_ROOT" || ! -f "$TB_ROOT/CMakeLists.txt" ]]; then
  echo "usage: $0 /path/to/TrenchBroom" >&2
  exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! grep -q "McpServerBridge.cpp" "$TB_ROOT/lib/TbUiLib/CMakeLists.txt"; then
  (cd "$TB_ROOT" && patch -p1 --forward < "$HERE/patches/0001-build-and-mapwindow-integration.patch")
else
  echo "build/MapWindow patch already applied — skipping"
fi

mkdir -p "$TB_ROOT/lib/TbUiLib/include/ui" "$TB_ROOT/lib/TbUiLib/src"
cp "$HERE"/lib/TbUiLib/include/ui/Mcp*.h "$TB_ROOT/lib/TbUiLib/include/ui/"
cp "$HERE"/lib/TbUiLib/src/Mcp*.cpp "$TB_ROOT/lib/TbUiLib/src/"

echo "Bridge sources installed into $TB_ROOT/lib/TbUiLib"
echo
echo "Build:"
echo "  cmake -S \"$TB_ROOT\" -B \"$TB_ROOT/build\" -DCMAKE_BUILD_TYPE=Release \\"
echo "        -DQt6_DIR=/opt/Qt/6.10.0/gcc_64/lib/cmake/Qt6"
echo "  cmake --build \"$TB_ROOT/build\" -j\"\$(nproc)\""
echo
echo "Runtime env vars: TB_MCP_PORT (default 6161), TB_MCP_ENABLE=0, TB_MCP_TOKEN,"
echo "                  TB_MCP_SNAPSHOT_DIR, TB_MCP_CAPTURE_DIR"
