# Raft Recomp (Android): vertex compression of the PC meshes, like Unity's mobile builds do.
# The PC data keeps every vertex attribute as float32; on the phone mesh data is ~1 GB of GPU memory and a
# world gets killed by Android's low-memory killer. Per mesh:
#   normal   float32 x3  -> float16 x4         tangent float32 x4 -> float16 x4
#   uv0..7   float32 x2  -> float16 x2 (only if every value is within +-UV_LIMIT, else kept)
#   color    float32 x4  -> unorm8 x4 (only if within [0,1])
#   position, skin weights / indices: unchanged
# Streamed vertex data (.resS) is rewritten into a new .resS together with everything else still streamed.
# usage: compress_meshes.py <asset-file>   (in place; reads <asset-file>.resS if present)
import os, sys
import numpy as np
import UnityPy

FLOAT, FLOAT16, UNORM8 = 0, 1, 2
FSIZE = {0: 4, 1: 2, 2: 1, 3: 1, 4: 2, 5: 2, 6: 1, 7: 1, 8: 2, 9: 2, 10: 4, 11: 4}
UV_LIMIT = float(os.environ.get('RAFT_UV_LIMIT', '8'))
NORMAL, TANGENT, COLOR = 1, 2, 3
UVS = range(4, 12)


def layout(channels):
    """-> per stream: (stride, [(channel index, offset, format, dim)])"""
    streams = {}
    for i, c in enumerate(channels):
        dim = c['dimension'] & 0xF
        if dim == 0: continue
        streams.setdefault(c['stream'], []).append((i, c['offset'], c['format'], dim))
    out = {}
    for s, chs in streams.items():
        chs.sort(key=lambda x: x[1])
        stride = max(off + FSIZE[fmt] * dim for _, off, fmt, dim in chs)
        out[s] = (stride, chs)
    return out


def stream_offsets(lay, count):
    offs, pos = {}, 0
    for s in sorted(lay):
        pos = (pos + 15) & ~15
        offs[s] = pos
        pos += lay[s][0] * count
    return offs, pos


def compress(vd):
    count = vd['m_VertexCount']
    data = bytes(vd['m_DataSize'])
    chans = vd['m_Channels']
    lay = layout(chans)
    offs, total = stream_offsets(lay, count)
    if count == 0 or total > len(data): return None
    # read every channel as float arrays (only the float32 ones are candidates)
    arrays, plan = {}, {}
    for s, (stride, chs) in lay.items():
        base = np.frombuffer(data, np.uint8, count * stride, offs[s]).reshape(count, stride)
        for i, off, fmt, dim in chs:
            raw = np.ascontiguousarray(base[:, off:off + FSIZE[fmt] * dim])
            arrays[i] = raw
            plan[i] = (fmt, dim)
            if fmt != FLOAT: continue
            v = raw.view(np.float32).reshape(count, dim)
            if not np.all(np.isfinite(v)): continue
            if i == NORMAL and dim == 3:
                plan[i] = (FLOAT16, 4); arrays[i] = np.hstack([v, np.zeros((count, 1), np.float32)]).astype(np.float16).view(np.uint8)
            elif i == TANGENT and dim == 4:
                plan[i] = (FLOAT16, 4); arrays[i] = v.astype(np.float16).view(np.uint8)
            elif i in UVS and dim == 2 and np.abs(v).max(initial=0) <= UV_LIMIT:
                plan[i] = (FLOAT16, 2); arrays[i] = v.astype(np.float16).view(np.uint8)
            elif i == COLOR and dim == 4 and v.min(initial=0) >= 0 and v.max(initial=0) <= 1:
                plan[i] = (UNORM8, 4); arrays[i] = np.round(v * 255).astype(np.uint8)
    if all(plan[i] == (chans[i]['format'], chans[i]['dimension'] & 0xF) for i in plan): return None
    # new layout: same streams, same channel order, packed
    new_lay, new_ch = {}, [dict(c) for c in chans]
    for s, (_, chs) in lay.items():
        off = 0; lst = []
        for i, _, _, _ in chs:
            fmt, dim = plan[i]
            new_ch[i]['offset'] = off; new_ch[i]['format'] = fmt
            new_ch[i]['dimension'] = (chans[i]['dimension'] & ~0xF) | dim
            lst.append((i, off)); off += FSIZE[fmt] * dim
        new_lay[s] = (off, lst)
    noffs, ntotal = stream_offsets({s: (v[0], None) for s, v in new_lay.items()}, count)
    out = bytearray(ntotal)
    for s, (stride, lst) in new_lay.items():
        block = np.zeros((count, stride), np.uint8)
        for i, off in lst:
            a = np.asarray(arrays[i]).reshape(count, -1)
            block[:, off:off + a.shape[1]] = a
        out[noffs[s]:noffs[s] + count * stride] = block.tobytes()
    return new_ch, bytes(out), len(data) - ntotal


def main():
    path = sys.argv[1]
    if '/steamapps/' in os.path.realpath(path): sys.exit('refusing to write into Steam')
    env = UnityPy.load(path)
    ress = path + '.resS'
    old = open(ress, 'rb').read() if os.path.exists(ress) else b''
    blob = bytearray(); saved = done = 0; changed = False

    def put(b):
        while len(blob) % 16: blob.append(0)
        o = len(blob); blob.extend(b); return o

    for obj in env.objects:
        t = obj.type.name
        if t not in ('Mesh', 'Texture2D', 'Texture2DArray', 'Cubemap', 'Texture3D', 'AudioClip', 'VideoClip'): continue
        try: d = obj.read_typetree()
        except Exception: continue
        sd = d.get('m_StreamData') if isinstance(d.get('m_StreamData'), dict) else None
        res = d.get('m_Resource') if isinstance(d.get('m_Resource'), dict) else None
        streamed = sd and sd.get('size') and (sd.get('path') or '').endswith('.resS')
        if t == 'Mesh' and not d.get('m_MeshCompression'):
            vd = d['m_VertexData']
            if streamed: vd['m_DataSize'] = old[sd['offset']:sd['offset'] + sd['size']]
            r = compress(vd)
            if r:
                vd['m_Channels'], newdata, sv = r
                saved += sv; done += 1
                if streamed:
                    sd['offset'] = put(newdata); sd['size'] = len(newdata); vd['m_DataSize'] = b''
                else:
                    vd['m_DataSize'] = newdata
                obj.save_typetree(d); changed = True; continue
            if streamed: vd['m_DataSize'] = b''
        # anything else still streamed: carry its bytes into the new .resS
        if streamed:
            sd['offset'] = put(old[sd['offset']:sd['offset'] + sd['size']]); obj.save_typetree(d); changed = True
        elif res and res.get('m_Size') and (res.get('m_Source') or '').endswith('.resS'):
            res['m_Offset'] = put(old[res['m_Offset']:res['m_Offset'] + res['m_Size']]); obj.save_typetree(d); changed = True
    if changed:
        with open(path + '.tmp', 'wb') as f: f.write(env.file.save())
        os.replace(path + '.tmp', path)
        if old or blob:
            with open(ress + '.tmp', 'wb') as f: f.write(blob)
            os.replace(ress + '.tmp', ress)
    print(f'{os.path.basename(path)}: {done} meshes compressed, {saved >> 20} MB saved')


if __name__ == '__main__':
    main()
