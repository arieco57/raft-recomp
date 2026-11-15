# Unity 2021.3 program parameters: a variant's parameters are the program's m_CommonParameters
# (typetree, names through the pass's m_NameIndices) plus its own parameter blob.
from ushader import parse_params


def name_table(pass_):
    return {idx: name for name, idx in pass_['m_NameIndices']}


def _vec(names, v):
    return {'name': names[v['m_NameIndex']], 'type': v['m_Type'], 'rows': 1, 'cols': v['m_Dim'], 'isMatrix': 0,
            'arraySize': v['m_ArraySize'], 'offset': v['m_Index']}


def _mat(names, m):
    return {'name': names[m['m_NameIndex']], 'type': m['m_Type'], 'rows': m['m_RowCount'], 'cols': m.get('m_ColumnCount', 4), 'isMatrix': 1,
            'arraySize': m['m_ArraySize'], 'offset': m['m_Index']}


def common_info(prog, names):
    c = prog['m_CommonParameters']
    info = {'cbs': [], 'cbBind': {}, 'textures': [], 'unsupported': []}
    loose = [_vec(names, v) for v in c['m_VectorParams']] + [_mat(names, m) for m in c['m_MatrixParams']]
    if loose: info['cbs'].append({'name': '', 'size': 0, 'params': loose})
    for cb in c['m_ConstantBuffers']:
        if cb['m_StructParams']: info['unsupported'].append('struct params in ' + names[cb['m_NameIndex']])
        info['cbs'].append({'name': names[cb['m_NameIndex']], 'size': cb['m_Size'],
                            'params': [_vec(names, v) for v in cb['m_VectorParams']] + [_mat(names, m) for m in cb['m_MatrixParams']]})
    for b in c['m_ConstantBufferBindings']:
        info['cbBind'][names[b['m_NameIndex']]] = b['m_Index']
    for t in c['m_TextureParams']:
        info['textures'].append({'name': names[t['m_NameIndex']], 'index': t['m_Index'], 'sampler': t['m_SamplerIndex'],
                                 'dim': t['m_Dim'], 'ms': int(t['m_MultiSampled'])})
    if c['m_BufferParams']: info['unsupported'].append('buffer params')
    if c['m_UAVParams']: info['unsupported'].append('uav params')
    return info


def blob_info(blob):
    p = parse_params(blob)
    info = {'cbs': [], 'cbBind': {}, 'textures': [], 'unsupported': []}
    for cb in p['cbs']:
        if cb['name'] or cb['params']:
            info['cbs'].append({'name': cb['name'], 'size': cb['size'], 'params': cb['params']})
    for b in p['binds']:
        if b['kind'] == 1: info['cbBind'][b['name']] = b['index']
        else: info['textures'].append({'name': b['name'], 'index': b['index'], 'sampler': b['sampler'], 'dim': b['dim'] >> 1, 'ms': b['dim'] & 1})
    return info


def merge(a, b):
    out = {'cbs': [], 'cbBind': dict(a['cbBind']), 'textures': list(a['textures']), 'unsupported': a['unsupported'] + b['unsupported']}
    byname = {}
    for cb in a['cbs'] + b['cbs']:
        if cb['name'] in byname:
            m = byname[cb['name']]
            m['size'] = max(m['size'], cb['size'])
            have = {p['name'] for p in m['params']}
            m['params'] += [p for p in cb['params'] if p['name'] not in have]
        else:
            m = {'name': cb['name'], 'size': cb['size'], 'params': list(cb['params'])}
            byname[cb['name']] = m
            out['cbs'].append(m)
    out['cbBind'].update(b['cbBind'])
    have = {t['name'] for t in out['textures']}
    out['textures'] += [t for t in b['textures'] if t['name'] not in have]
    for cb in out['cbs']:
        cb['params'].sort(key=lambda p: p['offset'])
    return out
