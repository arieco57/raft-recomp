#!/bin/bash
# Raft Recomp (Android): textures (ASTC, smaller) + compacted .resS into android/data2, from the Steam data
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
O="${RAFT_STEAM:-$HOME/.local/share/Steam/steamapps/common/Raft}/Raft_Data"
D="$HERE/android/data2"
rm -rf "$D"; mkdir -p "$D"
ls "$O" | grep -v -E "\.resS$|\.resource$" | grep -E "\.assets$|^level[0-9]+$" > "$HERE/android/files.txt"
xargs -P 3 -I{} sh -c '"$0/tools/venv/bin/python" -I "$0/tools/shrink_textures.py" "$1/{}" "$2/{}" || echo "FAILED {}"' "$HERE" "$O" "$D" < "$HERE/android/files.txt" > "$HERE/android/shrink2.log" 2>&1
xargs -P 3 -I{} sh -c '"$0/tools/venv/bin/python" -I "$0/tools/compact_ress.py" "$1" "$2" "{}" || echo "FAILED {}"' "$HERE" "$O" "$D" < "$HERE/android/files.txt" > "$HERE/android/compact2.log" 2>&1
grep FAILED "$HERE/android/"{shrink2,compact2}.log || true
du -sh "$D"
