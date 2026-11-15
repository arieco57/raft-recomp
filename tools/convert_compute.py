# Raft Recomp: add a GLCore (renderer 17) variant to the D3D11-only ComputeShaders of an asset file.
# Kernel metadata is copied as is (Unity's GL device uses the same bind points); the code is the
# kernel's DXBC (with a rebuilt resource definition) translated by HLSLcc to GLSL 430.
# usage: convert_compute.py <in-asset> <out-asset> [--test]   (--test: translate only, keep files)
import os, re, sys, copy, subprocess, tempfile, shutil
import UnityPy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dxbc import with_rdef_compute

TOOL = os.environ.get('RAFT_DXBC2GLSL') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dxbc2glsl')
R_D3D11 = 2
GLES = bool(os.environ.get('RAFT_GLES'))
R_GLCORE, GL_LEVEL = (11, 3) if GLES else (17, 11)   # renderer GLES3x / GLCore


def kernel_inputs(variant, kv):
    names = {}
    for cb in kv['cbs']: names[('c', cb['bindPoint'])] = cb['name']
    for t in kv['textures']:
        names[('t', t['bindPoint'])] = t['name']
        if t['samplerBindPoint'] >= 0: names.setdefault(('s', t['samplerBindPoint']), 'sampler' + t['name'])
    for bs in kv['builtinSamplers']:
        names[('s', bs['bindPoint'])] = bs.get('name') or 'sampler_builtin%d' % bs['bindPoint']
    for b in kv['inBuffers']: names[('t', b['bindPoint'])] = b['name']
    for b in kv['outBuffers']: names[('u', b['bindPoint'])] = b['name']
    cbdefs = {}
    for i, cb in enumerate(kv['cbs']):
        vi = kv['cbVariantIndices'][i] if i < len(kv['cbVariantIndices']) else i
        c = variant['constantBuffers'][vi]
        cbdefs[cb['name']] = {'size': c['byteSize'], 'params': [
            {'name': p['name'], 'type': p['type'], 'rows': p['rowCount'], 'cols': p['colCount'], 'isMatrix': int(p['rowCount'] > 1),
             'arraySize': p['arraySize'], 'offset': p['offset']} for p in c['params']]}
    return names, cbdefs


IMAGE_FLAG = 0x04000000


def gl_metadata(kv, glsl):
    """Unity's GL compute metadata differs from D3D11's: textures carry their GLSL name and texture
    unit, images their image unit | 0x04000000, and every texture unit without a sampler of its own
    gets a 'no sampler' builtin entry"""
    text = glsl.decode('latin1')
    units = {m.group(2): int(m.group(1)) for m in re.finditer(r'UNITY_LOCATION\((\d+)\)\s+uniform\s+\w*\s*\w*sampler\w*\s+(\w+)\s*;', text)}
    images = {m.group(2): int(m.group(1)) for m in re.finditer(r'layout\(binding\s*=\s*(\d+)[^)]*\)\s*uniform\s+\w*\s*\w*image\w*\s+(\w+)\s*;', text)}
    builtin = []
    for t in kv['textures']:
        unit = units.get(t['name'], t['bindPoint'])
        t['generatedName'] = t['name']
        t['bindPoint'] = unit
        if t['samplerBindPoint'] >= 0: t['samplerBindPoint'] = unit
        else: builtin.append({'sampler': 0xFFFFFFFF, 'bindPoint': unit})
    kv['builtinSamplers'] = list(kv['builtinSamplers']) + builtin
    for b in kv['outBuffers']:
        if b['texDimension'] != -1:
            b['bindPoint'] = IMAGE_FLAG | images.get(b['name'], b['bindPoint'])
    missing = [t['name'] for t in kv['textures'] if t['name'] not in units] + \
              [b['name'] for b in kv['outBuffers'] if b['texDimension'] != -1 and b['name'] not in images]
    return missing


R_VULKAN = 21
UAV_FLAG = 0x02000000
RE_V_UBO = re.compile(r'layout\(set = (\d+), binding = (\d+), std140\) uniform (\w+)')
RE_V_RES = re.compile(r'layout\(set = (\d+), binding = (\d+)\) uniform\s+(?:\w+\s+)?(texture\w+|sampler\w*)\s+(\w+)\s*;')
RE_V_IMG = re.compile(r'layout\(set = (\d+), binding = (\d+)(?:, \w+)?\)\s*(?:\w+\s+)*uniform\s+(?:\w+\s+)?[ui]?image\w+\s+(\w+)\s*;')
RE_V_BUF = re.compile(r'layout\(set = (\d+), binding = (\d+), std430\)\s*(?:\w+\s+)*buffer\s+(\w+)')


def vk_metadata(kv, glsl):
    """Unity's Vulkan compute metadata: (set << 16) | binding, UAVs flagged 0x02000000; $Globals is CGlobals"""
    orig = lambda n: re.sub(r'_origX\d+X$', '', n)
    ubo = {m.group(3): (int(m.group(1)) << 16) | int(m.group(2)) for m in RE_V_UBO.finditer(glsl)}
    tex, smp = {}, {}
    for m in RE_V_RES.finditer(glsl):
        (tex if 'texture' in m.group(3) else smp)[m.group(4)] = (int(m.group(1)) << 16) | int(m.group(2))
    img = {orig(m.group(3)): (int(m.group(1)) << 16) | int(m.group(2)) for m in RE_V_IMG.finditer(glsl)}
    buf = {orig(m.group(3)): (int(m.group(1)) << 16) | int(m.group(2)) for m in RE_V_BUF.finditer(glsl)}
    pairs = {}
    for m in re.finditer(r'\bsampler\w*\(\s*(\w+)\s*,\s*(\w+)\s*\)', glsl): pairs.setdefault(m.group(1), m.group(2))
    missing = []
    for cb in kv['cbs']:
        name = 'CGlobals' if cb['name'] == '$Globals' else cb['name']
        cb['name'] = name
        if name in ubo: cb['bindPoint'] = ubo[name]
        else: missing.append(name)
    for t in kv['textures']:
        if t['name'] not in tex: missing.append(t['name']); continue
        t['bindPoint'] = tex[t['name']]
        sn = pairs.get(t['name'])
        t['samplerBindPoint'] = smp[sn] if sn in smp else -1
    for b in kv['inBuffers']:
        if b['name'] in buf: b['bindPoint'] = buf[b['name']]
        elif b['name'] in tex: b['bindPoint'] = tex[b['name']]
        else: missing.append(b['name'])
    for b in kv['outBuffers']:
        v = img.get(b['name'], buf.get(b['name']))
        if v is None: missing.append(b['name']); continue
        b['bindPoint'] = UAV_FLAG | v
    return missing


def compile_comp(path):
    src = re.sub(r'\b(mediump|lowp)\b', '', open(path).read())
    open(path + '.comp', 'w').write(src)
    r = subprocess.run(['glslangValidator', '-V', '--target-env', 'vulkan1.0', '-S', 'comp', '-o', path + '.spv', path + '.comp'], capture_output=True, text=True)
    if r.returncode: return None, r.stdout[-300:]
    return open(path + '.spv', 'rb').read(), None


def main():
    src, dst = sys.argv[1], sys.argv[2]
    test = '--test' in sys.argv
    vulkan = '--vulkan' in sys.argv
    target = R_VULKAN if vulkan else R_GLCORE
    if not test and '/steamapps/' in os.path.realpath(os.path.dirname(os.path.abspath(dst))): sys.exit('refusing to write into Steam')
    env = UnityPy.load(src)
    work = tempfile.mkdtemp(prefix='raftcs_', dir=os.environ.get('RAFT_WORK'))
    todo, jobs = [], []
    for obj in env.objects:
        if obj.type.name != 'ComputeShader': continue
        d = obj.read_typetree()
        rs = [v['targetRenderer'] for v in d['variants']]
        if R_D3D11 not in rs or target in rs: continue
        v = d['variants'][rs.index(R_D3D11)]
        refs = []
        for ki, k in enumerate(v['kernels']):
            for mi, (kw, kv) in enumerate(k['variantMap']):
                names, cbdefs = kernel_inputs(v, kv)
                out = f'{work}/o' + str(obj.path_id).replace('-', 'm') + f'_k{ki}_{mi}'
                open(out + '.cs', 'wb').write(with_rdef_compute(bytes(kv['code']), names, cbdefs))
                jobs.append(f'CS {out}.cs {out}')
                refs.append((ki, mi, out))
        todo.append((obj, d, v, refs))
    if not todo: print('no D3D11 compute shaders'); return
    open(f'{work}/jobs.txt', 'w').write('\n'.join(jobs) + '\n')
    print('hlslcc:', subprocess.run([TOOL] + (['--vulkan'] if vulkan else []) + (['--es'] if GLES else []) + [f'{work}/jobs.txt'], capture_output=True, text=True).stdout.strip())
    changed = 0
    for obj, d, v, refs in todo:
        gl = copy.deepcopy(v)
        gl['targetRenderer'], gl['targetLevel'] = (R_VULKAN, 0) if vulkan else (R_GLCORE, GL_LEVEL)
        if vulkan:
            for c in gl['constantBuffers']:
                if c['name'] == '$Globals': c['name'] = 'CGlobals'
        bad = 0
        for ki, mi, out in refs:
            if not os.path.exists(out + '.glsl'):
                bad += 1
                print('   ', d['m_Name'], gl['kernels'][ki]['name'], open(out + '.err').read().strip()[:200] if os.path.exists(out + '.err') else '')
                continue
            code = open(out + '.glsl', 'rb').read()
            kw, kv = gl['kernels'][ki]['variantMap'][mi]
            if vulkan:
                miss = vk_metadata(kv, code.decode('latin1'))
                code, err = compile_comp(out + '.glsl')
                if err:
                    bad += 1; print('   ', d['m_Name'], gl['kernels'][ki]['name'], 'glslang:', err); continue
            else:
                miss = gl_metadata(kv, code)
                code += b'\0'
            if miss: print('   ', d['m_Name'], gl['kernels'][ki]['name'], 'not found in GLSL:', miss)
            kv['code'] = code if isinstance(kv['code'], (bytes, bytearray)) else list(code)
        print(f'{"OK  " if not bad else "FAIL"} {d["m_Name"]}: {len(refs) - bad}/{len(refs)} kernels')
        if bad or test: continue
        d['variants'].append(gl)
        obj.save_typetree(d); changed += 1
    if test: print('kept', work); return
    if changed:
        data = env.file.save()
        if os.path.islink(dst): os.unlink(dst)
        with open(dst + '.tmp', 'wb') as f: f.write(data)
        os.replace(dst + '.tmp', dst)
        print(f'wrote {dst} ({changed} compute shaders)')
    shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
