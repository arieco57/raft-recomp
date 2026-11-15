# Raft Recomp

Raft (la versión de PC de Steam, Unity 2021.3) corriendo nativo en **Linux** y en **Android (arm64)**.
Proyecto personal, en beta.

**Este repo no trae nada del juego.** Necesitás tu propia copia de Raft comprada en Steam: todo se arma a
partir de tus archivos.

## Android

1. Bajá `RaftRecomp-Installer.apk` de [Releases](../../releases) e instalalo.
2. Copiá la carpeta `Raft` del juego al teléfono, por ejemplo a Descargas. La encontrás en Steam: Raft >
   Administrar > Ver archivos locales. Pesa unos 7,3 GB.
3. Abrí Raft Recomp, tocá **Elegir carpeta de Raft**, seleccioná esa carpeta y dale el permiso de acceso a
   archivos.
4. El teléfono convierte los datos: unos 20 minutos en un Snapdragon 732G, y necesita unos 4 GB libres. Se
   puede apagar la pantalla. Al terminar entra al juego. Después podés borrar la copia de la carpeta `Raft`.

Detalles:

- Hay un mando en pantalla, y también funcionan los mandos físicos.
- El botón **Opciones**, arriba a la derecha, tiene el contador de FPS, la resolución y el mando en pantalla.
- Para crear un mundo, elegí "Nadie puede unirse": no hay online.
- Conviene poner los gráficos del juego en bajo. Probado en un Redmi Note 10 Pro (Adreno 618, 6 GB): unos 40
  FPS a 540p.
- Si algo falla, mandá estos archivos de `Android/data/com.raftrecomp.raft/files/`:
  - `raft.log`, que es el log del juego;
  - `conversion.log`, si falló la instalación.

El APK solo sirve para la versión de Raft con la que fue compilado. Si tu copia es de otra versión, el
instalador te avisa.

## Cómo funciona

- **Gráficos:** Raft solo trae shaders de Direct3D 11. `tools/convert_shaders.py` extrae el bytecode DXBC, le
  agrega la información de reflexión que Unity no guarda, lo traduce con
  [HLSLcc](https://github.com/Unity-Technologies/HLSLcc) (`tools/dxbc2glsl.cpp`) a GLSL (OpenGL 4.x / ES 3)
  y lo vuelve a guardar en los archivos del juego. `tools/glsl_patches.py` corrige los shaders que suponen
  convenciones de D3D, como el Z invertido o el clip space dado vuelta.
- **Linux:** se usa el player de Linux de Unity 2021.3.45f2 con los datos del juego (`tools/build_data.sh`).
- **Android:**
  - El código C# del juego se compila a arm64 con IL2CPP (`tools/build_il2cpp.sh`). Mono en Android es solo
    de 32 bits, y un mundo de Raft no entra en 4 GB de memoria.
  - El APK no tiene datos. `InstallerActivity` y `ConvertService` corren en el teléfono
    `tools/android_convert.py`, sobre Python 3.14 para Android con UnityPy y astc-encoder
    (`tools/build_pyandroid.sh`). El convertidor:
    - traduce los shaders;
    - recomprime las texturas a ASTC;
    - arma un zip con la misma estructura que un APK.
  - `RaftActivity` le indica a Unity que lea los datos de ese zip.
- **Overlay:** `overlay/RecompOverlay.cs` agrega el menú de opciones, el mando virtual (Input System) y el log.

## Compilar

Hace falta:

- el editor de Unity 2021.3.45f2 para Linux (player, il2cpp y módulo de Android);
- Android SDK y los NDK r21d y r29;
- FMOD Studio API 2.02.15 para Linux y Android;
- Python 3.14 con UnityPy;
- tu copia de Raft de Steam.

Los scripts nunca escriben en la carpeta de Steam.

- **Linux:** `tools/build_data.sh` y después `jugar.sh`.
- **APK instalador:**

  ```
  tools/build_il2cpp.sh
  tools/build_pyandroid.sh
  RAFT_INSTALLER=1 UVAR=Release tools/build_android.sh
  ```

Raft es de Redbeet Interactive. Este proyecto no está afiliado con ellos.
