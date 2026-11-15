import sys, json, UnityPy, lz4.block
src, want, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
env = UnityPy.load(src)
for obj in env.objects:
    if obj.type.name != "Shader": continue
    d = obj.read_typetree()
    if d["m_ParsedForm"]["m_Name"] != want: continue
    blob = bytes(d["compressedBlob"])
    for i, p in enumerate(d["platforms"]):
        for j in range(len(d["offsets"][i])):
            off, cl, dl = d["offsets"][i][j], d["compressedLengths"][i][j], d["decompressedLengths"][i][j]
            raw = lz4.block.decompress(blob[off:off+cl], uncompressed_size=dl)
            open(f"{outdir}/p{p}_{j}.bin", "wb").write(raw)
    d["compressedBlob"] = f"<{len(blob)} bytes>"
    json.dump(d, open(f"{outdir}/shader.json", "w"), indent=1, default=str)
    print("platforms", d["platforms"], "offsets", d["offsets"], "dl", d["decompressedLengths"])
