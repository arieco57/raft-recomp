# compile overlay/RecompOverlay.cs with the Unity editor's Roslyn and register it with the player:
# ScriptingAssemblies.json (assembly list) + RuntimeInitializeOnLoads.json (Init entry point)
import os, sys, json, subprocess
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, 'game', 'Raft_Data')
M = os.path.join(DATA, 'Managed')
SDK = os.path.join(HERE, 'unity', 'sdk', 'Editor', 'Data')
refs = ['mscorlib.dll', 'netstandard.dll', 'System.dll', 'System.Core.dll', 'UnityEngine.dll', 'UnityEngine.CoreModule.dll',
        'UnityEngine.IMGUIModule.dll', 'UnityEngine.TextRenderingModule.dll', 'UnityEngine.InputLegacyModule.dll',
        'UnityEngine.AndroidJNIModule.dll', 'Unity.InputSystem.dll']
out = os.path.join(M, 'RaftRecomp.dll')
# the editor's own Mono + mcs (its .NET 5 runtime needs OpenSSL 1.1, which current distros dropped)
MONO = os.path.join(SDK, 'MonoBleedingEdge')
cmd = [os.path.join(MONO, 'bin', 'mono'), os.path.join(MONO, 'lib', 'mono', '4.5', 'mcs.exe'), '-target:library',
       '-nostdlib', '-optimize', '-out:' + out] + ['-r:' + os.path.join(M, r) for r in refs] + [os.path.join(HERE, 'overlay', 'RecompOverlay.cs')]
r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stdout, r.stderr)
if r.returncode: sys.exit(1)

def rewrite(name, fn):
    p = os.path.join(DATA, name)
    d = json.load(open(p))
    fn(d)
    if os.path.islink(p): os.unlink(p)
    json.dump(d, open(p, 'w'))

def assemblies(d):
    if 'RaftRecomp.dll' not in d['names']:
        d['names'].append('RaftRecomp.dll'); d['types'].append(16)
# loadTypes: 0 AfterSceneLoad, 4 SubsystemRegistration (StartLog: Android log file, as early as possible)
INITS = [("Init", 0), ("StartLog", 4)]
def inits(d):
    for m, t in INITS:
        e = {"assemblyName": "RaftRecomp", "nameSpace": "", "className": "RecompOverlay", "methodName": m, "loadTypes": t, "isUnityClass": False}
        if e not in d['root']: d['root'].append(e)
rewrite('ScriptingAssemblies.json', assemblies)
rewrite('RuntimeInitializeOnLoads.json', inits)
print('installed', out)
