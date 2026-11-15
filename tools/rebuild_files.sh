#!/bin/bash
# reconvert only some data files (shaders, then compute if it has any): rebuild_files.sh <file>...
HERE="$(cd "$(dirname "$0")/.." && pwd)"
STEAM="${RAFT_STEAM:-$HOME/.local/share/Steam/steamapps/common/Raft}/Raft_Data"
GAME="$HERE/game/Raft_Data"
py() { "$HERE/tools/venv/bin/python" -I "$@"; }
for f in "$@"; do
    rm -f "$GAME/$f"; ln -s "$STEAM/$f" "$GAME/$f"
    py "$HERE/tools/convert_shaders.py" "$STEAM/$f" "$GAME/$f" | grep -E "^(hlslcc|wrote|SKIP)"
    py "$HERE/tools/convert_compute.py" "$GAME/$f" "$GAME/$f" | grep -E "^(wrote)"
done
