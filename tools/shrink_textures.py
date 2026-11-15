# Raft Recomp (Android): re-encode the PC textures of an asset file for a phone GPU.
#   DXT1 -> ETC2_RGB, DXT5 -> ETC2_RGBA8, RGBA32/ARGB32 -> ETC2_RGBA8, at half resolution (the top mip
#   is dropped: a quarter of the memory and of the size, little visible loss on a phone screen).
# The original mips below the top one are decoded and re-encoded one by one (no re-filtering).
# Texture data goes inline into the .assets file (no .resS).
# usage: shrink_textures.py <in-asset> <out-asset> [min-size-to-halve=128]
import os, sys
import UnityPy, texture2ddecoder, astc_encoder
# numpy / etcpak only for the ETC2 path (PC experiments); the phone converter has neither

DXT1, DXT5, RGBA32, ARGB32 = 10, 12, 4, 5
ETC2_RGB, ETC2_RGBA8 = 45, 47
ASTC_BLOCK = int(os.environ.get('RAFT_ASTC_BLOCK', '10'))
ASTC_FMT = {4: 48, 5: 49, 6: 50, 8: 51, 10: 52, 12: 53}[ASTC_BLOCK]
USE_ASTC = os.environ.get('RAFT_TEX', 'astc8') == 'astc8'
_astc = None


def astc_ctx():
    global _astc
    if _astc is None:
        cfg = astc_encoder.ASTCConfig(astc_encoder.ASTCProfile.LDR, ASTC_BLOCK, ASTC_BLOCK, block_z=1, quality=float(os.environ.get('RAFT_ASTC_Q', '10')),
                                      flags=astc_encoder.ASTCConfigFlags.USE_DECODE_UNORM8)
        _astc = astc_encoder.ASTCContext(cfg)
    return _astc


def mip_sizes(fmt, w, h, n):
    out = []
    for _ in range(n):
        if fmt in (DXT1, DXT5):
            out.append(((w + 3) // 4) * ((h + 3) // 4) * (8 if fmt == DXT1 else 16))
        else:
            out.append(w * h * 4)
        w, h = max(1, w // 2), max(1, h // 2)
    return out


def decode(fmt, data, w, h):
    """-> RGBA8 pixels (bytes, w*h*4)"""
    if fmt == RGBA32: return bytes(data[:w * h * 4])
    if fmt == ARGB32:
        src = data[:w * h * 4]; out = bytearray(len(src))
        out[0::4], out[1::4], out[2::4], out[3::4] = src[1::4], src[2::4], src[3::4], src[0::4]
        return bytes(out)
    raw = texture2ddecoder.decode_bc1(data, w, h) if fmt == DXT1 else texture2ddecoder.decode_bc3(data, w, h)
    out = bytearray(raw)                               # BGRA -> RGBA
    out[0::4], out[2::4] = raw[2::4], raw[0::4]
    return bytes(out)


def encode(px, w, h, rgba):
    if USE_ASTC:
        im = astc_encoder.ASTCImage(astc_encoder.ASTCType.U8, w, h, 1, px)
        return astc_ctx().compress(im, astc_encoder.ASTCSwizzle.from_str('RGBA'))
    import numpy as np, etcpak
    img = np.frombuffer(px, np.uint8).reshape(h, w, 4)
    pw, ph = (w + 3) // 4 * 4, (h + 3) // 4 * 4        # ETC works on 4x4 blocks: pad by replicating the edge
    if (pw, ph) != (w, h):
        img = np.pad(img, ((0, ph - h), (0, pw - w), (0, 0)), mode='edge')
    data = np.ascontiguousarray(img).tobytes()
    return etcpak.compress_etc2_rgba(data, pw, ph) if rgba else etcpak.compress_etc2_rgb(data, pw, ph)


def shrink(obj, d, min_size):
    if os.environ.get("RAFT_TRACE"): print("tex", obj.path_id, d["m_Name"], d["m_TextureFormat"], d["m_Width"], d["m_Height"], d["m_MipCount"], flush=True)
    fmt, w, h, n = d['m_TextureFormat'], d['m_Width'], d['m_Height'], max(1, d['m_MipCount'])
    if fmt not in (DXT1, DXT5, RGBA32, ARGB32) or w <= 0 or h <= 0: return False
    # small uncompressed ones stay as they are: cursors must be RGBA32 (Cursor.SetCursor), UI icons
    if fmt in (RGBA32, ARGB32) and max(w, h) <= 128: return False
    tex = obj.read()
    data = bytes(tex.get_image_data()) if tex.m_StreamData and tex.m_StreamData.size else bytes(tex.image_data)
    sizes = mip_sizes(fmt, w, h, n)
    if len(data) < sum(sizes): return False
    # half resolution; a quarter from RAFT_DROP2 (default 2048) up, an eighth from RAFT_DROP3 (default never).
    # The phone build is a 32-bit process: texture memory decides whether a world fits.
    big2, big3 = int(os.environ.get('RAFT_DROP2', '2048')), int(os.environ.get('RAFT_DROP3', '1000000'))
    m = min(w, h)
    drop = 0 if (n <= 1 or m < min_size) else (3 if (m >= big3 and n > 3) else 2 if (m >= big2 and n > 2) else 1)
    rgba = fmt != DXT1
    out, off, mw, mh = [], 0, w, h
    for i in range(n):
        if i >= drop:
            out.append(encode(decode(fmt, data[off:off + sizes[i]], mw, mh), mw, mh, rgba))
        off += sizes[i]
        mw, mh = max(1, mw // 2), max(1, mh // 2)
    blob = b''.join(out)
    d['m_Width'], d['m_Height'] = (w >> drop) or 1, (h >> drop) or 1
    d['m_MipCount'] = n - drop
    d['m_TextureFormat'] = ASTC_FMT if USE_ASTC else (ETC2_RGBA8 if rgba else ETC2_RGB)
    d['m_CompleteImageSize'] = len(blob)
    d['image data'] = blob
    d['m_StreamData'] = {'offset': 0, 'size': 0, 'path': ''}
    obj.save_typetree(d)
    return True


def main():
    src, dst = sys.argv[1], sys.argv[2]
    min_size = int(sys.argv[3]) if len(sys.argv) > 3 else 128
    if '/steamapps/' in os.path.realpath(os.path.dirname(os.path.abspath(dst))): sys.exit('refusing to write into Steam')
    env = UnityPy.load(src)
    done = skipped = 0
    for obj in env.objects:
        if obj.type.name != 'Texture2D': continue
        try:
            if shrink(obj, obj.read_typetree(), min_size): done += 1
            else: skipped += 1
        except Exception as ex:
            skipped += 1
            print('  skip', obj.path_id, ex)
    if done:
        data = env.file.save()
        with open(dst + '.tmp', 'wb') as f: f.write(data)
        os.replace(dst + '.tmp', dst)
    print(f'{os.path.basename(src)}: {done} re-encoded, {skipped} left as is')


if __name__ == '__main__':
    main()
