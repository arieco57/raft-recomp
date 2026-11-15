#!/bin/bash
# Raft Recomp installer: Python 3.14 for the phone, so the converter (tools/*.py + UnityPy) runs on-device.
#   android/py/lib/   libpython3.14.so + libpyexec.so (tiny launcher: Android only lets an app exec files
#                     from its native library dir, so it ships in the APK's lib/ like a library)
#   android/py/root.zip  PYTHONHOME: stdlib + lib-dynload + site-packages (UnityPy with UnityPyBoost, lz4,
#                     texture2ddecoder, astc_encoder (NEON), pure deps) + the converter scripts.
#                     The installer unpacks it into the app's files dir.
# Needs: thirdparty/python-android/3.14.7 (python.org android build), thirdparty/py-src (sdists), NDK 29.
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PYA="$HERE/thirdparty/python-android/3.14.7/prefix"
SRC="$HERE/thirdparty/py-src"
NDK="${ANDROID_NDK:-$HOME/Android/Sdk/ndk/29.0.14206865}"
TC="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
CC="$TC/aarch64-linux-android24-clang"; CXX="$TC/aarch64-linux-android24-clang++"
SITE="$HERE/tools/venv/lib/python3.14/site-packages"
OUT="$HERE/android/py"; B="$OUT/build"; ROOT="$B/root"; LP="$ROOT/lib/python3.14"; SP="$LP/site-packages"
SUF=".cpython-314-aarch64-linux-android.so"
PYINC=(-I"$PYA/include/python3.14")
EXT_LD=(-shared -static-libstdc++ -L"$PYA/lib" -lpython3.14 -Wl,-z,max-page-size=16384)

rm -rf "$OUT"; mkdir -p "$OUT/lib" "$B" "$SP"

# launcher
cat > "$B/pyexec.c" <<'C'
#include <Python.h>
int main(int argc, char **argv) { return Py_BytesMain(argc, argv); }
C
"$CC" -O2 "${PYINC[@]}" "$B/pyexec.c" -L"$PYA/lib" -lpython3.14 -o "$OUT/lib/libpyexec.so"
cp "$PYA/lib/libpython3.14.so" "$OUT/lib/"

# stdlib (without what a converter never needs)
cp -r "$PYA/lib/python3.14/." "$LP/"
rm -rf "$LP"/{test,idlelib,tkinter,turtledemo,ensurepip,pydoc_data,lib2to3,venv,__phello__,config-3.14*} "$LP/site-packages/"*
find "$LP" -name "__pycache__" -prune -exec rm -rf {} +
rm -f "$LP/lib-dynload/"{_ctypes_test,_testcapi,_testinternalcapi,_testbuffer,_testmultiphase,_testsinglephase,_testimportmultiple,_testclinic,_testexternalinspection,xxlimited,xxlimited_35,xxsubtype,_xxtestfuzz,_sqlite3,_ssl,_hashlib,_tkinter}*.so 2>/dev/null || true

# pure-Python packages from the PC venv
for p in UnityPy attrs attr fsspec archspec tpk_ar; do cp -r "$SITE/$p" "$SP/"; done
find "$SP" -name "*.so" -delete; find "$SP" -name "__pycache__" -prune -exec rm -rf {} +
mkdir -p "$SP/lz4" "$SP/texture2ddecoder" "$SP/astc_encoder"
cp -r "$SRC/lz4-4.4.5/lz4/"{__init__.py,version.py,block,frame} "$SP/lz4/"; find "$SP/lz4" -name "*.c" -delete
cp "$SRC/texture2ddecoder-1.0.6/texture2ddecoder/"*.py* "$SP/texture2ddecoder/" 2>/dev/null || true
cp "$SRC/astc_encoder_py-0.1.12/astc_encoder/"*.py* "$SP/astc_encoder/"
# archspec can't tell NEON on Android: always the NEON build
sed -i 's/^from archspec.cpu import host$/class _H:\n    class family: name = "aarch64"\n    features = ()\nhost = lambda: _H/' "$SP/astc_encoder/encoder.py"
grep -q 'name = "aarch64"' "$SP/astc_encoder/encoder.py" || { echo "astc_encoder loader patch failed"; exit 1; }
# UnityPy imports these at load time; the converter never uses them
echo 'def decompress(*a, **k): raise RuntimeError("brotli not available")' > "$SP/brotli.py"
mkdir -p "$SP/PIL"; : > "$SP/PIL/__init__.py"
printf 'class Image: pass\ndef open(*a, **k): raise RuntimeError("PIL not available")\n' > "$SP/PIL/Image.py"

# native extensions
J=$(nproc)
build_ext() {  # out-name lang flags... -- sources...
    local out="$1"; shift; local cc="$1"; shift; local flags=(); while [ "$1" != "--" ]; do flags+=("$1"); shift; done; shift
    "$cc" -O2 -fPIC "${PYINC[@]}" "${flags[@]}" "$@" "${EXT_LD[@]}" -o "$out"
    echo "built $(basename "$out")"
}
( cd "$SRC/unitypy-1.25.4" && build_ext "$SP/UnityPy/UnityPyBoost$SUF" "$CXX" -std=c++17 -IUnityPyBoost -- UnityPyBoost/*.cpp ) &
( cd "$SRC/lz4-4.4.5" && build_ext "$SP/lz4/_version$SUF" "$CC" -Ilz4libs -- lz4/_version.c lz4libs/lz4.c &&
  build_ext "$SP/lz4/block/_block$SUF" "$CC" -Ilz4libs -- lz4/block/_block.c lz4libs/lz4.c lz4libs/lz4hc.c &&
  build_ext "$SP/lz4/frame/_frame$SUF" "$CC" -Ilz4libs -- lz4/frame/_frame.c lz4libs/lz4.c lz4libs/lz4hc.c lz4libs/lz4frame.c lz4libs/xxhash.c ) &
( cd "$SRC/texture2ddecoder-1.0.6" && build_ext "$SP/texture2ddecoder/_texture2ddecoder$SUF" "$CXX" -std=c++17 -fms-extensions -Isrc/Texture2DDecoder -- \
    $(find src -name "*.cpp" ! -name Texture2DDecoder.cpp ! -name AssemblyInfo.cpp) ) &
( cd "$SRC/astc_encoder_py-0.1.12" && A=src/astc-encoder/Source &&
  build_ext "$SP/astc_encoder/_encoder_neon$SUF" "$CXX" -std=c++14 -ffp-contract=fast -pthread -I$A \
    -DPy_LIMITED_API=0x03070000 '-DMODULE_NAME="module_name"' -DINIT_FUNC_NAME=PyInit__encoder_neon \
    -DASTCENC_NEON=1 -DASTCENC_SVE=0 -DASTCENC_SSE=0 -DASTCENC_AVX=0 -DASTCENC_POPCNT=0 -DASTCENC_F16C=0 -- \
    src/pybind.cpp src/astcenc_error_metrics.cpp $(ls $A/astcenc_*.cpp | grep -v toplevel) ) &
wait
for m in "$SP/UnityPy/UnityPyBoost$SUF" "$SP/lz4/block/_block$SUF" "$SP/texture2ddecoder/_texture2ddecoder$SUF" "$SP/astc_encoder/_encoder_neon$SUF"; do
    [ -f "$m" ] || { echo "missing $m"; exit 1; }; done

# shader translator (HLSLcc, static) - an executable, so it ships in the APK's lib/ too
H="$HERE/thirdparty/HLSLcc"
if [ ! -f "$H/build-android/libhlslcc.a" ]; then
  cmake -S "$H" -B "$H/build-android" -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI=arm64-v8a \
      -DANDROID_PLATFORM=24 -DHLSLCC_LIBRARY_SHARED=OFF -DCMAKE_BUILD_TYPE=Release > /dev/null
  cmake --build "$H/build-android" -j"$J" > /dev/null
fi
"$CXX" -O2 -std=c++14 -I"$H/include" -I"$H/src" -I"$H/src/internal_includes" -I"$H/src/cbstring" "$HERE/tools/dxbc2glsl.cpp" \
    "$H/build-android/libhlslcc.a" -static-libstdc++ -Wl,-z,max-page-size=16384 -o "$OUT/lib/libdxbc2glsl.so"

# the converter
mkdir -p "$ROOT/raft"
# the APK's IL2CPP code was compiled from this Assembly-CSharp: the installer refuses other game versions
STEAM_DATA="${RAFT_STEAM:-$HOME/.local/share/Steam/steamapps/common/Raft}/Raft_Data"
sha256sum "$STEAM_DATA/Managed/Assembly-CSharp.dll" | cut -d' ' -f1 > "$ROOT/raft/expected_assembly.sha256"
cp "$HERE/tools/"{convert_shaders,convert_compute,shrink_textures,compact_ress,patch_buildsettings,set_platform,ushader,uparams,dxbc,glsl_patches,android_convert}.py "$ROOT/raft/"
(cd "$ROOT" && rm -f "$OUT/root.zip" && zip -q -r -9 "$OUT/root.zip" .)
ls -la "$OUT/lib" "$OUT/root.zip"
