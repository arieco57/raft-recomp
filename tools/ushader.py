# Unity 2021.3 shader blob helpers (platform blob = entries of serialized sub programs / parameter blocks)
import struct
MAGIC = 0x0c0a75ba
class R:
    def __init__(s, b, p=0): s.b, s.p = b, p
    def u32(s): v = struct.unpack_from('<I', s.b, s.p)[0]; s.p += 4; return v
    def i32(s): v = struct.unpack_from('<i', s.b, s.p)[0]; s.p += 4; return v
    def u8(s): v = s.b[s.p]; s.p += 1; return v
    def align(s): s.p = (s.p + 3) & ~3
    def bytes_(s):
        n = s.u32(); v = s.b[s.p:s.p + n]; s.p += n; s.align(); return v
    def str_(s): return s.bytes_().decode('utf8', 'replace')
    def strs(s): return [s.str_() for _ in range(s.u32())]
def entries(b):
    n = struct.unpack_from('<I', b, 0)[0]
    return [b[o:o + l] for o, l, _ in (struct.unpack_from('<III', b, 4 + 12 * i) for i in range(n))]
def parse_prog(x):
    r = R(x)
    d = {'version': r.u32(), 'type': r.u32(), 'stats': [r.u32() for _ in range(4)]}
    d['keywords'] = r.strs()
    d['code'] = r.bytes_()
    d['tail'] = x[r.p:]
    return d

# ---- parameter blocks ----
# layout: magic, nCB, CB[nCB] (CB[0] is the unnamed global block), nBind, Bind[nBind]
#   CB   = name, size, nParam, Param[nParam], nStruct(0)
#   Param= name, type, rows, cols, isMatrix, arraySize, offset
#   Bind = name, kind, then kind 0 (texture): index, samplerIndex, dimFlags ; kind 1 (cbuffer): index, arraySize
def parse_params(x):
    r = R(x)
    assert r.u32() == MAGIC
    cbs = []
    for _ in range(r.u32()):
        cb = {'name': r.str_(), 'size': r.u32(), 'params': []}
        for _ in range(r.u32()):
            cb['params'].append({'name': r.str_(), 'type': r.u32(), 'rows': r.u32(), 'cols': r.u32(), 'isMatrix': r.u32(), 'arraySize': r.u32(), 'offset': r.u32()})
        cb['structs'] = r.u32()
        assert cb['structs'] == 0, 'struct params'
        cbs.append(cb)
    binds = []
    for _ in range(r.u32()):
        b = {'name': r.str_(), 'kind': r.u32()}
        if b['kind'] == 0: b.update(index=r.u32(), sampler=r.u32(), dim=r.u32())
        elif b['kind'] == 1: b.update(index=r.u32(), arraySize=r.u32())
        else: raise ValueError('bind kind %d at %d' % (b['kind'], r.p))
        binds.append(b)
    rest = x[r.p:]
    return {'cbs': cbs, 'binds': binds, 'rest': rest}


def entries_segmented(segs):
    """platform blob split in segments: the (offset, length, segment) table lives in segment 0 and each
    entry's offset is relative to its own segment"""
    t = segs[0]
    n = struct.unpack_from('<I', t, 0)[0]
    out = []
    for i in range(n):
        o, l, s = struct.unpack_from('<III', t, 4 + 12 * i)
        out.append(segs[s][o:o + l])
    return out
