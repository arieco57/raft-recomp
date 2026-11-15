// Raft Recomp (Android): IL fixes applied to a copy of Assembly-CSharp.dll for the phone build only.
//  - UltimateWater's GPU FFT: the compute path (Dx11FFT) needs R16F/RG16F random-write textures, which
//    OpenGL ES does not have (RenderTexture.Create fails every frame). WavesRendererFFT's
//    "SystemInfo.supportsComputeShaders" checks become false, so it takes its pixel-shader FFT instead.
// usage: mono AndroidIlPatch.exe <in Assembly-CSharp.dll> <out dll> <managed dir for references>
using System;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;

static class AndroidIlPatch
{
    static int Main(string[] a)
    {
        var res = new DefaultAssemblyResolver();
        res.AddSearchDirectory(a[2]);
        var asm = AssemblyDefinition.ReadAssembly(a[0], new ReaderParameters { AssemblyResolver = res });
        int n = 0;
        // FMODUnity: the banks are not in the APK (the game data comes from the user's copy), the installer
        // copies them to <persistentDataPath>/banks; PlatformAndroid.StaticGetBankFolder returns that folder
        foreach (var t in asm.MainModule.GetTypes().Where(t => t.FullName == "FMODUnity.PlatformAndroid"))
            foreach (var m in t.Methods.Where(m => m.Name == "StaticGetBankFolder"))
            {
                var core = res.Resolve(AssemblyNameReference.Parse("UnityEngine.CoreModule")).MainModule;
                var pdp = new MethodReference("get_persistentDataPath", asm.MainModule.TypeSystem.String,
                    asm.MainModule.ImportReference(core.GetType("UnityEngine.Application")));
                // reuse the module's own String.Concat(string, string) reference (it targets netstandard)
                var concat = asm.MainModule.GetTypes().SelectMany(x => x.Methods).Where(x => x.HasBody)
                    .SelectMany(x => x.Body.Instructions).Select(x => x.Operand as MethodReference)
                    .First(x => x != null && x.DeclaringType.FullName == "System.String" && x.Name == "Concat" &&
                                x.Parameters.Count == 2 && x.Parameters.All(p => p.ParameterType.FullName == "System.String"));
                var il = m.Body.GetILProcessor();
                m.Body.Instructions.Clear(); m.Body.ExceptionHandlers.Clear(); m.Body.Variables.Clear();
                il.Append(il.Create(OpCodes.Call, pdp));
                il.Append(il.Create(OpCodes.Ldstr, "/banks"));
                il.Append(il.Create(OpCodes.Call, concat));
                il.Append(il.Create(OpCodes.Ret));
                n++; Console.WriteLine("  PlatformAndroid.StaticGetBankFolder -> persistentDataPath + /banks");
            }
        foreach (var t in asm.MainModule.GetTypes().Where(t => t.FullName == "UltimateWater.WavesRendererFFT"))
            foreach (var m in t.Methods.Where(m => m.HasBody))
                foreach (var ins in m.Body.Instructions)
                    if (ins.OpCode == OpCodes.Call && ins.Operand is MethodReference mr &&
                        mr.DeclaringType.FullName == "UnityEngine.SystemInfo" && mr.Name == "get_supportsComputeShaders")
                    {
                        ins.OpCode = OpCodes.Ldc_I4_0; ins.Operand = null; n++;
                        Console.WriteLine("  " + t.Name + "." + m.Name + ": supportsComputeShaders -> false");
                    }
        if (n == 0) { Console.Error.WriteLine("nothing patched"); return 1; }
        asm.Write(a[1]);
        return 0;
    }
}
