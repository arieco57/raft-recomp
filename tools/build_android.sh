#!/bin/bash
# Raft Recomp (Android, armeabi-v7a, Mono, OpenGL ES 3): APK from the Steam data.
#   android/data/  textures re-encoded (shrink_textures.py) + compacted .resS (compact_ress.py)
#   this script: GLES shaders/compute, BuildSettings -> GLES3, managed assemblies, APK.
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
STEAM="${RAFT_STEAM:-$HOME/.local/share/Steam/steamapps/common/Raft}/Raft_Data"
DATA="$HERE/android/${RAFT_DATA:-data2}"
OUT="$HERE/android/apk"
U="$HERE/unity/apkg/payload"
FMODJAR="$HERE/thirdparty/fmod-android/fmodstudioapi20215android/api/core/lib/fmod.jar"
SDK="$HOME/Android/Sdk"; BT="$SDK/build-tools/36.1.0"; JAR="$SDK/platforms/android-36/android.jar"
py() { "$HERE/tools/venv/bin/python" -I "$@"; }
export RAFT_WORK="${RAFT_WORK:-/tmp}" RAFT_GLES=1
# RAFT_INSTALLER=1: APK with no game data at all (64-bit IL2CPP code + Python converter); the phone converts
# the user's own Raft copy (InstallerActivity / ConvertService / tools/android_convert.py)
INST="${RAFT_INSTALLER:-0}"
if [ "$INST" = 1 ]; then SKIP_SHADERS=1; RAFT_IL2CPP=1; RAFT_OBB=0; fi

if [ "${SKIP_SHADERS:-0}" != 1 ]; then
  for f in Resources/unity_builtin_extra globalgamemanagers.assets resources.assets sharedassets0.assets sharedassets1.assets \
           sharedassets6.assets sharedassets8.assets sharedassets9.assets sharedassets24.assets sharedassets25.assets \
           sharedassets29.assets sharedassets30.assets sharedassets34.assets sharedassets35.assets sharedassets36.assets; do
      in="$DATA/$f"; [ -f "$in" ] || in="$STEAM/$f"
      mkdir -p "$(dirname "$DATA/$f")"
      echo "##### $f"
      # shaders always from the Steam original (the copy in $DATA may already hold converted ones)
      py "$HERE/tools/convert_shaders.py" "$in" "$DATA/$f" --shader-src "$STEAM/$f" | grep -E "^(hlslcc|wrote)" || true
  done
  for f in resources.assets sharedassets0.assets sharedassets29.assets; do
      py "$HERE/tools/convert_compute.py" "$DATA/$f" "$DATA/$f" | grep -E "^(wrote)|not found" || true
  done
  py "$HERE/tools/patch_buildsettings.py" "$STEAM/globalgamemanagers" "$DATA/globalgamemanagers" 11
  # Android refuses data built for another target (Raft ships StandaloneWindows64 = 19)
  find "$DATA" -type f ! -name "*.resS" -print0 | xargs -0 python3 -I "$HERE/tools/set_platform.py" 13 | tail -2
fi

rm -rf "$OUT"; mkdir -p "$OUT/stage/assets/bin/Data/Managed" "$OUT/stage/lib/armeabi-v7a"
D="$OUT/stage/assets/bin/Data"
cp "$U/Data/unity default resources" "$D/unity default resources"
if [ "$INST" != 1 ]; then
# data
cp -a "$DATA/"* "$D/"
cp "$STEAM/boot.config" "$D/"
# 32-bit address space: smaller allocator blocks (Unity's defaults reserve 16 MB+ chunks, which stop fitting
# once a world's islands load - DynamicHeapAllocator::CreateTLSFBlock fails)
cat >> "$D/boot.config" <<'CFG'
memorysetup-main-allocator-block-size=4194304
memorysetup-thread-allocator-block-size=2097152
memorysetup-gfx-main-allocator-block-size=4194304
memorysetup-gfx-thread-allocator-block-size=2097152
memorysetup-cache-allocator-block-size=1048576
memorysetup-typetree-allocator-block-size=1048576
memorysetup-profiler-allocator-block-size=1048576
memorysetup-profiler-editor-allocator-block-size=1048576
memorysetup-bucket-allocator-block-count=1
memorysetup-temp-allocator-size-loading-worker=262144
memorysetup-temp-allocator-size-background-worker=32768
memorysetup-temp-allocator-size-job-worker=262144
memorysetup-temp-allocator-size-preload-manager=262144
CFG
cp "$HERE/game/Raft_Data/"{RuntimeInitializeOnLoads.json,ScriptingAssemblies.json} "$D/"
cp -r "$STEAM/StreamingAssets/." "$OUT/stage/assets/"
fi
if [ "${RAFT_IL2CPP:-0}" = 1 ]; then
  # 64-bit (arm64-v8a) IL2CPP build: tools/build_il2cpp.sh made libil2cpp.so + metadata from the same assemblies
  IL="$HERE/android/il2cpp"
  ABI=arm64-v8a; VAR="$U/Variations/il2cpp/${UVAR:-Release}"
  mkdir -p "$OUT/stage/lib/$ABI" "$D/Managed"
  cp -r "$IL/data/Metadata" "$IL/data/Resources" "$D/Managed/"
  cp "$VAR/Libs/$ABI/"*.so "$IL/out/libil2cpp.so" "$OUT/stage/lib/$ABI/"
  cp "$HERE/android/plugins64/"*.so "$OUT/stage/lib/$ABI/"
  rm -rf "$OUT/stage/lib/armeabi-v7a"
  CLASSES="$VAR/Classes/classes.jar"
  if [ "$INST" = 1 ]; then   # Python + shader translator (tools/build_pyandroid.sh)
    cp "$HERE/android/py/lib/"*.so "$OUT/stage/lib/$ABI/"
    cp "$HERE/android/py/root.zip" "$OUT/stage/assets/python.zip"
  fi
else
  ABI=armeabi-v7a; CLASSES="$U/Variations/mono/${UVAR:-Development}/Classes/classes.jar"
  # managed: game assemblies (Linux BCL + patched Assembly-CSharp + overlay) with Unity's Android engine modules
  cp "$HERE/game/Raft_Data/Managed/"*.dll "$D/Managed/"
  cp "$U/Variations/mono/Managed/"*.dll "$D/Managed/"
  # phone-only IL fixes (android/ilpatch/AndroidIlPatch.cs): water FFT without compute
  MB="$HERE/unity/sdk/Editor/Data/MonoBleedingEdge"
  MONO_PATH="$MB/lib/mono/net_4_x-linux" "$MB/bin/mono" "$HERE/android/ilpatch/AndroidIlPatch.exe" \
      "$HERE/game/Raft_Data/Managed/Assembly-CSharp.dll" "$D/Managed/Assembly-CSharp.dll" "$HERE/game/Raft_Data/Managed"
  mkdir -p "$D/Managed/etc"; cp -r "$HERE/game/Raft_Data/MonoBleedingEdge/etc/." "$D/Managed/etc/"
  # the Android Mono runtime takes the class libraries from Managed/mono/4.5
  BCL="$HERE/unity/editor-bcl/Editor/Data/MonoBleedingEdge/lib/mono/unityjit-linux"
  mkdir -p "$D/Managed/mono/4.5"; cp "$BCL/"*.dll "$D/Managed/mono/4.5/"; cp -r "$BCL/Facades" "$D/Managed/mono/4.5/"
  # native
  cp "$U/Variations/mono/${UVAR:-Development}/Libs/armeabi-v7a/"*.so "$OUT/stage/lib/armeabi-v7a/"
  cp "$U/Variations/mono/${UVAR:-Development}/MonoLibs/armeabi-v7a/"*.so "$OUT/stage/lib/armeabi-v7a/"
  [ -d "$HERE/android/plugins" ] && cp "$HERE/android/plugins/"*.so "$OUT/stage/lib/armeabi-v7a/" 2>/dev/null || true
fi

# manifest + resources
cat > "$OUT/AndroidManifest.xml" <<'EOF'
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.raftrecomp.raft"
          android:versionCode="1" android:versionName="0.1" android:installLocation="preferExternal">
    <uses-sdk android:minSdkVersion="22" android:targetSdkVersion="32" />
    <uses-feature android:glEsVersion="0x00030000" />
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" android:maxSdkVersion="32" />
    <uses-permission android:name="android.permission.MANAGE_EXTERNAL_STORAGE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.WAKE_LOCK" />
    <application android:label="Raft Recomp" android:debuggable="true" android:vmSafeMode="true" android:icon="@mipmap/app_icon" android:extractNativeLibs="true"
                 android:hardwareAccelerated="false" android:isGame="true">
        <activity android:name="com.raftrecomp.RaftActivity" android:exported="true"
                  android:theme="@style/UnityThemeSelector" android:screenOrientation="sensorLandscape"
                  android:launchMode="singleTask" android:hardwareAccelerated="false" android:resizeableActivity="false"
                  android:configChanges="mcc|mnc|locale|touchscreen|keyboard|keyboardHidden|navigation|orientation|screenLayout|uiMode|screenSize|smallestScreenSize|fontScale|layoutDirection|density">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
            <meta-data android:name="unityplayer.UnityActivity" android:value="true" />
            <meta-data android:name="android.notch_support" android:value="true" />
        </activity>
    </application>
</manifest>
EOF
if [ "$INST" = 1 ]; then   # the installer screen is the launcher; Unity's activity starts from it
  python3 - "$OUT/AndroidManifest.xml" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
f = s.index('            <intent-filter>'); e = s.index('</intent-filter>', f) + len('</intent-filter>\n')
s = s[:f] + s[e:]
s = s.replace('android:name="com.raftrecomp.RaftActivity" android:exported="true"', 'android:name="com.raftrecomp.RaftActivity" android:exported="false"')
s = s.replace('    </application>', """        <activity android:name="com.raftrecomp.InstallerActivity" android:exported="true"
                  android:theme="@android:style/Theme.Material.NoActionBar" android:screenOrientation="sensorLandscape"
                  android:configChanges="orientation|screenSize|keyboardHidden">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        <service android:name="com.raftrecomp.ConvertService" android:exported="false" />
    </application>""")
open(p, 'w').write(s)
PY
fi
"$BT/aapt2" compile --dir "$U/Apk/res" -o "$OUT/res.zip"
"$BT/aapt2" link -o "$OUT/base.apk" -I "$JAR" --manifest "$OUT/AndroidManifest.xml" "$OUT/res.zip" --auto-add-overlay
# dex
mkdir -p "$OUT/javac"
mapfile -t JAVA_SRC < <(find "$U/Source" "$HERE/android/java" -name "*.java")   # Unity's activity + ours (virtual pad, gamepad)
javac -nowarn -source 8 -target 8 -cp "$CLASSES:$FMODJAR" -bootclasspath "$JAR" -d "$OUT/javac" \
    "${JAVA_SRC[@]}" 2>&1 | grep -v -E "^warning|^Note|bootstrap|obsolete|^[0-9]+ warning" || true
(cd "$OUT/javac" && jar cf "$OUT/activity.jar" .)
"$BT/d8" --min-api 22 --output "$OUT" "$CLASSES" "$OUT/activity.jar" "$FMODJAR" --lib "$JAR" 2>&1 | grep -v -E "^Warning|was not found|^WARNING" || true
cp "$OUT/classes.dex" "$OUT/stage/"
# pack: data stored uncompressed (Unity reads straight from the APK)
cp "$OUT/base.apk" "$OUT/unsigned.apk"
if [ "${RAFT_OBB:-0}" = 1 ]; then
  # split build: the APK holds code only, the game data goes to Android/obb/<pkg>/main.1.<pkg>.obb (a stored
  # zip with the same assets/ layout; Unity checks its unity_obb_guid against the APK's unity_app_guid)
  GUID=$(cat /proc/sys/kernel/random/uuid | tr -d -); printf %s "$GUID" > "$D/unity_app_guid"
  OBBS="$OUT/obbstage"; rm -rf "$OBBS"; mkdir -p "$OBBS/assets/bin/Data"; printf %s "$GUID" > "$OBBS/unity_obb_guid"
  (cd "$OUT/stage/assets" && for f in *; do [ "$f" = bin ] || mv "$f" "$OBBS/assets/"; done)
  (cd "$D" && for f in *; do case "$f" in Managed|boot.config|unity_app_guid) ;; *) mv "$f" "$OBBS/assets/bin/Data/";; esac; done)
  [ "${RAFT_OBB_ZIP:-1}" = 1 ] && rm -f "$HERE/android/main.1.com.raftrecomp.raft.obb" && (cd "$OBBS" && zip -q -0 -r "$HERE/android/main.1.com.raftrecomp.raft.obb" unity_obb_guid assets)
fi
# order matters: some readers use 32-bit signed offsets, so the small, essential entries go first
if [ "${RAFT_OBB:-0}" = 1 ] || [ "$INST" = 1 ]; then
  (cd "$OUT/stage" && zip -q -0 "$OUT/unsigned.apk" classes.dex && zip -q -9 -r "$OUT/unsigned.apk" lib && zip -q -0 -r "$OUT/unsigned.apk" assets)
else
(cd "$OUT/stage" && zip -q -0 "$OUT/unsigned.apk" classes.dex && zip -q -9 -r "$OUT/unsigned.apk" lib && zip -q -0 -r "$OUT/unsigned.apk" assets/bin/Data/Managed \
    assets/bin/Data/globalgamemanagers assets/bin/Data/globalgamemanagers.assets assets/bin/Data/boot.config \
    assets/bin/Data/*.json "assets/bin/Data/unity default resources" assets/bin/Data/Resources && \
 zip -q -0 -r "$OUT/unsigned.apk" assets)
fi
"$BT/zipalign" -f -p 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"
"$BT/apksigner" sign --ks "$HOME/.android/debug.keystore" --ks-pass pass:android --key-pass pass:android \
    --out "$HERE/android/RaftRecomp.apk" "$OUT/aligned.apk"
ls -la "$HERE/android/RaftRecomp.apk"
SZ=$(stat -c %s "$HERE/android/RaftRecomp.apk")
[ "$SZ" -lt 2147483648 ] || echo "WARNING: APK over 2 GiB - Unity's APK reader cannot mount it"
