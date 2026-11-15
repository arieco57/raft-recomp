import sys, UnityPy
env = UnityPy.load(sys.argv[1])
for obj in env.objects:
    if obj.type.name != "Shader": continue
    try:
        d = obj.read_typetree()
        name = d.get("m_ParsedForm", {}).get("m_Name", "?")
        plats = d.get("platforms", [])
        print(obj.path_id, name, plats)
    except Exception as e:
        print(obj.path_id, "ERR", e)
