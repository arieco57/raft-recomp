// smolv_tool: encode <in.spv> <out.smolv> | decode <in.smolv> <out.spv>
#include "smolv.h"
#include <cstdio>
#include <fstream>
#include <vector>
#include <cstring>
static std::vector<char> Read(const char* p) { std::ifstream f(p, std::ios::binary); return std::vector<char>((std::istreambuf_iterator<char>(f)), {}); }
int main(int argc, char** argv)
{
    if (argc < 4) { fprintf(stderr, "usage: smolv_tool encode|decode in out\n"); return 2; }
    std::vector<char> in = Read(argv[2]);
    if (!strcmp(argv[1], "decode"))
    {
        size_t n = smolv::GetDecodedBufferSize(in.data(), in.size());
        std::vector<char> out(n);
        if (!n || !smolv::Decode(in.data(), in.size(), out.data(), n)) { fprintf(stderr, "decode failed\n"); return 1; }
        std::ofstream(argv[3], std::ios::binary).write(out.data(), n);
    }
    else
    {
        smolv::ByteArray out;
        if (!smolv::Encode(in.data(), in.size(), out, smolv::kEncodeFlagStripDebugInfo)) { fprintf(stderr, "encode failed\n"); return 1; }
        std::ofstream(argv[3], std::ios::binary).write((const char*)out.data(), out.size());
    }
    return 0;
}
