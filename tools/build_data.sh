#!/bin/bash
# Raft Recomp: rebuild the converted data files in game/Raft_Data from the (read-only) Steam install.
#   shaders -> GLCore (convert_shaders.py), compute shaders -> GLCore variant (convert_compute.py),
#   BuildSettings graphics APIs -> OpenGLCore + Vulkan (patch_buildsettings.py)
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
STEAM="${RAFT_STEAM:-$HOME/.local/share/Steam/steamapps/common/Raft}/Raft_Data"
GAME="$HERE/game/Raft_Data"
py() { "$HERE/tools/venv/bin/python" -I "$@"; }
export RAFT_WORK="${RAFT_WORK:-/tmp}"

reset_link() {   # put the original back (as a symlink) before converting
    rm -f "$GAME/$1"
    ln -s "$STEAM/$1" "$GAME/$1"
}

for f in Resources/unity_builtin_extra globalgamemanagers.assets resources.assets sharedassets0.assets sharedassets1.assets \
         sharedassets6.assets sharedassets8.assets sharedassets9.assets sharedassets24.assets sharedassets25.assets \
         sharedassets29.assets sharedassets30.assets sharedassets34.assets sharedassets35.assets sharedassets36.assets; do
    echo "##### $f"
    reset_link "$f"
    py "$HERE/tools/convert_shaders.py" "$STEAM/$f" "$GAME/$f" | grep -E "^(hlslcc|wrote|FAIL)" || true
done
for f in resources.assets sharedassets0.assets sharedassets29.assets; do
    echo "##### compute $f"
    py "$HERE/tools/convert_compute.py" "$GAME/$f" "$GAME/$f" | grep -E "^(hlslcc|wrote|FAIL)" || true
done
reset_link globalgamemanagers
py "$HERE/tools/patch_buildsettings.py" "$STEAM/globalgamemanagers" "$GAME/globalgamemanagers" 17,21
python3 -I "$HERE/tools/patch_paths.py" "$STEAM/Managed/Assembly-CSharp.dll" "$GAME/Managed/Assembly-CSharp.dll"
python3 -I "$HERE/tools/install_overlay.py"
