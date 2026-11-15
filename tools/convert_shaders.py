# Raft Recomp: rewrite the D3D11-only shaders of a Unity 2021.3 asset file as GLCore (platform 15).
#
# Every pass's vertex + fragment D3D11 sub programs are paired by keywords, their DXBC gets a resource
# definition chunk rebuilt from Unity's parameters (the game's bytecode is stripped), HLSLcc turns each
# pair into one GLSL 150 program (the same converter Unity uses for GLCore), and the program's
# parameter block is written from HLSLcc's reflection. The D3D11 platform is replaced by the GLCore one.
#
# usage: convert_shaders.py <in-asset> <out-asset> [--only "Shader/Name"]
# The input file is only read. The output is written only if at least one shader was converted.
import os, sys, struct, subprocess, tempfile, shutil, collections
import lz4.block
import UnityPy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ushader import MAGIC, entries, entries_segmented, parse_prog
from uparams import name_table, common_info, blob_info, merge
from dxbc import find_dxbc, with_rdef
from glsl_patches import PATCHES
import re as _re

TOOL = os.environ.get('RAFT_DXBC2GLSL') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dxbc2glsl')
D3D11 = 4
GLES = bool(os.environ.get('RAFT_GLES'))
GLCORE = 9 if GLES else 15          # platform: GLES3x or GLCore
VS_TYPES, FS_TYPES = (15, 16), (17, 18)
GL_PROG_TYPE = 4 if GLES else 6      # kShaderGpuProgramGLES3 / GLCore32
SVT_TO_UNITY = {3: 0, 2: 1, 1: 2, 19: 5}
TD_TO_DIM = {2: 2, 3: 3, 4: 4, 5: 2, 6: 5, 7: 6}


class W:
    def __init__(s): s.b = bytearray()
    def u32(s, *v):
        for x in v: s.b += struct.pack('<I', x & 0xFFFFFFFF)
    def bytes_(s, data):
        s.u32(len(data)); s.b += data
        while len(s.b) % 4: s.b += b'\0'
    def str_(s, t): s.bytes_(t.encode())


def gl_program(code, keywords, mask):
    w = W()
    w.u32(MAGIC, GL_PROG_TYPE, 0, 0, 0, 0)
    w.u32(len(keywords))
    for k in keywords: w.str_(k)
    w.bytes_(code)
    w.u32(mask, 0)                   # vertex channel mask, no explicit bind channels (GL binds in_* by name)
    return bytes(w.b)


def gl_params(refl):
    uniforms, seen, textures, tseen, bad = [], set(), [], set(), []
    for line in refl.splitlines():
        f = line.split()
        if not f: continue
        if f[0] == 'C':
            _, st, cb, name, _bind, t, rows, cols, ismat, arr = f
            if name in seen: continue
            seen.add(name)
            uniforms.append((name, SVT_TO_UNITY.get(int(t), 0), int(rows), int(cols), int(ismat), int(arr)))
        elif f[0] == 'T':
            _, st, name, unit, samp, ms, dim, uav = f
            if int(uav): bad.append('uav ' + name); continue
            if name in tseen: continue
            tseen.add(name)
            textures.append((name, int(unit), int(samp), int(ms), TD_TO_DIM.get(int(dim), 2)))
        elif f[0] == 'B':
            bad.append('buffer ' + f[2])
    w = W()
    w.u32(MAGIC, 2)
    w.u32(0, 0, 0, 0)                # unnamed global block, empty
    body, off = W(), 0
    for name, t, rows, cols, ismat, arr in uniforms:
        regs = cols if ismat else 1
        body.str_(name)
        body.u32(t, rows, cols, ismat, arr, off)
        off += max(arr, 1) * regs * 16
    w.str_('$Globals')
    w.u32(off, len(uniforms))
    w.b += body.b
    w.u32(0)                         # no struct params
    w.u32(len(textures))
    for name, unit, samp, ms, dim in textures:
        w.str_(name)
        w.u32(0, unit, samp, (dim << 1) | ms)
    return bytes(w.b), bad


def program_list(prog, types):
    if not prog['m_PlayerSubPrograms']: return []
    l, ix = prog['m_PlayerSubPrograms'][3], prog['m_ParameterBlobIndices'][3]
    return [(e, ix[k]) for k, e in enumerate(l) if e['m_GpuProgramType'] in types]


def pair_variants(vs, fs):
    """vertex/fragment sub programs -> (v, f) pairs; a stage-specific keyword means the other stage's
    best match is the one whose keywords are a subset."""
    pairs, used_v = [], set()
    def best(x, pool):
        kx = set(x[0]['m_KeywordIndices'])
        cands = [(len(set(p[0]['m_KeywordIndices'])), i) for i, p in enumerate(pool) if set(p[0]['m_KeywordIndices']) <= kx]
        return max(cands)[1] if cands else None
    for f in fs:
        exact = [i for i, v in enumerate(vs) if set(v[0]['m_KeywordIndices']) == set(f[0]['m_KeywordIndices'])]
        i = exact[0] if exact else best(f, vs)
        if i is not None:
            pairs.append((vs[i], f)); used_v.add(i)
    for i, v in enumerate(vs):
        if i in used_v: continue
        j = best(v, fs)
        if j is not None: pairs.append((v, fs[j]))
    return pairs


def convert_shader(d, work, tag, log):
    plats = d['platforms']
    if D3D11 not in plats or GLCORE in plats: return None
    pi = plats.index(D3D11)
    blob = bytes(d['compressedBlob'])
    segs = []
    for seg in range(len(d['offsets'][pi])):
        off, cl, dl = d['offsets'][pi][seg], d['compressedLengths'][pi][seg], d['decompressedLengths'][pi][seg]
        segs.append(lz4.block.decompress(blob[off:off + cl], uncompressed_size=dl))
    d3d = entries_segmented(segs)
    jobs, meta = [], []
    for si, sub in enumerate(d['m_ParsedForm']['m_SubShaders']):
        for pa, ps in enumerate(sub['m_Passes']):
            names = name_table(ps)
            V, F = ps['progVertex'], ps['progFragment']
            for other in ('progGeometry', 'progHull', 'progDomain'):
                if program_list(ps[other], range(100)):
                    log.append(f'  pass {si}.{pa}: has {other}, skipped')
            vs, fs = program_list(V, VS_TYPES), program_list(F, FS_TYPES)
            for k, (v, f) in enumerate(pair_variants(vs, fs)):
                out = f'{work}/{tag}_s{si}p{pa}_{k}'
                try:
                    progs = {}
                    for (e, pidx), prog, ext in ((v, V, 'vs'), (f, F, 'ps')):
                        info = merge(common_info(prog, names), blob_info(d3d[pidx]))
                        if info['unsupported']: raise ValueError(', '.join(info['unsupported']))
                        p = parse_prog(d3d[e['m_BlobIndex']])
                        progs[ext] = p
                        open(f'{out}.{ext}', 'wb').write(with_rdef(find_dxbc(p['code'])[1], info))
                except Exception as ex:
                    log.append(f'  pass {si}.{pa} variant {k}: {ex}')
                    continue
                jobs.append(f'{out}.vs {out}.ps {out}')
                kws = list(dict.fromkeys(progs['vs']['keywords'] + progs['ps']['keywords']))
                kwi = sorted(set(v[0]['m_KeywordIndices']) | set(f[0]['m_KeywordIndices']))
                mask = struct.unpack_from('<I', progs['vs']['tail'], 0)[0] if len(progs['vs']['tail']) >= 4 else 0
                req = v[0]['m_ShaderRequirements'] | f[0]['m_ShaderRequirements']
                meta.append((si, pa, out, kws, kwi, mask, req))
    return jobs, meta


def finish_shader(d, meta, log):
    new_entries, param_index, per_pass = [], {}, collections.defaultdict(list)
    failed = 0
    for si, pa, out, kws, kwi, mask, req in meta:
        if not os.path.exists(out + '.glsl'):
            failed += 1
            if os.path.exists(out + '.err'): log.append(f'  {os.path.basename(out)}: ' + open(out + '.err').read().strip().replace('\n', ' | ')[:300])
            continue
        pblob, bad = gl_params(open(out + '.refl').read())
        if bad:
            failed += 1; log.append(f'  {os.path.basename(out)}: ' + ', '.join(bad)); continue
        if pblob not in param_index:
            param_index[pblob] = len(new_entries); new_entries.append(pblob)
        code = open(out + '.glsl', 'rb').read()
        sname = d['m_ParsedForm']['m_Name']
        for pat, rep in [pr for k, v in PATCHES.items() if k == sname or (k.endswith('*') and sname.startswith(k[:-1])) for pr in v]:
            code, n = _re.subn(pat.encode(), rep.encode(), code)
            if n: log.append(f'  patched {n}x: {pat[:40]}')
        new_entries.append(gl_program(code, kws, mask))
        per_pass[(si, pa)].append({'m_BlobIndex': len(new_entries) - 1, 'm_KeywordIndices': kwi, 'm_ShaderRequirements': req,
                                   'm_GpuProgramType': GL_PROG_TYPE, '_param': param_index[pblob]})
    if not per_pass: return False, failed
    # platform blob: count, (offset, length, segment) per entry, then the entries
    hdr = 4 + 12 * len(new_entries)
    table, body = struct.pack('<I', len(new_entries)), b''
    for e in new_entries:
        table += struct.pack('<III', hdr + len(body), len(e), 0)
        body += e
    raw = table + body
    comp = lz4.block.compress(raw, store_size=False)
    d['platforms'] = [GLCORE]
    d['offsets'], d['compressedLengths'], d['decompressedLengths'] = [[0]], [[len(comp)]], [[len(raw)]]
    d['compressedBlob'] = comp
    d['stageCounts'] = [1]
    empty_common = {'m_VectorParams': [], 'm_MatrixParams': [], 'm_TextureParams': [], 'm_BufferParams': [],
                    'm_ConstantBuffers': [], 'm_ConstantBufferBindings': [], 'm_UAVParams': [], 'm_Samplers': []}
    for si, sub in enumerate(d['m_ParsedForm']['m_SubShaders']):
        for pa, ps in enumerate(sub['m_Passes']):
            ps['m_Platforms'] = [GLCORE]
            for key in ('progVertex', 'progFragment', 'progGeometry', 'progHull', 'progDomain', 'progRayTracing'):
                prog = ps[key]
                if 'm_PlayerSubPrograms' not in prog: continue
                lst = per_pass.get((si, pa), []) if key == 'progVertex' else []
                prog['m_PlayerSubPrograms'] = [[], [], [], [{k: v for k, v in e.items() if k != '_param'} for e in lst]]
                prog['m_ParameterBlobIndices'] = [[], [], [], [e['_param'] for e in lst]]
                prog['m_SubPrograms'] = []
                prog['m_CommonParameters'] = dict(empty_common)
    return True, failed


def main():
    src, dst = sys.argv[1], sys.argv[2]
    only = sys.argv[sys.argv.index('--only') + 1] if '--only' in sys.argv else None
    target = os.path.realpath(os.path.dirname(os.path.abspath(dst))) + '/' + os.path.basename(dst)
    if '/steamapps/' in target or (not os.path.islink(dst) and '/steamapps/' in os.path.realpath(dst)):
        sys.exit('refusing to write into the Steam install: ' + dst)
    env = UnityPy.load(src)
    # --shader-src <original asset>: take the D3D11 shaders from there (same path ids) and write them,
    # converted, into <src> (e.g. the Android copy, whose textures were re-encoded and shaders converted before)
    shsrc = None
    if '--shader-src' in sys.argv:
        senv = UnityPy.load(sys.argv[sys.argv.index('--shader-src') + 1])
        shsrc = {o.path_id: o for o in senv.objects if o.type.name == 'Shader'}
    work = tempfile.mkdtemp(prefix='raftsh_', dir=os.environ.get('RAFT_WORK'))
    todo = []
    for obj in env.objects:
        if obj.type.name != 'Shader': continue
        d = (shsrc[obj.path_id] if shsrc and obj.path_id in shsrc else obj).read_typetree()
        name = d['m_ParsedForm']['m_Name']
        if only and name != only: continue
        if os.environ.get('RAFT_SKIP') and __import__('re').search(os.environ['RAFT_SKIP'], name):
            print('SKIP', name); continue
        log = []
        r = convert_shader(d, work, f'o{obj.path_id}'.replace('-', 'm'), log)
        if r is None: continue
        todo.append((obj, d, name, r[1], log))
        with open(f'{work}/jobs.txt', 'a') as jf:
            for j in r[0]: jf.write(j + '\n')
    if not todo: print('no D3D11 shaders'); return
    res = subprocess.run([TOOL] + (['--es'] if GLES else []) + [f'{work}/jobs.txt'], capture_output=True, text=True)
    print('hlslcc:', res.stdout.strip())
    changed = 0
    for obj, d, name, meta, log in todo:
        ok, failed = finish_shader(d, meta, log)
        print(f'{"OK  " if ok else "FAIL"} {name}: {len(meta) - failed}/{len(meta)} variants')
        for l in log[:6]: print(l)
        if ok:
            obj.save_typetree(d); changed += 1
    if changed:
        data = env.file.save()
        tmp = dst + '.tmp'
        with open(tmp, 'wb') as f: f.write(data)
        if os.path.islink(dst): os.unlink(dst)
        os.replace(tmp, dst)
        print(f'wrote {dst} ({changed} shaders)')
    if not os.environ.get('RAFT_KEEP'): shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
