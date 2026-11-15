# set the graphics APIs the player was "built" with (Raft ships [2] = Direct3D11 only)
import sys, os, UnityPy
src, dst = sys.argv[1], sys.argv[2]
apis = [int(x) for x in sys.argv[3].split(',')]
if '/steamapps/' in os.path.realpath(os.path.dirname(os.path.abspath(dst))): sys.exit('refusing to write into Steam')
env = UnityPy.load(src)
for o in env.objects:
    if o.type.name == 'BuildSettings':
        d = o.read_typetree(); print('was', d['m_GraphicsAPIs']); d['m_GraphicsAPIs'] = apis; o.save_typetree(d)
data = env.file.save()
if os.path.islink(dst): os.unlink(dst)
open(dst, 'wb').write(data)
print('now', apis)
