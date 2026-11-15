# compare our Vulkan translation of a shader's D3D11 programs with Unity's own Vulkan programs
# usage: test_vk_ref.py <asset> "<Shader/Name>" [max-variants]
import os, sys, struct, subprocess, tempfile, json
import UnityPy, lz4.block
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ushader import entries_segmented, parse_prog, parse_params
import convert_shaders_vk as vk

def plat_entries(d, plat):
    i = d['platforms'].index(plat); blob = bytes(d['compressedBlob'])
    segs = [lz4.block.decompress(blob[a:a + c], uncompressed_size=u) for a, c, u in zip(d['offsets'][i], d['compressedLengths'][i], d['decompressedLengths'][i])]
    return entries_segmented(segs)

def summarize(pblob):
    p = parse_params(pblob)
    tex = {b['name']: (hex(b['index']), hex(b['sampler']), b['dim']) for b in p['binds'] if b['kind'] == 0}
    cbs = {b['name']: hex(b['index']) for b in p['binds'] if b['kind'] == 1}
    offs = {c['name'][:8]: {q['name']: q['offset'] for q in c['params']} for c in p['cbs'] if c['name']}
    return tex, cbs, {c['name']: c['size'] for c in p['cbs'] if c['name']}, offs

env = UnityPy.load(sys.argv[1]); want = sys.argv[2]; maxv = int(sys.argv[3]) if len(sys.argv) > 3 else 3
for o in env.objects:
    if o.type.name != 'Shader': continue
    d = o.read_typetree()
    if d['m_ParsedForm']['m_Name'] != want: continue
    ref = plat_entries(d, 18)
    work = tempfile.mkdtemp(dir=os.environ.get('RAFT_WORK'))
    log = []
    d2 = json.loads(json.dumps(d, default=lambda x: list(x) if isinstance(x, (bytes, bytearray)) else str(x)))
    d2['compressedBlob'] = bytes(d['compressedBlob'])
    d2['platforms'] = [p for p in d['platforms'] if p != 18]
    jobs, meta = vk.convert_shader(d2, work, 'x', log)
    open(f'{work}/jobs.txt', 'w').write('\n'.join(jobs) + '\n')
    vk.run_hlslcc(work)
    for m in meta[:maxv]:
        si, pa, out, kws, kwi = m[:5]
        res, err = vk.build_variant(m)
        if err: print('ERR', err); continue
        mine = summarize(res[0])
        ps = d['m_ParsedForm']['m_SubShaders'][si]['m_Passes'][pa]['progVertex']
        l, ix = ps['m_PlayerSubPrograms'][3], ps['m_ParameterBlobIndices'][3]
        cand = [ix[k] for k, e in enumerate(l) if e['m_GpuProgramType'] == 25 and sorted(e['m_KeywordIndices']) == kwi]
        if not cand: print('no ref variant for', kwi); continue
        theirs = summarize(ref[cand[0]])
        print(f'== s{si}p{pa} kw {kws}')
        print('  tex   mine ', mine[0]); print('  tex   unity', theirs[0])
        print('  cbs   mine ', mine[1]); print('  cbs   unity', theirs[1])
        print('  sizes mine ', mine[2]); print('  sizes unity', theirs[2])
        for cb in theirs[3]:
            a, b = mine[3].get(cb, {}), theirs[3][cb]
            diff = {k: (a.get(k), b[k]) for k in set(a) | set(b) if a.get(k) != b.get(k)}
            print('  offsets', cb, 'identical' if not diff else diff)
