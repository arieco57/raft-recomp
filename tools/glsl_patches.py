# Per-shader GLSL fixes for D3D-baked conventions that HLSLcc cannot know about.
# {shader name: [(regex, replacement), ...]}, applied to every GLSL program of the shader.

# UltimateWater draws its full-screen passes with raw clip-space positions on Quads.TriangleMeshInversedY,
# a mesh flipped for D3D render targets (row 0 at the top). On GL that writes the image upside down
# (e.g. the copied _CameraDepthTexture, which then puts the fog's horizon in the wrong place: the cyan band).
# Negating y gives the D3D result.
RAW_CLIP_FLIP = [
    (r'gl_Position\.xyz = in_POSITION0\.xyz;', r'gl_Position.xyz = in_POSITION0.xyz * vec3(1.0, -1.0, 1.0);'),
    (r'gl_Position = in_POSITION0;', r'gl_Position = in_POSITION0 * vec4(1.0, -1.0, 1.0, 1.0);'),
]

PATCHES = {
    'UltimateWater/Depth/Depth Copy': RAW_CLIP_FLIP,
    'UltimateWater/Underwater/Base IME': RAW_CLIP_FLIP,
}

# UltimateWater's subtractive volume mask (the raft cutting the water out) stores clip z/w and the water
# tests "back <= z <= front", which only holds with D3D's reversed Z (near = 1, far = 0, clear 0 = far).
# Store and compare everything in that convention: d3d = 0.5 - 0.5 * (GL z/w).
MASK_STORE = [(r'(SV_Target0\.[yz]) = vs_TEXCOORD0\.x / vs_TEXCOORD0\.y;', r'\1 = 0.5 - 0.5 * (vs_TEXCOORD0.x / vs_TEXCOORD0.y);')]
MASK_TEST_WATER = [(r'(u_xlat\d+(?:\.x)?) = (u_xlat\d+)\.w / \2\.z;(\s*\2\.xy = \2\.xy / \2\.zz;\s*u_xlat\d+ = texture\(_SubtractiveMask)',
                    r'\1 = 0.5 - 0.5 * (\2.w / \2.z);\3')]
# Water Depth: z/w is both the mask test value and the depth written; keep the written depth GL window depth
MASK_TEST_DEPTH = [(r'(u_xlat0\.xyz = vs_TEXCOORD0\.zxy / vs_TEXCOORD0\.www;\s*u_xlat1 = texture\(_SubtractiveMask, u_xlat0\.yz\);)',
                    r'\1 float raft_z = u_xlat0.x; u_xlat0.x = 0.5 - 0.5 * raft_z;'),
                   (r'SV_Target0 = u_xlat0\.xxxx;', r'SV_Target0 = vec4(raft_z * 0.5 + 0.5);')]
# Depth merges take the nearest depth: max() under reversed Z, min() on GL
DEPTH_MERGE = [(r'max\((u_xlat\d\.x)(xxx)?, (u_xlat\d\.x)(xxx)?\)', r'min(\1\2, \3\4)')]

PATCHES.update({
    'UltimateWater/Volumes/Front': MASK_STORE,
    'UltimateWater/Volumes/Back': MASK_STORE,
    'UltimateWater/Volumes/Front Simple': MASK_STORE,
    'UltimateWater/Variations/Water *': MASK_TEST_WATER,
    'UltimateWater/Depth/Water Depth': MASK_TEST_DEPTH,
    'UltimateWater/Depth/Depth Copy': RAW_CLIP_FLIP + DEPTH_MERGE,
})
