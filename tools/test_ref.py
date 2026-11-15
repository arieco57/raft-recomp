# translate a dumped reference shader's D3D11 programs and compare with Unity's own GLCore output
import sys, os, json, subprocess, struct
sys.path.insert(0, os.path.dirname(__file__))
from ushader import entries, parse_prog
from uparams import name_table, common_info, blob_info, merge
from dxbc import find_dxbc, with_rdef
ref, work = sys.argv[1], sys.argv[2]
os.makedirs(work, exist_ok=True)
d = json.load(open(f'{ref}/shader.json'))
kw = d['m_ParsedForm']['m_KeywordNames']
d3d = entries(open(f'{ref}/p4_0.bin', 'rb').read())
gl = entries(open(f'{ref}/p15_0.bin', 'rb').read()) if os.path.exists(f'{ref}/p15_0.bin') else None
jobs = []
for si, sub in enumerate(d['m_ParsedForm']['m_SubShaders']):
    for pi, ps in enumerate(sub['m_Passes']):
        names = name_table(ps)
        V, F = ps['progVertex'], ps['progFragment']
        def lst(prog, t):
            l = prog['m_PlayerSubPrograms'][3] if prog['m_PlayerSubPrograms'] else []
            ix = prog['m_ParameterBlobIndices'][3] if prog['m_ParameterBlobIndices'] else []
            return [(e, ix[k]) for k, e in enumerate(l) if e['m_GpuProgramType'] in t]
        vs, fs = lst(V, (15, 16)), lst(F, (17, 18))
        glv = lst(V, (6,))
        for k, (ve, vpi) in enumerate(vs):
            key = tuple(ve['m_KeywordIndices'])
            fe = [f for f in fs if tuple(f[0]['m_KeywordIndices']) == key]
            if not fe: print('no fragment for', key); continue
            fe, fpi = fe[0]
            out = f'{work}/s{si}p{pi}v{k}'
            for e, pidx, prog, tag in ((ve, vpi, V, 'vs'), (fe, fpi, F, 'ps')):
                info = merge(common_info(prog, names), blob_info(d3d[pidx]))
                pre, dx = find_dxbc(parse_prog(d3d[e['m_BlobIndex']])['code'])
                open(f'{out}.{tag}', 'wb').write(with_rdef(dx, info))
            jobs.append(f'{out}.vs {out}.ps {out}')
            if gl:
                g = [x for x in glv if tuple(x[0]['m_KeywordIndices']) == key]
                if g: open(f'{out}.ref.glsl', 'wb').write(parse_prog(gl[g[0][0]['m_BlobIndex']])['code'])
open(f'{work}/jobs.txt', 'w').write('\n'.join(jobs) + '\n')
print(subprocess.run([os.path.join(os.path.dirname(__file__), 'dxbc2glsl'), f'{work}/jobs.txt'], capture_output=True, text=True).stdout)
