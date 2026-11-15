# print the GLCore programs of a converted shader: dump_glsl.py <asset> "<Shader/Name>" [variant-index]
import sys, os, UnityPy, lz4.block
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ushader import entries_segmented, parse_prog
env = UnityPy.load(sys.argv[1])
for o in env.objects:
    if o.type.name != 'Shader': continue
    d = o.read_typetree()
    if d['m_ParsedForm']['m_Name'] != sys.argv[2] or not ({15, 9} & set(d['platforms'])): continue
    i = d['platforms'].index(15 if 15 in d['platforms'] else 9); blob = bytes(d['compressedBlob'])
    segs = [lz4.block.decompress(blob[a:a + c], uncompressed_size=u) for a, c, u in zip(d['offsets'][i], d['compressedLengths'][i], d['decompressedLengths'][i])]
    es = entries_segmented(segs)
    for si, s in enumerate(d['m_ParsedForm']['m_SubShaders']):
        for pi, p in enumerate(s['m_Passes']):
            for k, e in enumerate(p['progVertex']['m_PlayerSubPrograms'][3]):
                if len(sys.argv) > 3 and k != int(sys.argv[3]): continue
                pr = parse_prog(es[e['m_BlobIndex']])
                print(f'===== subshader {si} pass {pi} ({p["m_Name"]}) variant {k} keywords {pr["keywords"]}')
                print(pr['code'].decode('utf8', 'replace'))
