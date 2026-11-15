# Raft Recomp (Android): rebuild an asset file's .resS with only the ranges still referenced (after the
# textures went inline, most of the original .resS is dead weight).
# usage: compact_ress.py <original-dir> <out-dir> <file>
#   reads <out-dir>/<file> if it exists (already processed), else <original-dir>/<file>;
#   writes <out-dir>/<file> and <out-dir>/<file>.resS (if anything is still streamed)
import os, sys, shutil
import UnityPy

orig, out, name = sys.argv[1], sys.argv[2], sys.argv[3]
src = os.path.join(out, name) if os.path.exists(os.path.join(out, name)) else os.path.join(orig, name)
env = UnityPy.load(src)
res_path = os.path.join(orig, name + '.resS')
blobs, changed, total = bytearray(), 0, 0
fh = open(res_path, 'rb') if os.path.exists(res_path) else None


def remap(sd, keys):
    global changed, total
    po, ps, pp = keys
    path, size = sd.get(pp) or '', sd.get(ps, 0) or 0
    if not path or not size: return False
    if not path.endswith('.resS') or fh is None: return False
    fh.seek(sd[po]); data = fh.read(size)
    # align to 16 like Unity does
    while len(blobs) % 16: blobs.append(0)
    sd[po] = len(blobs)
    blobs.extend(data)
    total += size
    return True


for obj in env.objects:
    if obj.type.name not in ('Mesh', 'Texture2D', 'Texture2DArray', 'Cubemap', 'Texture3D', 'AudioClip', 'VideoClip'): continue
    try: d = obj.read_typetree()
    except Exception: continue
    hit = False
    if isinstance(d.get('m_StreamData'), dict): hit = remap(d['m_StreamData'], ('offset', 'size', 'path'))
    elif isinstance(d.get('m_Resource'), dict): hit = remap(d['m_Resource'], ('m_Offset', 'm_Size', 'm_Source'))
    if hit:
        obj.save_typetree(d); changed += 1

if '/steamapps/' in os.path.realpath(out): sys.exit('refusing to write into Steam')
dst = os.path.join(out, name)
if changed:
    with open(dst + '.tmp', 'wb') as f: f.write(env.file.save())
    os.replace(dst + '.tmp', dst)
    with open(dst + '.resS', 'wb') as f: f.write(blobs)
elif src != dst:
    shutil.copyfile(src, dst)
print(f'{name}: {changed} streamed objects, resS {total / 1048576:.0f} MB')
