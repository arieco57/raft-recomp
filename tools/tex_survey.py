import sys, glob, os, collections, UnityPy
root = sys.argv[1]
fmt = collections.Counter(); size = collections.Counter(); n = 0
for f in sorted(glob.glob(root + '/*.assets') + glob.glob(root + '/level*')):
    if f.endswith('.resS'): continue
    env = UnityPy.load(f)
    for o in env.objects:
        if o.type.name not in ('Texture2D', 'Cubemap', 'Texture2DArray'): continue
        d = o.read_typetree()
        k = (o.type.name, d.get('m_TextureFormat', d.get('m_Format')))
        fmt[k] += 1
        size[k] += d.get('m_CompleteImageSize', 0) if o.type.name == 'Texture2D' else d.get('m_DataSize', 0)
        n += 1
for k, v in fmt.most_common(): print(k, v, '%.0f MB' % (size[k] / 1048576))
print('total', n)
