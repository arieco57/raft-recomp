# set the build target of Unity serialized files (header: ... unity version "2021.3.45f2\0", then int32 platform)
# usage: set_platform.py <platform> <file>...      (13 = Android)
import sys, struct, os
plat = int(sys.argv[1])
for f in sys.argv[2:]:
    if '/steamapps/' in os.path.realpath(f): sys.exit('refusing to write into Steam')
    with open(f, 'r+b') as fh:
        head = fh.read(256)
        i = head.find(b'2021.3.45f2\x00')
        if i < 0: print('skip (no header)', f); continue
        old = struct.unpack_from('<i', head, i + 12)[0]
        fh.seek(i + 12); fh.write(struct.pack('<i', plat))
        print(f'{os.path.basename(f)}: {old} -> {plat}')
