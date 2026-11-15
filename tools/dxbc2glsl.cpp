// dxbc2glsl: batch DXBC (vertex + fragment) -> one Unity-style GLSL 150 program, with the
// reflection HLSLcc reports for both stages (what Unity stores as the program's parameters).
//
// usage: dxbc2glsl <jobs.txt>
//   every line of jobs.txt: <vs.dxbc> <ps.dxbc> <out-prefix>
//   writes <out-prefix>.glsl and <out-prefix>.refl, or <out-prefix>.err on failure.
// .refl lines (stage = V or F):
//   CB <stage> <name> <size>
//   C <stage> <cbname> <name> <offset> <type> <rows> <cols> <isMatrix> <arraySize>
//   T <stage> <name> <unit> <sampler> <multisampled> <dim> <isUAV>
//   B <stage> <name> <index> <isUAV>
#include "hlslcc.h"

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

bool g_es = false;   // OpenGL ES 3 (Android)
namespace
{
std::vector<char> ReadFile(const std::string& path)
{
    std::ifstream f(path, std::ios::binary);
    return std::vector<char>((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

struct Refl : HLSLccReflection
{
    std::ostringstream out, diag;
    char stage = 'V';
    std::string curCB;
    void OnDiagnostics(const std::string& error, int line, bool isError) override
    {
        diag << (isError ? "error " : "warn ") << line << ": " << error << "\n";
    }
    bool OnConstantBuffer(const std::string& name, size_t size, size_t) override
    {
        curCB = name;
        out << "CB " << stage << " " << (name.empty() ? std::string("-") : name) << " " << size << "\n";
        return true;
    }
    bool OnConstant(const std::string& name, int bindIndex, SHADER_VARIABLE_TYPE t, int rows, int cols, bool isMatrix, int arraySize, bool) override
    {
        out << "C " << stage << " " << (curCB.empty() ? std::string("-") : curCB) << " " << name << " " << bindIndex << " " << int(t) << " " << rows << " " << cols << " " << int(isMatrix) << " " << arraySize << "\n";
        return true;
    }
    void OnTextureBinding(const std::string& name, int bindIndex, int samplerIndex, bool ms, HLSLCC_TEX_DIMENSION dim, bool isUAV) override
    {
        out << "T " << stage << " " << name << " " << bindIndex << " " << samplerIndex << " " << int(ms) << " " << int(dim) << " " << int(isUAV) << "\n";
    }
    void OnBufferBinding(const std::string& name, int bindIndex, bool isUAV) override
    {
        out << "B " << stage << " " << name << " " << bindIndex << " " << int(isUAV) << "\n";
    }
};

bool TranslateCompute(const std::vector<char>& code, Refl& refl, std::string& glsl)
{
    // Unity's GLCore compute path: GLSL 430, constant buffers as uniform blocks ($Globals -> CGlobals)
    unsigned flags = HLSLCC_FLAG_UNIFORM_BUFFER_OBJECT | HLSLCC_FLAG_TRANSLATE_MATRICES | HLSLCC_FLAG_DISABLE_GLOBALS_STRUCT |
                     HLSLCC_FLAG_FORCE_EXPLICIT_LOCATIONS;
    GlExtensions ext{};
    ext.ARB_shading_language_420pack = g_es ? 0 : 1;   // desktop-only extension
    HLSLccSamplerPrecisionInfo prec;
    GLSLCrossDependencyData deps;
    deps.SetupGLSLResourceBindingSlotsIndices();
    GLSLShader result;
    if (!TranslateHLSLFromMem(code.data(), flags, g_es ? LANG_ES_310 : LANG_430, &ext, &deps, prec, refl, &result)) return false;
    glsl = result.sourceCode;
    if (g_es) {
        // GLSL ES has no default precision for images / 3D samplers: declare them all after #version
        size_t nl = glsl.find('\n');
        glsl.insert(nl + 1, "precision highp float;\nprecision highp int;\nprecision highp image2D;\nprecision highp image3D;\n"
                            "precision highp iimage2D;\nprecision highp uimage2D;\nprecision highp image2DArray;\n"
                            "precision highp sampler3D;\nprecision highp sampler2DArray;\n");
    }
    return true;
}

bool g_vulkan = false;


bool TranslateComputeVulkan(const std::vector<char>& code, Refl& refl, std::string& glsl)
{
    unsigned flags = HLSLCC_FLAG_VULKAN_BINDINGS | HLSLCC_FLAG_UNIFORM_BUFFER_OBJECT | HLSLCC_FLAG_TRANSLATE_MATRICES |
                     HLSLCC_FLAG_DISABLE_GLOBALS_STRUCT;
    GlExtensions ext{};
    ext.ARB_shading_language_420pack = 1;
    HLSLccSamplerPrecisionInfo prec;
    GLSLCrossDependencyData deps;
    GLSLShader result;
    if (!TranslateHLSLFromMem(code.data(), flags, LANG_440, &ext, &deps, prec, refl, &result)) return false;
    glsl = result.sourceCode;
    return true;
}

bool TranslateVulkan(const std::vector<char>& code, Refl& refl, GLSLCrossDependencyData& deps, std::string& glsl)
{
    // Unity's Vulkan path: (set, binding) layouts, every constant buffer a std140 uniform block
    unsigned flags = HLSLCC_FLAG_VULKAN_BINDINGS | HLSLCC_FLAG_UNIFORM_BUFFER_OBJECT | HLSLCC_FLAG_INOUT_SEMANTIC_NAMES |
                     HLSLCC_FLAG_TRANSLATE_MATRICES | HLSLCC_FLAG_DISABLE_GLOBALS_STRUCT | HLSLCC_FLAG_COMBINE_TEXTURE_SAMPLERS * 0;
    GlExtensions ext{};
    ext.ARB_explicit_attrib_location = 1;
    ext.ARB_explicit_uniform_location = 1;
    ext.ARB_shading_language_420pack = 1;
    HLSLccSamplerPrecisionInfo prec;
    GLSLShader result;
    if (!TranslateHLSLFromMem(code.data(), flags, LANG_440, &ext, &deps, prec, refl, &result)) return false;
    glsl = result.sourceCode;
    return true;
}

bool Translate(const std::vector<char>& code, Refl& refl, GLSLCrossDependencyData& deps, std::string& glsl)
{
    if (g_vulkan) return TranslateVulkan(code, refl, deps, glsl);
    // same flags as Unity's GLCore path: semantic names (in_POSITION0 / vs_TEXCOORD0), matrices as
    // vec4 arrays (hlslcc_mtx4x4*), every constant buffer as plain uniforms
    unsigned flags = HLSLCC_FLAG_INOUT_SEMANTIC_NAMES | HLSLCC_FLAG_TRANSLATE_MATRICES | HLSLCC_FLAG_DISABLE_GLOBALS_STRUCT |
                     HLSLCC_FLAG_GLOBAL_CONSTS_NEVER_IN_UBO | HLSLCC_FLAG_FORCE_EXPLICIT_LOCATIONS;
    GlExtensions ext{};
    ext.ARB_explicit_attrib_location = g_es ? 0 : 1;   // core in GLES 3
    HLSLccSamplerPrecisionInfo prec;
    GLSLShader result;
    if (!TranslateHLSLFromMem(code.data(), flags, g_es ? LANG_ES_300 : LANG_150, &ext, &deps, prec, refl, &result)) return false;
    glsl = result.sourceCode;
    return true;
}
}

int main(int argc, char** argv)
{
    if (argc < 2) { fprintf(stderr, "usage: dxbc2glsl <jobs.txt>\n"); return 2; }
    if (std::string(argv[1]) == "--vulkan") { g_vulkan = true; argv++; argc--; }
    if (std::string(argv[1]) == "--es") { g_es = true; argv++; argc--; }
    std::ifstream jobs(argv[1]);
    std::string vsPath, psPath, out;
    int ok = 0, bad = 0;
    while (jobs >> vsPath >> psPath >> out)
    {
        Refl refl;
        if (vsPath == "CS")   // compute kernel: "CS <cs.dxbc> <out>"
        {
            std::string cs;
            refl.stage = 'C';
            if (!(g_vulkan ? TranslateComputeVulkan(ReadFile(psPath), refl, cs) : TranslateCompute(ReadFile(psPath), refl, cs))) { std::ofstream(out + ".err") << refl.diag.str(); bad++; continue; }
            std::ofstream(out + ".glsl", std::ios::binary) << cs;
            std::ofstream(out + ".refl") << refl.out.str();
            ok++;
            continue;
        }
        GLSLCrossDependencyData deps;
        std::string vs, ps;
        refl.stage = 'V';
        bool good = Translate(ReadFile(vsPath), refl, deps, vs);
        refl.stage = 'F';
        good = good && Translate(ReadFile(psPath), refl, deps, ps);
        if (!good)
        {
            std::ofstream(out + ".err") << refl.diag.str();
            bad++;
            continue;
        }
        if (g_vulkan)
        {
            std::ofstream(out + ".vert", std::ios::binary) << vs;
            std::ofstream(out + ".frag", std::ios::binary) << ps;
        }
        else
            std::ofstream(out + ".glsl", std::ios::binary) << "#ifdef VERTEX\n" << vs << "\n#endif\n#ifdef FRAGMENT\n" << ps << "\n#endif\n";
        std::ofstream(out + ".refl") << refl.out.str();
        if (!refl.diag.str().empty()) std::ofstream(out + ".diag") << refl.diag.str();
        ok++;
    }
    printf("ok %d failed %d\n", ok, bad);
    return bad ? 1 : 0;
}
