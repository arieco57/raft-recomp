# Raft Recomp installer: converts the user's own Raft copy (Raft_Data from the PC game) into the data zip
# the phone build loads (RaftActivity points libunity at it). Runs on the phone (android/py) and on the PC.
#   python android_convert.py <Raft or Raft_Data dir> <out data zip> <banks dir> <apk> <work dir>
#   python android_convert.py --refresh <data zip> <apk>     (new APK build: only its code parts change)
# Per asset/level file: shrink_textures (ASTC, smaller) -> compact_ress -> GLES shaders / compute (from the
# untouched original) -> platform = Android -> into the zip, then the temp files go (little extra disk).
# The zip is stored and 4-byte aligned like an APK (Unity maps the files straight from it); from the APK
# itself it takes what is ours/Unity's: the IL2CPP metadata and Unity's Android "unity default resources".
# Progress on stdout: "PROGRESS <0-100> <text>"; errors: "ERROR <text>".
import os, sys, json, shutil, subprocess, zipfile, struct, time, hashlib, re
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.environ.get('RAFT_PY') or sys.executable
SHADER_FILES = ['Resources/unity_builtin_extra', 'globalgamemanagers.assets', 'resources.assets', 'sharedassets0.assets',
                'sharedassets1.assets', 'sharedassets6.assets', 'sharedassets8.assets', 'sharedassets9.assets',
                'sharedassets24.assets', 'sharedassets25.assets', 'sharedassets29.assets', 'sharedassets30.assets',
                'sharedassets34.assets', 'sharedassets35.assets', 'sharedassets36.assets']
COMPUTE_FILES = ['resources.assets', 'sharedassets0.assets', 'sharedassets29.assets']
# Unity's allocator block sizes (smaller than the desktop defaults; less address space / memory reserved)
BOOT_EXTRA = """memorysetup-main-allocator-block-size=4194304
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
"""


def progress(p, text):
    print(f'PROGRESS {int(p)} {text}', flush=True)


def fail(text):
    print(f'ERROR {text}', flush=True)
    sys.exit(1)


def run(script, *args):
    env = dict(os.environ, RAFT_GLES='1')
    r = subprocess.run([PY] + os.environ.get('RAFT_PYFLAGS', '-I').split() + [os.path.join(HERE, script)] + list(args), capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f'{script} {os.path.basename(args[0]) if args else ""}: {(r.stderr or r.stdout).strip()[-600:]}')
    return r.stdout


class AlignedZip:
    """stored zip with every file's data 4-byte aligned (zipalign's 0xD935 extra field)"""
    def __init__(self, path):
        self.z = zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED, allowZip64=True)

    def _info(self, name, size):
        zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_STORED
        zi.file_size = size
        hdr = self.z.fp.tell() + 30 + len(name.encode('utf-8'))
        pad = (-(hdr + 6)) % 4
        zi.extra = struct.pack('<HHH', 0xD935, 2 + pad, 4) + b'\0' * pad
        return zi

    def add_file(self, path, name):
        with open(path, 'rb') as src, self.z.open(self._info(name, os.path.getsize(path)), 'w') as dst:
            shutil.copyfileobj(src, dst, 1 << 20)

    def add_bytes(self, data, name):
        with self.z.open(self._info(name, len(data)), 'w') as dst:
            dst.write(data)

    def close(self):
        self.z.close()


def find_data(root):
    for c in (root, os.path.join(root, 'Raft_Data')):
        if os.path.isfile(os.path.join(c, 'globalgamemanagers')) and os.path.isdir(os.path.join(c, 'StreamingAssets')):
            return c
    fail('no encuentro Raft_Data en la carpeta elegida (tiene que ser la carpeta "Raft" de Steam)')


# our overlay (RaftRecomp.dll, compiled into the APK's IL2CPP code): Unity starts these methods
OVERLAY_INITS = [('Init', 0), ('StartLog', 4)]      # loadTypes: 0 AfterSceneLoad, 4 SubsystemRegistration


def overlay_json(rt_bytes, sa_bytes):
    """RuntimeInitializeOnLoads.json / ScriptingAssemblies.json (game's lists) + our overlay assembly"""
    rt = json.loads(rt_bytes.decode('utf-8-sig'))
    for m, t in OVERLAY_INITS:
        e = {'assemblyName': 'RaftRecomp', 'nameSpace': '', 'className': 'RecompOverlay', 'methodName': m,
             'loadTypes': t, 'isUnityClass': False}
        if e not in rt['root']: rt['root'].append(e)
    sa = json.loads(sa_bytes.decode('utf-8-sig'))
    if 'RaftRecomp.dll' not in sa['names']:
        sa['names'].append('RaftRecomp.dll'); sa['types'].append(16)
    return {'RuntimeInitializeOnLoads.json': json.dumps(rt, indent=4).encode(),
            'ScriptingAssemblies.json': json.dumps(sa, indent=4).encode()}


def json_with_overlay(orig_dir):
    rd = lambda n: open(os.path.join(orig_dir, n), 'rb').read()
    return overlay_json(rd('RuntimeInitializeOnLoads.json'), rd('ScriptingAssemblies.json'))


def apk_code_entries(apk):
    """what the data zip takes from the APK: IL2CPP metadata + Unity's Android default resources"""
    D = 'assets/bin/Data/'
    with zipfile.ZipFile(apk) as a:
        for n in a.namelist():
            if n.startswith(D + 'Managed/') or n == D + 'unity default resources':
                yield n, a.read(n)


def refresh(out_zip, apk):
    """new APK, same game data: copy the zip with the APK's code parts and the overlay lists replaced
    (the user's Raft copy is not needed again)"""
    D = 'assets/bin/Data/'
    progress(1, 'Actualizando los datos para esta versión')
    tmp = out_zip + '.tmp'
    z = AlignedZip(tmp)
    with zipfile.ZipFile(out_zip) as old:
        names = old.namelist()
        lists = overlay_json(old.read(D + 'RuntimeInitializeOnLoads.json'), old.read(D + 'ScriptingAssemblies.json'))
        skip = {D + n for n in lists}
        total = sum(old.getinfo(n).file_size for n in names) or 1; done = 0
        for n in names:
            info = old.getinfo(n); done += info.file_size
            if n.startswith(D + 'Managed/') or n == D + 'unity default resources' or n in skip: continue
            with old.open(n) as src, z.z.open(z._info(n, info.file_size), 'w') as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            progress(1 + 97 * done / total, 'Actualizando los datos para esta versión')
    for n, data in apk_code_entries(apk): z.add_bytes(data, n)
    for n, data in lists.items(): z.add_bytes(data, D + n)
    z.close()
    os.replace(tmp, out_zip)
    progress(100, 'Listo')


def convert_one(orig, work, name):
    """-> list of (path, arcname) ready for the zip"""
    src = os.path.join(orig, name)
    dst = os.path.join(work, name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if name.endswith('.assets') or re.fullmatch(r'level\d+', name):
        run('shrink_textures.py', src, dst)
        run('compact_ress.py', orig, work, name)
    else:
        shutil.copyfile(src, dst)
    if name in SHADER_FILES:
        run('convert_shaders.py', dst, dst, '--shader-src', src)
    if name in COMPUTE_FILES:
        run('convert_compute.py', dst, dst)
    run('set_platform.py', '13', dst)
    out = [(dst, name)]
    if os.path.exists(dst + '.resS'): out.append((dst + '.resS', name + '.resS'))
    return out


def main():
    if sys.argv[1] == '--refresh':                 # python android_convert.py --refresh <data zip> <apk>
        return refresh(sys.argv[2], sys.argv[3])
    root, out_zip, banks, apk, work = sys.argv[1:6]
    t0 = time.time()
    orig = find_data(root)
    os.makedirs(work, exist_ok=True); os.makedirs(banks, exist_ok=True)
    os.environ.setdefault('RAFT_WORK', work)
    os.environ.setdefault('RAFT_DROP2', '1024'); os.environ.setdefault('RAFT_DROP3', '4096')

    # same game build the phone's code was compiled from?
    exp = os.path.join(HERE, 'expected_assembly.sha256')
    if os.path.exists(exp):
        h = hashlib.sha256(open(os.path.join(orig, 'Managed', 'Assembly-CSharp.dll'), 'rb').read()).hexdigest()
        if h != open(exp).read().split()[0]:
            fail('esta copia de Raft es de otra versión que la del APK (Assembly-CSharp.dll distinto)')

    files = sorted(f for f in os.listdir(orig) if f.endswith('.assets') or re.fullmatch(r'level\d+', f))
    files.append('Resources/unity_builtin_extra')
    sizes = {f: os.path.getsize(os.path.join(orig, f)) + (os.path.getsize(os.path.join(orig, f + '.resS'))
             if os.path.exists(os.path.join(orig, f + '.resS')) else 0) for f in files}
    total = sum(sizes.values()) or 1
    files.sort(key=lambda f: -sizes[f])            # big ones first: the pool ends with the short jobs

    tmp_zip = out_zip + '.tmp'
    z = AlignedZip(tmp_zip)
    D = 'assets/bin/Data/'
    progress(1, 'Preparando')
    for n, data in apk_code_entries(apk): z.add_bytes(data, n)
    for n, data in json_with_overlay(orig).items():
        z.add_bytes(data, D + n)
    z.add_bytes(open(os.path.join(orig, 'boot.config'), 'rb').read().rstrip(b'\n') + b'\n' + BOOT_EXTRA.encode(), D + 'boot.config')
    # globalgamemanagers: graphics API list -> OpenGL ES 3
    g = os.path.join(work, 'globalgamemanagers')
    run('patch_buildsettings.py', os.path.join(orig, 'globalgamemanagers'), g, '11')
    run('set_platform.py', '13', g)
    z.add_file(g, D + 'globalgamemanagers'); os.remove(g)
    # StreamingAssets: FMOD banks to their folder (FMOD reads plain files), the rest into the zip
    sa = os.path.join(orig, 'StreamingAssets')
    for dp, _, fns in os.walk(sa):
        for fn in fns:
            p = os.path.join(dp, fn); rel = os.path.relpath(p, sa).replace(os.sep, '/')
            if fn.endswith('.bank'): shutil.copyfile(p, os.path.join(banks, fn))
            else: z.add_file(p, 'assets/' + rel)
    progress(3, 'Bancos de sonido copiados')

    done = 0
    workers = int(os.environ.get('RAFT_JOBS', '2'))
    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(convert_one, orig, work, f): f for f in files}
        from concurrent.futures import as_completed
        for fu in as_completed(futs):
            f = futs[fu]
            try: parts = fu.result()
            except Exception as ex: fail(str(ex))
            for p, n in parts:
                z.add_file(p, D + n); os.remove(p)
            done += sizes[f]
            el = time.time() - t0
            left = el / done * (total - done) if done else 0
            progress(3 + 96 * done / total, f'{f} ({int(left // 60)} min restantes)')
    z.close()
    os.replace(tmp_zip, out_zip)
    shutil.rmtree(work, ignore_errors=True)
    progress(100, f'Listo en {int((time.time() - t0) // 60)} min')


if __name__ == '__main__':
    main()
