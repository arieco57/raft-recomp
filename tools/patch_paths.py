# Raft builds its save paths with Windows separators ("\User\User_<id>\World\..."). On Linux those
# become single file names with backslashes in them. Patch the path literals in the #US heap
# (UTF-16, same length) so they use '/'.
import sys, os, shutil
src, dst = sys.argv[1], sys.argv[2]
if '/steamapps/' in os.path.realpath(os.path.dirname(os.path.abspath(dst))): sys.exit('refusing to write into Steam')
LITERALS = ['\\', '-Latest\\', '\\Player\\', 'Player\\', '/Resources\\', '\\Resources/', '\\Resources\\', 'Root\\', '\\User', '\\User\\User_', 'World\\']
data = bytearray(open(src, 'rb').read())
for s in LITERALS:
    enc = s.encode('utf-16-le')
    pat = bytes([len(enc) + 1]) + enc            # #US entry: length prefix, UTF-16 text, terminal flag byte
    n, pos = 0, 0
    while True:
        i = data.find(pat, pos)
        if i < 0: break
        if data[i + len(pat)] in (0, 1):
            data[i + 1:i + 1 + len(enc)] = s.replace('\\', '/').encode('utf-16-le')
            n += 1
        pos = i + 1
    print(f'{s!r:22} -> {s.replace(chr(92), "/")!r:22} {n}x')
tmp = dst + '.tmp'
open(tmp, 'wb').write(data)
os.replace(tmp, dst)
