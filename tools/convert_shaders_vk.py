# Raft Recomp: rewrite the D3D11-only shaders of a Unity 2021.3 asset file as Vulkan (platform 18).
#
# Vulkan in Unity shares Direct3D's conventions (UV origin at the top, reversed Z, near clip at 1),
# which are baked into the game's D3D11 bytecode, so this is the faithful target.
# Per vertex + fragment pair: DXBC + rebuilt resource definitions (constant buffers padded to their
# D3D layout: every buffer is a std140 uniform block) -> HLSLcc (Vulkan bindings) -> glslang -> SPIR-V
# -> SMOL-V, inside Unity's Vulkan program container; the parameter block comes from the bindings
# HLSLcc chose ((stages << 24) | (set << 16) | binding) and Unity's own parameter data.
#
# usage: convert_shaders_vk.py <in-asset> <out-asset> [--only "Shader/Name"]
import os, sys, re, struct, subprocess, tempfile, shutil, collections, hashlib, zlib
from concurrent.futures import ThreadPoolExecutor
import lz4.block
import UnityPy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ushader import MAGIC, entries_segmented, parse_prog
from uparams import name_table, common_info, blob_info, merge
from dxbc import find_dxbc, with_rdef, input_signature
from convert_shaders import program_list, pair_variants, W, D3D11, VS_TYPES, FS_TYPES

TOOLS = os.path.dirname(os.path.abspath(__file__))
VULKAN, VK_PROG = 18, 25
STAGE_BIT = {'V': 0x04, 'F': 0x08}
DESC_UBO, DESC_TEX = 0x04, 0x08                      # observed in Unity's own blobs: high byte = stage mask
GLOBALS_PREFIX = {'V': 'VGlobals', 'F': 'PGlobals'}

RE_UBO = re.compile(r'layout\(set = (\d+), binding = (\d+), std140\) uniform (\w+)\s*\{')
RE_RES = re.compile(r'layout\(set = (\d+), binding = (\d+)\) uniform\s+(?:(?:highp|mediump|lowp)\s+)?(\w+)\s+(\w+)\s*;')


def stage_bindings(glsl):
    ubos = {m.group(3): (int(m.group(1)), int(m.group(2))) for m in RE_UBO.finditer(glsl)}
    tex, smp, bad = {}, {}, []
    for m in RE_RES.finditer(glsl):
        s, b, typ, name = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
        if 'texture' in typ: tex[name] = (s, b)
        elif typ.startswith('sampler'): smp[name] = (s, b)
        else: bad.append(f'{typ} {name}')
    if re.search(r'\)\s*(readonly\s+)?buffer\s+\w+', glsl): bad.append('storage buffer')
    # which sampler each texture is actually used with: sampler2D(_Tex, sampler_Tex)
    pairs = {}
    for m in re.finditer(r'\bsampler\w*\(\s*(\w+)\s*,\s*(\w+)\s*\)', glsl):
        pairs.setdefault(m.group(1), m.group(2))
    smp = dict(smp, _pairs=pairs)
    return ubos, tex, smp, bad


RE_BLOCK = re.compile(r'layout\(set = (\d+), binding = (\d+), std140\) uniform (\w+)\s*\{\n(.*?)\n\};\n', re.S)
RE_MEMBER = re.compile(r'^\s*(?:(?:highp|mediump|lowp|precise)\s+)*(\w+)\s+(\w+)(?:\[(\w+)\])?\s*;\s*$')
STD140 = {'float': (4, 4), 'int': (4, 4), 'uint': (4, 4), 'bool': (4, 4),
          'vec2': (8, 8), 'ivec2': (8, 8), 'uvec2': (8, 8), 'bvec2': (8, 8),
          'vec3': (12, 16), 'ivec3': (12, 16), 'uvec3': (12, 16), 'bvec3': (12, 16),
          'vec4': (16, 16), 'ivec4': (16, 16), 'uvec4': (16, 16), 'bvec4': (16, 16)}
KEEP_SEPARATE = ('UnityInstancing', 'UnityStereo')


def merge_blocks(text, stage, binding):
    """fold every uniform block of the stage into one '<V|P>Globals' block (set 1); returns the new
    text and [(member, std140 offset)]"""
    members, kept, first = [], [], None
    for m in RE_BLOCK.finditer(text):
        if m.group(3).startswith(KEEP_SEPARATE): kept.append(m.group(3)); continue
        first = first if first is not None else m.start()
        for line in m.group(4).split('\n'):
            mm = RE_MEMBER.match(line)
            if not mm: raise ValueError('uniform block member: ' + line.strip())
            if mm.group(2).startswith('hlslcc_pad'): continue
            members.append((mm.group(1), mm.group(2), mm.group(3)))
    if first is None: return text, []
    if kept: raise ValueError('separate blocks ' + ', '.join(kept))
    body = RE_BLOCK.sub(lambda m: '' if not m.group(3).startswith(KEEP_SEPARATE) else m.group(0), text)
    off, layout, decl = 0, [], []
    for typ, name, arr in members:
        if typ not in STD140: raise ValueError('uniform type ' + typ)
        size, align = STD140[typ]
        if arr:
            n = int(arr); stride = (size + 15) & ~15
            off = (off + 15) & ~15; layout.append((name, off)); off += stride * n
            decl.append(f'\t{typ} {name}[{n}];')
        else:
            off = (off + align - 1) & ~(align - 1); layout.append((name, off)); off += size
            decl.append(f'\t{typ} {name};')
    block = f'layout(set = 1, binding = {binding}, std140) uniform {GLOBALS_PREFIX[stage]} {{\n' + '\n'.join(decl) + '\n};\n'
    first = min(first, len(body))
    return body[:first] + block + body[first:], layout


def unity_param(info, member):
    """Unity's parameter for a GLSL member (matrices are vec4 arrays named hlslcc_mtx<r>x<c><name>)"""
    name = re.sub(r'^hlslcc_mtx\d+x\d+', '', member)
    for c in info['cbs']:
        for p in c['params']:
            if p['name'] == name: return p
    return None


def enc(stages, s, b):
    return (stages << 24) | (s << 16) | b


def vk_params(prog_hash, stages_info):
    """stages_info: {'V': (info, ubos, tex, smp, layout), 'F': ...} -> parameter block bytes"""
    cbs = {}       # unity name -> [size, {param name: param}, stage mask, (set, binding)]
    binds_tex = {}
    for st, (info, ubos, tex, smp, layout) in stages_info.items():
        for glname, (s, b) in ubos.items():
            uname = f'{glname}{prog_hash}'
            params, size = {}, 0
            for member, off in layout:
                p = unity_param(info, member)
                if p is None: continue
                params[p['name']] = dict(p, offset=off)
                regs = p['cols'] if p['isMatrix'] else 1
                size = max(size, off + (max(p['arraySize'], 1) * regs * 16 if (p['isMatrix'] or p['arraySize']) else p['cols'] * 4))
            cbs[uname] = [size, params, STAGE_BIT[st], (s, b)]
        samp_names = {}
        for t in info['textures']:
            if t['sampler'] >= 0: samp_names.setdefault(t['sampler'], 'sampler' + t['name'])
        # a sampler shared by several textures is listed only once (on the texture it belongs to),
        # the others get 0xFFFFFFFF, as Unity does (e.g. unity_SpecCube1 uses samplerunity_SpecCube0)
        owner = {}
        for t in info['textures']:
            sn = smp['_pairs'].get(t['name']) or samp_names.get(t['sampler'])
            if sn and (sn not in owner or sn == 'sampler' + t['name']): owner[sn] = t['name']
        for t in info['textures']:
            if t['name'] not in tex: continue
            s, b = tex[t['name']]
            sn = smp['_pairs'].get(t['name']) or samp_names.get(t['sampler'])
            sb = smp.get(sn) if sn and owner.get(sn) == t['name'] else None
            e = binds_tex.setdefault(t['name'], [s, b, sb, 0, (t['dim'] << 1) | t['ms']])
            e[3] |= STAGE_BIT[st]
    w = W()
    w.u32(MAGIC, len(cbs) + 1)
    w.u32(0, 0, 0, 0)                                # unnamed global block
    for uname in sorted(cbs):
        size, params, mask, sb = cbs[uname]
        w.str_(uname)
        w.u32(size, len(params))
        for pn in sorted(params):
            p = params[pn]
            w.str_(pn)
            w.u32(p['type'], p['rows'], p['cols'], p['isMatrix'], p['arraySize'], p['offset'])
        w.u32(0)
    w.u32(len(binds_tex) + len(cbs))
    for tn in sorted(binds_tex):
        s, b, sb, mask, dim = binds_tex[tn]
        w.str_(tn)
        w.u32(0, enc(mask, s, b), enc(mask, *sb) if sb else 0xFFFFFFFF, dim)
    for uname in sorted(cbs):
        size, params, mask, (s, b) = cbs[uname]
        w.str_(uname)
        w.u32(1, enc(mask, s, b), 0)
    return bytes(w.b)


def vk_container(vert_smolv, frag_smolv):
    hdr = bytearray(0xb0)
    foff = 0xb0
    voff = foff + len(frag_smolv)
    struct.pack_into('<5I', hdr, 0, 0x02000020, voff, len(vert_smolv), foff, len(frag_smolv))
    struct.pack_into('<I', hdr, 0x4c, 1)
    hdr[0x50:0x70] = hashlib.sha256(vert_smolv + frag_smolv).digest()
    return bytes(hdr) + frag_smolv + vert_smolv


def vk_program(code, keywords, mask, pairs):
    w = W()
    w.u32(MAGIC, VK_PROG, 0, 0, 0, 0)
    w.u32(len(keywords))
    for k in keywords: w.str_(k)
    w.bytes_(code)
    w.u32(mask, len(pairs))
    for src, tgt in pairs: w.u32(src, tgt)
    return bytes(w.b)


def compile_stage(path, stage):
    spv, smol = path + '.spv', path + '.smolv'
    src = open(path).read()
    # HLSLcc marks samplers mediump; in SPIR-V that becomes RelaxedPrecision and Mesa really runs those
    # in FP16 (HDR lighting saturates). Unity's own SPIR-V has full precision everywhere.
    src = re.sub(r'\b(mediump|lowp)\b', '', src)
    open(path, 'w').write(src)
    if stage == 'vert' and 'void main_hlslcc()' not in src:
        # Unity flips clip-space Y in every Vulkan vertex shader (D3D convention); HLSLcc does not
        src = src.replace('void main()', 'void main_hlslcc()', 1) + '\nvoid main()\n{\n    main_hlslcc();\n    gl_Position.y = -gl_Position.y;\n}\n'
        open(path, 'w').write(src)
    if 'Shadow' in src and 'textureLod' in src and 'GL_EXT_texture_shadow_lod' not in src:
        i = src.index('\n') + 1                                   # right after #version
        open(path, 'w').write(src[:i] + '#extension GL_EXT_texture_shadow_lod : enable\n' + src[i:])
    r = subprocess.run(['glslangValidator', '-V', '--target-env', 'vulkan1.0', '-S', stage, '-o', spv, path], capture_output=True, text=True)
    if r.returncode: return None, r.stdout[-400:]
    r = subprocess.run([os.path.join(TOOLS, 'smolv_tool'), 'encode', spv, smol], capture_output=True, text=True)
    if r.returncode: return None, 'smolv ' + r.stderr
    return open(smol, 'rb').read(), None


def convert_shader(d, work, tag, log):
    plats = d['platforms']
    if D3D11 not in plats or VULKAN in plats: return None
    pi = plats.index(D3D11)
    blob = bytes(d['compressedBlob'])
    segs = [lz4.block.decompress(blob[a:a + c], uncompressed_size=u) for a, c, u in zip(d['offsets'][pi], d['compressedLengths'][pi], d['decompressedLengths'][pi])]
    d3d = entries_segmented(segs)
    jobs, meta = [], []
    for si, sub in enumerate(d['m_ParsedForm']['m_SubShaders']):
        for pa, ps in enumerate(sub['m_Passes']):
            names = name_table(ps)
            V, F = ps['progVertex'], ps['progFragment']
            for other in ('progGeometry', 'progHull', 'progDomain'):
                if program_list(ps[other], range(100)): log.append(f'  pass {si}.{pa}: has {other}, skipped')
            vs, fs = program_list(V, VS_TYPES), program_list(F, FS_TYPES)
            for k, (v, f) in enumerate(pair_variants(vs, fs)):
                out = f'{work}/{tag}_s{si}p{pa}_{k}'
                try:
                    progs, infos, isgn = {}, {}, None
                    for (e, pidx), prog, ext in ((v, V, 'vs'), (f, F, 'ps')):
                        info = merge(common_info(prog, names), blob_info(d3d[pidx]))
                        if info['unsupported']: raise ValueError(', '.join(info['unsupported']))
                        p = parse_prog(d3d[e['m_BlobIndex']])
                        dx = find_dxbc(p['code'])[1]
                        if ext == 'vs': isgn = input_signature(dx)
                        progs[ext], infos[ext] = p, info
                        open(f'{out}.{ext}', 'wb').write(with_rdef(dx, info, pad=True))
                except Exception as ex:
                    log.append(f'  pass {si}.{pa} variant {k}: {ex}')
                    continue
                jobs.append(f'{out}.vs {out}.ps {out}')
                kws = list(dict.fromkeys(progs['vs']['keywords'] + progs['ps']['keywords']))
                kwi = sorted(set(v[0]['m_KeywordIndices']) | set(f[0]['m_KeywordIndices']))
                t = progs['vs']['tail']
                tw = struct.unpack_from('<%dI' % (len(t) // 4), t) if len(t) >= 8 else (0, 0)
                mask, n = tw[0], tw[1]
                # D3D bind targets name the shader input: 0 POSITION, 1 NORMAL, 2 TANGENT, 3 COLOR, 5+n TEXCOORDn;
                # Vulkan wants 13 + the input's location (= its register)
                regof = {(sem.upper(), idx): reg for sem, idx, reg, sv in isgn if sv == 0}
                def d3d_target_sem(t):
                    return {0: ('POSITION', 0), 1: ('NORMAL', 0), 2: ('TANGENT', 0), 3: ('COLOR', 0)}.get(t, ('TEXCOORD', t - 5) if 5 <= t <= 12 else None)
                pairs = []
                for i in range(n):
                    src, tgt = tw[2 + 2 * i], tw[3 + 2 * i]
                    sem = d3d_target_sem(tgt)
                    if sem in regof: pairs.append((src, sem))
                    else: log.append(f'  pass {si}.{pa} variant {k}: unknown bind target {tgt}')
                req = v[0]['m_ShaderRequirements'] | f[0]['m_ShaderRequirements']
                meta.append((si, pa, out, kws, kwi, mask, pairs, req, infos['vs'], infos['ps']))
    return jobs, meta


def build_variant(m):
    si, pa, out, kws, kwi, mask, pairs, req, vinfo, finfo = m
    if not os.path.exists(out + '.vert'):
        return None, (open(out + '.err').read().strip().replace('\n', ' | ')[:300] if os.path.exists(out + '.err') else 'hlslcc failed')
    vtxt, ftxt = open(out + '.vert').read(), open(out + '.frag').read()
    # Vulkan links varyings by location, not by name: HLSLcc numbers each stage on its own, so give
    # every fragment input the location its vertex output got
    outs = {m.group(2): m.group(1) for m in re.finditer(r'layout\(location = (\d+)\) out (?:highp )?\w+ (\w+)\s*;', vtxt)}
    ftxt = re.sub(r'layout\(location = (\d+)\) in ((?:highp )?\w+) (\w+)\s*;',
                  lambda m: f'layout(location = {outs.get(m.group(3), m.group(1))}) in {m.group(2)} {m.group(3)};', ftxt)
    if os.environ.get('RAFT_DEBUG_GB') and 'SV_Target3' in ftxt and 'SV_Target2' in ftxt and '_CameraGBufferTexture0' not in ftxt:
        # diagnostic for G-buffer passes: write an expression into the light buffer (SV_Target3)
        tgt = os.environ.get('RAFT_DEBUG_GB_TARGET', 'SV_Target3')
        ftxt = re.sub(r'\n(\s*)return;\n\}\s*$', lambda mm: f"\n{mm.group(1)}{tgt} = {os.environ['RAFT_DEBUG_GB']};\n{mm.group(1)}return;\n}}\n", ftxt)
    dbg_tex = os.environ.get('RAFT_DEBUG_TEX', '_CameraGBufferTexture0')
    if os.environ.get('RAFT_DEBUG_ALBEDO') and '_CameraGBufferTexture0' in ftxt and dbg_tex in ftxt:
        # diagnostic: output a texture read (default: the G-buffer albedo) instead of the lighting
        m = re.search(r'(u_xlat\d+) = texture\(sampler2D\(' + dbg_tex + r', \w+\), [^;]+\);', ftxt)
        ftxt = ftxt.replace('void main()', 'vec4 dbg_albedo;\nvoid main()', 1)
        if m: ftxt = ftxt.replace(m.group(0), m.group(0) + f'\n    dbg_albedo = {m.group(1)};', 1)
        if True:
            expr = os.environ.get('RAFT_DEBUG_EXPR', 'vec4(dbg_albedo.xyz, 1.0)')
            ftxt = re.sub(r'\n(\s*)return;\n\}\s*$', lambda mm: f'\n{mm.group(1)}SV_Target0 = {expr};\n{mm.group(1)}return;\n}}\n', ftxt)
    try:
        vtxt, vlay = merge_blocks(vtxt, 'V', 0)
        ftxt, flay = merge_blocks(ftxt, 'F', 1)
    except ValueError as ex:
        return None, str(ex)
    open(out + '.vert', 'w').write(vtxt)
    open(out + '.frag', 'w').write(ftxt)
    vb, fb = stage_bindings(vtxt), stage_bindings(ftxt)
    if vb[3] or fb[3]: return None, 'unsupported ' + ', '.join(vb[3] + fb[3])
    vsm, err = compile_stage(out + '.vert', 'vert')
    if err: return None, 'glslang vert: ' + err
    fsm, err = compile_stage(out + '.frag', 'frag')
    if err: return None, 'glslang frag: ' + err
    # vertex inputs: HLSLcc numbers the used inputs in order (not by register); Unity wants 13 + location
    locs = {m.group(2).upper(): int(m.group(1)) for m in re.finditer(r'layout\(location = (\d+)\) in (?:highp )?\w+ in_(\w+?)\s*;', vtxt)}
    pairs = [(src, 13 + locs[f'{sem}{idx}']) for src, (sem, idx) in pairs if f'{sem}{idx}' in locs]
    code = vk_container(vsm, fsm)
    h = zlib.crc32(code)
    params = vk_params(h, {'V': (vinfo,) + vb[:3] + (vlay,), 'F': (finfo,) + fb[:3] + (flay,)})
    return (params, vk_program(code, kws, mask, pairs)), None


def finish_shader(d, meta, log, pool):
    results = list(pool.map(build_variant, meta))
    new_entries, param_index, per_pass, failed = [], {}, collections.defaultdict(list), 0
    for m, (res, err) in zip(meta, results):
        si, pa, out, kws, kwi, mask, pairs, req = m[:8]
        if err:
            failed += 1; log.append(f'  {os.path.basename(out)}: {err}'); continue
        pblob, prog = res
        if pblob not in param_index:
            param_index[pblob] = len(new_entries); new_entries.append(pblob)
        new_entries.append(prog)
        per_pass[(si, pa)].append({'m_BlobIndex': len(new_entries) - 1, 'm_KeywordIndices': kwi, 'm_ShaderRequirements': req,
                                   'm_GpuProgramType': VK_PROG, '_param': param_index[pblob]})
    if not per_pass: return False, failed
    hdr = 4 + 12 * len(new_entries)
    table, body = struct.pack('<I', len(new_entries)), b''
    for e in new_entries:
        table += struct.pack('<III', hdr + len(body), len(e), 0)
        body += e
    raw = table + body
    comp = lz4.block.compress(raw, store_size=False)
    d['platforms'] = [VULKAN]
    d['offsets'], d['compressedLengths'], d['decompressedLengths'] = [[0]], [[len(comp)]], [[len(raw)]]
    d['compressedBlob'] = comp
    d['stageCounts'] = [1]
    empty = {'m_VectorParams': [], 'm_MatrixParams': [], 'm_TextureParams': [], 'm_BufferParams': [],
             'm_ConstantBuffers': [], 'm_ConstantBufferBindings': [], 'm_UAVParams': [], 'm_Samplers': []}
    for si, sub in enumerate(d['m_ParsedForm']['m_SubShaders']):
        for pa, ps in enumerate(sub['m_Passes']):
            ps['m_Platforms'] = [VULKAN]
            for key in ('progVertex', 'progFragment', 'progGeometry', 'progHull', 'progDomain', 'progRayTracing'):
                prog = ps[key]
                if 'm_PlayerSubPrograms' not in prog: continue
                lst = per_pass.get((si, pa), []) if key == 'progVertex' else []
                prog['m_PlayerSubPrograms'] = [[], [], [], [{k: v for k, v in e.items() if k != '_param'} for e in lst]]
                prog['m_ParameterBlobIndices'] = [[], [], [], [e['_param'] for e in lst]]
                prog['m_SubPrograms'] = []
                prog['m_CommonParameters'] = dict(empty)
    return True, failed


def run_hlslcc(work):
    """translate every job; if HLSLcc crashes on one, mark it failed and go on with the rest"""
    jobs = open(f'{work}/jobs.txt').read().split('\n')
    jobs = [j for j in jobs if j.strip()]
    crashed = 0
    while jobs:
        open(f'{work}/jobs_now.txt', 'w').write('\n'.join(jobs) + '\n')
        r = subprocess.run([os.path.join(TOOLS, 'dxbc2glsl'), '--vulkan', f'{work}/jobs_now.txt'], capture_output=True, text=True)
        if r.returncode in (0, 1): break
        done = 0
        while done < len(jobs):
            out = jobs[done].split()[2]
            if not (os.path.exists(out + '.vert') or os.path.exists(out + '.err')): break
            done += 1
        if done < len(jobs):
            open(jobs[done].split()[2] + '.err', 'w').write(f'HLSLcc crashed (signal {-r.returncode})')
            crashed += 1
        jobs = jobs[done + 1:]
    print(f'hlslcc: done ({crashed} crashed)')


def main():
    src, dst = sys.argv[1], sys.argv[2]
    only = sys.argv[sys.argv.index('--only') + 1] if '--only' in sys.argv else None
    if '/steamapps/' in os.path.realpath(os.path.dirname(os.path.abspath(dst))): sys.exit('refusing to write into Steam')
    env = UnityPy.load(src)
    work = tempfile.mkdtemp(prefix='raftvk_', dir=os.environ.get('RAFT_WORK'))
    todo = []
    for obj in env.objects:
        if obj.type.name != 'Shader': continue
        d = obj.read_typetree()
        name = d['m_ParsedForm']['m_Name']
        if only and name != only: continue
        if os.environ.get('RAFT_SKIP') and re.search(os.environ['RAFT_SKIP'], name):
            print('SKIP', name); continue
        log = []
        r = convert_shader(d, work, 'o' + str(obj.path_id).replace('-', 'm'), log)
        if r is None: continue
        todo.append((obj, d, name, r[1], log))
        with open(f'{work}/jobs.txt', 'a') as jf:
            for j in r[0]: jf.write(j + '\n')
    if not todo: print('no D3D11 shaders'); return
    run_hlslcc(work)
    changed = 0
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        for obj, d, name, meta, log in todo:
            ok, failed = finish_shader(d, meta, log, pool)
            print(f'{"OK  " if ok else "FAIL"} {name}: {len(meta) - failed}/{len(meta)} variants')
            for l in log[:4]: print(l)
            if ok:
                obj.save_typetree(d); changed += 1
    if changed:
        data = env.file.save()
        if os.path.islink(dst): os.unlink(dst)
        with open(dst + '.tmp', 'wb') as f: f.write(data)
        os.replace(dst + '.tmp', dst)
        print(f'wrote {dst} ({changed} shaders)')
    if not os.environ.get('RAFT_KEEP'): shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
