import sys, os, glob, collections, UnityPy
root = sys.argv[1]
files = sorted(set(glob.glob(root + '/*.assets') + glob.glob(root + '/level*') + glob.glob(root + '/Resources/*') + [root + '/globalgamemanagers']) - set(glob.glob(root + '/*.resS')) - set(glob.glob(root + '/*.resource')))
tot = collections.Counter()
for f in files:
    if f.endswith('.resS'): continue
    try: env = UnityPy.load(f)
    except Exception as e: print('ERR', f, e); continue
    n = collections.Counter(o.type.name for o in env.objects if o.type.name in ('Shader', 'ComputeShader'))
    if n: print(os.path.basename(f), dict(n)); tot.update(n)
print('TOTAL', dict(tot))
