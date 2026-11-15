#!/bin/bash
# Raft Recomp: build game-vk/, the Vulkan flavour. Shares everything with game/ (OpenGL) except the
# converted data files: shaders -> Vulkan, BuildSettings graphics API -> Vulkan.
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
STEAM="${RAFT_STEAM:-$HOME/.local/share/Steam/steamapps/common/Raft}/Raft_Data"
GL="$HERE/game"
VK="$HERE/game-vk"
py() { "$HERE/tools/venv/bin/python" -I "$@"; }
export RAFT_WORK="${RAFT_WORK:-/tmp}"

mkdir -p "$VK/Raft_Data/Resources"
cp -f "$GL/Raft.x86_64" "$GL/UnityPlayer.so" "$GL/steam_appid.txt" "$VK/"
# everything in Raft_Data: the originals (symlinks into Steam) as they are, our own files through game/
for e in "$GL/Raft_Data/"*; do
    n="$(basename "$e")"
    [ "$n" = Resources ] && continue
    rm -rf "$VK/Raft_Data/$n"
    if [ -L "$e" ]; then cp -P "$e" "$VK/Raft_Data/$n"; else ln -s "$e" "$VK/Raft_Data/$n"; fi
done
ln -sf "$GL/Raft_Data/Resources/unity default resources" "$VK/Raft_Data/Resources/unity default resources"

for f in Resources/unity_builtin_extra globalgamemanagers.assets resources.assets sharedassets0.assets sharedassets1.assets \
         sharedassets6.assets sharedassets8.assets sharedassets9.assets sharedassets24.assets sharedassets25.assets \
         sharedassets29.assets sharedassets30.assets sharedassets34.assets sharedassets35.assets sharedassets36.assets; do
    echo "##### $f"
    rm -f "$VK/Raft_Data/$f"; ln -s "$STEAM/$f" "$VK/Raft_Data/$f"
    py "$HERE/tools/convert_shaders_vk.py" "$STEAM/$f" "$VK/Raft_Data/$f" | grep -E "^(hlslcc|wrote|FAIL)" || true
done
rm -f "$VK/Raft_Data/globalgamemanagers"
py "$HERE/tools/patch_buildsettings.py" "$STEAM/globalgamemanagers" "$VK/Raft_Data/globalgamemanagers" 21,17
for f in resources.assets sharedassets0.assets sharedassets29.assets; do
    echo "##### compute $f"
    py "$HERE/tools/convert_compute.py" "$VK/Raft_Data/$f" "$VK/Raft_Data/$f" --vulkan | grep -E "^(hlslcc|wrote|FAIL)|not found" || true
done
