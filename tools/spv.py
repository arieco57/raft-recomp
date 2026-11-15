# minimal SPIR-V inspector: names, locations, descriptor sets/bindings, storage classes
import struct, sys
SC = {0: 'UniformConstant', 1: 'Input', 2: 'Uniform', 3: 'Output', 6: 'Private', 7: 'Function', 9: 'PushConstant', 12: 'StorageBuffer'}
def parse(b):
    w = struct.unpack('<%dI' % (len(b) // 4), b)
    assert w[0] == 0x07230203, 'not SPIR-V'
    names, decos, vars_, i = {}, {}, [], 5
    exec_model = None
    while i < len(w):
        op, n = w[i] & 0xFFFF, w[i] >> 16
        a = w[i + 1:i + n]
        if op == 5: names[a[0]] = bytes(struct.pack('<%dI' % (n - 2), *a[1:])).split(b'\0')[0].decode()
        elif op == 71: decos.setdefault(a[0], {})[a[1]] = a[2] if len(a) > 2 else True
        elif op == 59: vars_.append((a[1], a[2], a[0]))
        elif op == 15: exec_model = a[0]
        i += n
    return {'names': names, 'decos': decos, 'vars': vars_, 'model': exec_model, 'version': w[1]}
def summary(b):
    p = parse(b)
    out = [f"model {p['model']} version {p['version']:#x}"]
    for vid, sc, tid in p['vars']:
        if sc in (7,): continue
        d = p['decos'].get(vid, {})
        nm = p['names'].get(vid, '') or p['names'].get(tid, '')
        out.append(f"  {SC.get(sc, sc):15} {nm:30} loc={d.get(30)} set={d.get(34)} bind={d.get(33)} builtin={d.get(11)}")
    return '\n'.join(out)
if __name__ == '__main__':
    print(summary(open(sys.argv[1], 'rb').read()))

OPN = {5:'Name',6:'MemberName',15:'EntryPoint',16:'ExecutionMode',17:'Capability',43:'Constant',50:'SpecConstant',51:'SpecConstantComposite',
       52:'SpecConstantOp',44:'ConstantComposite',59:'Variable',61:'Load',62:'Store',65:'AccessChain',71:'Decorate',72:'MemberDecorate',
       79:'VectorShuffle',80:'CompositeConstruct',81:'CompositeExtract',82:'CompositeInsert',127:'FNegate',129:'FAdd',131:'FSub',133:'FMul',
       142:'VectorTimesScalar',148:'Dot',12:'ExtInst',247:'SelectionMerge',250:'BranchConditional',249:'Branch',248:'Label',54:'Function',253:'Return',
       141:'FMod',170:'Select'}
def ops(b):
    w = struct.unpack('<%dI' % (len(b) // 4), b); i = 5; out = []
    while i < len(w):
        op, n = w[i] & 0xFFFF, w[i] >> 16
        out.append((OPN.get(op, op), w[i + 1:i + n])); i += n
    return out
