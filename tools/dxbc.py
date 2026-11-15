# DXBC helpers: pull the container out of a Unity D3D11 sub program and give it back a resource
# definition (RDEF) chunk built from Unity's own parameter data (the game's bytecode is stripped).
import re, struct

FOURCC = lambda s: struct.unpack('<I', s)[0]

# D3D shader variable class / type, resource input types, SRV dimensions
SVC_SCALAR, SVC_VECTOR, SVC_MATRIX_COLUMNS = 0, 1, 3
SVT = {0: 3, 1: 2, 2: 1, 3: 3, 4: 2, 5: 19}          # Unity param type -> D3D_SVT (float,int,bool,half,short,uint)
SIT_CBUFFER, SIT_TEXTURE, SIT_SAMPLER = 0, 2, 3
SRV_DIM = {2: 4, 3: 8, 4: 9, 5: 5, 6: 10}             # Unity tex dim -> D3D_SRV_DIMENSION
RETURN_FLOAT = 5


def find_dxbc(code):
    i = code.find(b'DXBC')
    if i < 0: raise ValueError('no DXBC in program')
    size = struct.unpack_from('<I', code, i + 24)[0]
    return code[:i], code[i:i + size]


def chunks(dx):
    n = struct.unpack_from('<I', dx, 28)[0]
    out = []
    for k in range(n):
        o = struct.unpack_from('<I', dx, 32 + 4 * k)[0]
        cc, sz = struct.unpack_from('<4sI', dx, o)
        out.append((cc, dx[o + 8:o + 8 + sz]))
    return out


def container(chs):
    hdr = 32 + 4 * len(chs)
    offs, body = [], b''
    for cc, data in chs:
        offs.append(hdr + len(body))
        body += struct.pack('<4sI', cc, len(data)) + data
    total = hdr + len(body)
    return b'DXBC' + b'\0' * 16 + struct.pack('<III', 1, total, len(chs)) + b''.join(struct.pack('<I', o) for o in offs) + body


def shader_version(dx):
    for cc, data in chunks(dx):
        if cc in (b'SHDR', b'SHEX'):
            tok = struct.unpack_from('<I', data, 0)[0]
            return (tok >> 16) & 0xFFFF, (tok >> 4) & 0xF, tok & 0xF   # program type (0 PS, 1 VS), major, minor
    raise ValueError('no SHDR')


class _Blob:
    def __init__(s): s.b = bytearray()
    def at(s): return len(s.b)
    def u32(s, *v):
        for x in v: s.b += struct.pack('<I', x & 0xFFFFFFFF)
    def u16(s, *v):
        for x in v: s.b += struct.pack('<H', x)
    def patch(s, pos, v): struct.pack_into('<I', s.b, pos, v & 0xFFFFFFFF)
    def cstr(s, t):
        p = s.at(); s.b += t.encode() + b'\0'
        while len(s.b) % 4: s.b += b'\xab'
        return p


def var_size(p):
    regs = p['cols'] if p['isMatrix'] else 1           # column-major: one register per column
    last = ((regs - 1) * 16 + p['rows'] * 4) if p['isMatrix'] else p['cols'] * 4
    return (max(p['arraySize'], 1) - 1) * regs * 16 + last


def build_rdef(info, ptype, major, minor, pad=False):
    """info: {'cbs': [{'name','size','params':[...]}], 'cbBind': {name: slot},
              'textures': [{'name','index','sampler','dim','ms'}]}"""
    sm5 = major >= 5
    b = _Blob()
    cbs = [c for c in info['cbs'] if c['name'] and c['name'] in info['cbBind']]
    if pad:
        cbs = [dict(c, params=padded(c['params'], c['size'], re.sub(r'\W', '', c['name']))) for c in cbs]
    # header
    b.u32(len(cbs), 0, 0, 0, ((0xFFFE if ptype == 1 else 0xFFFF) << 16) | (major << 4) | minor, 0x100, 0)
    if sm5:
        b.b += b'RD11'
        b.u32(60, 24, 32, 40, 36, 12, 0)
    # resource bindings
    binds = []
    for t in info['textures']:
        binds.append(('T', t))
    samplers = {}
    for t in info['textures']:
        if t['sampler'] >= 0 and t['sampler'] not in samplers:
            samplers[t['sampler']] = 'sampler' + t['name']
    for slot, name in sorted(samplers.items()):
        binds.append(('S', {'name': name, 'index': slot}))
    for c in cbs:
        binds.append(('C', {'name': c['name'], 'index': info['cbBind'][c['name']]}))
    b.patch(8, len(binds))
    b.patch(12, b.at())
    bind_pos = []
    for kind, x in binds:
        bind_pos.append(b.at())
        if kind == 'T':
            ms = x.get('ms', 0)
            b.u32(0, SIT_TEXTURE, RETURN_FLOAT, 6 if ms else SRV_DIM.get(x['dim'], 4), 0xFFFFFFFF if not ms else 0, x['index'], 1, 0x0C)
        elif kind == 'S':
            b.u32(0, SIT_SAMPLER, 0, 0, 0, x['index'], 1, 0)
        else:
            b.u32(0, SIT_CBUFFER, 0, 0, 0, x['index'], 1, 0)
    # constant buffers
    b.patch(4, b.at())
    cb_pos = []
    for c in cbs:
        cb_pos.append(b.at())
        b.u32(0, len(c['params']), 0, c['size'], 0, 0)
    var_pos = []
    for ci, c in enumerate(cbs):
        b.patch(cb_pos[ci] + 8, b.at())
        vp = []
        for p in c['params']:
            vp.append(b.at())
            b.u32(0, p['offset'], var_size(p), 2, 0, 0)
            if sm5: b.u32(0xFFFFFFFF, 0, 0xFFFFFFFF, 0)
        var_pos.append(vp)
    # types
    for ci, c in enumerate(cbs):
        for vi, p in enumerate(c['params']):
            b.patch(var_pos[ci][vi] + 16, b.at())
            cls = SVC_MATRIX_COLUMNS if p['isMatrix'] else (SVC_SCALAR if p['cols'] == 1 and p['rows'] == 1 else SVC_VECTOR)
            b.u16(cls, SVT.get(p['type'], 3), p['rows'], p['cols'], p['arraySize'], 0)
            b.u32(0)
            if sm5: b.u32(0, 0, 0, 0, 0)
    # strings
    b.patch(24, b.cstr('dxbc2glsl'))
    for (kind, x), pos in zip(binds, bind_pos):
        b.patch(pos, b.cstr(x['name']))
    for ci, c in enumerate(cbs):
        b.patch(cb_pos[ci], b.cstr(c['name']))
        for vi, p in enumerate(c['params']):
            b.patch(var_pos[ci][vi], b.cstr(p['name']))
    return bytes(b.b)


def with_rdef(dx, info, pad=False):
    ptype, major, minor = shader_version(dx)
    chs = [(cc, d) for cc, d in chunks(dx) if cc != b'RDEF']
    return container([(b'RDEF', build_rdef(info, ptype, major, minor, pad))] + chs)


def input_signature(dx):
    """[(semantic, index, register, systemValue)] of the ISGN chunk"""
    for cc, d in chunks(dx):
        if cc == b'ISGN':
            n = struct.unpack_from('<I', d, 0)[0]
            out = []
            for i in range(n):
                no, si, sv, ct, reg = struct.unpack_from('<IIIII', d, 8 + 24 * i)
                out.append((d[no:d.index(b'\0', no)].decode(), si, reg, sv))
            return out
    return []


# ---- compute: resource kinds come from the SHEX declarations, names from Unity's kernel data ----
SIT_UAV_RWTYPED, SIT_STRUCTURED, SIT_UAV_RWSTRUCTURED, SIT_BYTEADDRESS, SIT_UAV_RWBYTEADDRESS = 4, 5, 6, 7, 8
RES_DIM_TO_SRV = {1: 1, 2: 2, 3: 4, 4: 6, 5: 8, 6: 9, 7: 3, 8: 5, 9: 7, 10: 10}   # D3D10_SB_RESOURCE_DIMENSION -> D3D_SRV_DIMENSION


def shex_decls(dx):
    """[(group, slot, sit, dimension)] for every resource / uav / sampler / cbuffer declaration"""
    code = [d for cc, d in chunks(dx) if cc in (b'SHDR', b'SHEX')][0]
    toks = struct.unpack_from('<%dI' % (len(code) // 4), code)
    out, i = [], 2
    while i < len(toks):
        t = toks[i]
        op = t & 0x7FF
        ln = (t >> 24) & 0x7F
        if op == 0x35:                       # customdata block: length in the next token
            ln = toks[i + 1]
        ext = (t >> 31) & 1
        j = i + 1
        while ext and j < len(toks):        # extended opcode tokens
            ext = (toks[j] >> 31) & 1; j += 1
        def reg(k):                          # operand token at k, register index right after
            return toks[k + 1]
        if op == 0x58:   out.append(('t', reg(j), SIT_TEXTURE, RES_DIM_TO_SRV.get((t >> 11) & 0x1F, 4), toks[j + 2] & 0xF))
        elif op == 0xA1: out.append(('t', reg(j), SIT_BYTEADDRESS, 11))
        elif op == 0xA2: out.append(('t', reg(j), SIT_STRUCTURED, 1, toks[j + 2]))
        elif op == 0x9C: out.append(('u', reg(j), SIT_UAV_RWTYPED, RES_DIM_TO_SRV.get((t >> 11) & 0x1F, 4), toks[j + 2] & 0xF))
        elif op == 0x9D: out.append(('u', reg(j), SIT_UAV_RWBYTEADDRESS, 11))
        elif op == 0x9E: out.append(('u', reg(j), SIT_UAV_RWSTRUCTURED, 1, toks[j + 2]))
        elif op == 0x5A: out.append(('s', reg(j), SIT_SAMPLER, 0))
        elif op == 0x59: out.append(('c', reg(j), SIT_CBUFFER, 0))
        if ln == 0: break
        i += ln
    return [x if len(x) == 5 else x + (0,) for x in out]


def padded(params, size=0, tag=''):
    """fill the holes between used members (and up to the buffer size) with 4-byte pads: uniform
    blocks need the full D3D layout"""
    out, at = [], 0
    for p in sorted(params, key=lambda p: p['offset']):
        while at < p['offset']:
            out.append({'name': 'hlslcc_pad%s_%d' % (tag, at), 'type': 5, 'rows': 1, 'cols': 1, 'isMatrix': 0, 'arraySize': 0, 'offset': at})
            at += 4
        out.append(p)
        at = max(at, p['offset'] + var_size(p))
    while at < size:
        out.append({'name': 'hlslcc_pad%s_%d' % (tag, at), 'type': 5, 'rows': 1, 'cols': 1, 'isMatrix': 0, 'arraySize': 0, 'offset': at})
        at += 4
    return out


def build_rdef_compute(dx, names, cbdefs):
    """names: {(group, slot): name}; cbdefs: {name: {'size', 'params'}}"""
    ptype, major, minor = shader_version(dx)
    b = _Blob()
    decls = shex_decls(dx)
    cbs = [(names.get(('c', s), 'cb%d' % s), s) for g, s, sit, dim, st in decls if g == 'c']
    cbdefs = {k: {'size': v['size'], 'params': padded(v['params'], v['size'], re.sub(r'\W', '', k))} for k, v in cbdefs.items()}
    # structured buffers: HLSLcc takes the element stride from a same-named "resource bind info" buffer
    for g, s, sit, dim, st in decls:
        if sit in (SIT_STRUCTURED, SIT_UAV_RWSTRUCTURED):
            nm = names.get((g, s), '%s%d' % (g, s))
            cbs.append((nm, None))
            cbdefs[nm] = {'size': st, 'params': [{'name': '$Element', 'type': 5, 'rows': 1, 'cols': max(1, min(4, st // 4)),
                                                  'isMatrix': 0, 'arraySize': 0, 'offset': 0, '_size': st}]}
    b.u32(len(cbs), 0, 0, 0, (0x4353 << 16) | (major << 4) | minor, 0x100, 0)
    if major >= 5:
        b.b += b'RD11'; b.u32(60, 24, 32, 40, 36, 12, 0)
    b.patch(8, len(decls)); b.patch(12, b.at())
    bind_pos = []
    for g, s, sit, dim, st in decls:
        bind_pos.append(b.at())
        if sit in (SIT_TEXTURE, SIT_UAV_RWTYPED):
            b.u32(0, sit, st or RETURN_FLOAT, dim, 0xFFFFFFFF, s, 1, 0x0C)
        elif sit in (SIT_STRUCTURED, SIT_UAV_RWSTRUCTURED, SIT_BYTEADDRESS, SIT_UAV_RWBYTEADDRESS):
            b.u32(0, sit, 6, dim, 0, s, 1, 0)       # return type: mixed
        else:
            b.u32(0, sit, 0, 0, 0, s, 1, 0)
    b.patch(4, b.at())
    cb_pos = []
    for name, s in cbs:
        cb_pos.append(b.at())
        cd = cbdefs.get(name, {'size': 16, 'params': []})
        b.u32(0, len(cd['params']), 0, cd['size'] if s is None else (cd['size'] + 15) & ~15, 0, 3 if s is None else 0)
    var_pos = []
    for ci, (name, s) in enumerate(cbs):
        cd = cbdefs.get(name, {'size': 16, 'params': []})
        b.patch(cb_pos[ci] + 8, b.at())
        vp = []
        for p in cd['params']:
            vp.append(b.at())
            b.u32(0, p['offset'], p.get('_size') or var_size(p), 2, 0, 0)
            if major >= 5: b.u32(0xFFFFFFFF, 0, 0xFFFFFFFF, 0)
        var_pos.append(vp)
    for ci, (name, s) in enumerate(cbs):
        for vi, p in enumerate(cbdefs.get(name, {'params': []})['params']):
            b.patch(var_pos[ci][vi] + 16, b.at())
            cls = SVC_MATRIX_COLUMNS if p['isMatrix'] else (SVC_SCALAR if p['cols'] == 1 and p['rows'] == 1 else SVC_VECTOR)
            b.u16(cls, SVT.get(p['type'], 3), p['rows'], p['cols'], p['arraySize'], 0)
            b.u32(0)
            if major >= 5: b.u32(0, 0, 0, 0, 0)
    b.patch(24, b.cstr('dxbc2glsl'))
    for (g, s, sit, dim, st), pos in zip(decls, bind_pos):
        if g == 'c': nm = names.get(('c', s), 'cb%d' % s)
        elif g == 's': nm = names.get(('s', s), 'sampler%d' % s)
        else: nm = names.get((g, s), '%s%d' % (g, s))
        b.patch(pos, b.cstr(nm))
    for ci, (name, s) in enumerate(cbs):
        b.patch(cb_pos[ci], b.cstr(name))
        for vi, p in enumerate(cbdefs.get(name, {'params': []})['params']):
            b.patch(var_pos[ci][vi], b.cstr(p['name']))
    return bytes(b.b)


def with_rdef_compute(dx, names, cbdefs):
    chs = [(cc, d) for cc, d in chunks(dx) if cc != b'RDEF']
    return container([(b'RDEF', build_rdef_compute(dx, names, cbdefs))] + chs)
