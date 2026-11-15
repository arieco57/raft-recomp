#!/bin/bash
# Raft Recomp (Android 64-bit): IL2CPP the game's assemblies into android/il2cpp/out/libil2cpp.so + data/.
# Mono on Android is 32-bit only, and a Raft world (all islands loaded at once) does not fit in 4 GB of
# address space; IL2CPP gives an arm64 build. Then: RAFT_IL2CPP=1 tools/build_android.sh
#   in/   game assemblies (Linux build: patched Assembly-CSharp, RaftRecomp overlay) with the unityaot BCL and
#         the il2cpp Android UnityEngine modules
#   cpp/  generated C++    data/  global-metadata.dat + resources    out/  libil2cpp.so
# Needs: unity/il2cpp-ed (Editor/Data/il2cpp + MonoBleedingEdge/lib/mono/unityaot-linux from the editor
# tarball), Android NDK r21d (sdkmanager "ndk;21.3.6528147"), OpenSSL 1.1 for il2cpp's .NET 5 (unity/ssl11).
set -e
REAL="$(cd "$(dirname "$0")/.." && pwd)"
# il2cpp breaks on spaces in paths
H="${RAFT_NOSPACE:-/tmp/raftrecomp-$USER}"; ln -sfn "$REAL" "$H"
I="$H/android/il2cpp"; D="$H/unity/il2cpp-ed/Editor/Data/il2cpp/build/deploy"
AOT="$H/unity/il2cpp-ed/Editor/Data/MonoBleedingEdge/lib/mono/unityaot-linux"
NDK="${ANDROID_NDK_R21:-$HOME/Android/Sdk/ndk/21.3.6528147}"
export LD_LIBRARY_PATH="$H/unity/ssl11${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

rm -rf "$I/in"; mkdir -p "$I/in" "$I/out" "$I/cache"
cp "$H/game/Raft_Data/Managed/"*.dll "$I/in/"
for f in "$I/in/"*.dll; do b=$(basename "$f")
    if [ -f "$AOT/$b" ]; then cp "$AOT/$b" "$f"; elif [ -f "$AOT/Facades/$b" ]; then cp "$AOT/Facades/$b" "$f"; fi
done
cp "$H/unity/apkg/payload/Variations/il2cpp/Managed/"*.dll "$I/in/"
MB="$H/unity/sdk/Editor/Data/MonoBleedingEdge"
MONO_PATH="$MB/lib/mono/net_4_x-linux" "$MB/bin/mono" "$H/android/ilpatch/AndroidIlPatch.exe" \
    "$H/game/Raft_Data/Managed/Assembly-CSharp.dll" "$I/in/Assembly-CSharp.dll" "$H/game/Raft_Data/Managed"
MONO_PATH="$MB/lib/mono/net_4_x-linux" "$MB/bin/mono" "$H/android/ilpatch/AndroidIlPatch.exe" \
    "$H/game/Raft_Data/Managed/FMODUnity.dll" "$I/in/FMODUnity.dll" "$H/game/Raft_Data/Managed"

# the editor's il2cpp has no Android baselib: it comes with Unity's Android module
mkdir -p "$D/../baselib/release_android_aarch64"
cp "$H/unity/apkg/payload/Variations/il2cpp/Release/StaticLibs/arm64-v8a/baselib.a" "$D/../baselib/release_android_aarch64/"
rm -rf "$I/cpp" "$I/data"
"$D/il2cpp" --convert-to-cpp --directory="$I/in" --generatedcppdir="$I/cpp" --data-folder="$I/data" \
    --symbols-folder="$I/sym" --dotnetprofile=unityaot-linux --emit-null-checks --enable-array-bounds-check \
    --jobs="$(nproc)" > "$I/convert.log" 2>&1 || { tail -20 "$I/convert.log"; exit 1; }
STAMP="$I/cache/.compile-start"; touch "$STAMP"
ANDROID_NDK_ROOT="$NDK" "$D/il2cpp" --compile-cpp --platform=Android --architecture=ARM64 --configuration=Release \
    --generatedcppdir="$I/cpp" --outputpath="$I/out/libil2cpp.so" --cachedirectory="$I/cache" \
    --tool-chain-path="$NDK" --data-folder="$I/data" > "$I/compile.log" 2>&1 &
PID=$!
# bee_backend often never exits after the build (or after a failed link): once the linked library exists and
# nothing is compiling for 2 minutes, stop it ourselves
idle=0
while kill -0 $PID 2>/dev/null; do
    sleep 20
    if [ -n "$(find "$I/cache/buildstate/artifacts" -path '*objcopy*' -name libil2cpp.so -newer "$STAMP" 2>/dev/null)" ] \
       && ! pgrep -x clang++ >/dev/null && ! pgrep -x clang >/dev/null && ! pgrep -x ld.lld >/dev/null; then
        idle=$((idle+1))
        if [ $idle -ge 6 ]; then echo "il2cpp: build done but bee_backend hung, stopping it"
            pkill -P $PID 2>/dev/null; kill $PID 2>/dev/null; pgrep -x bee_backend | xargs -r kill; break; fi
    else idle=0; fi
done
wait $PID 2>/dev/null || true
# il2cpp leaves the debug build in its cache; ship it stripped (990 MB -> ~220 MB)
SO=$(find "$I/cache/buildstate" -name libil2cpp.so -newer "$STAMP" -size +500M | head -1)
[ -n "$SO" ] || { tail -20 "$I/compile.log"; echo "il2cpp: no libil2cpp.so linked"; exit 1; }
"$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" --strip-debug -o "$I/out/libil2cpp.so" "$SO"
ls -la "$I/out/libil2cpp.so"
