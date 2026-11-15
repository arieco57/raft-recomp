// Raft Recomp overlay: options menu (F1 on PC, top-right button on touch devices) with an FPS counter and,
// on Android, the on-screen controller toggle.
// Loaded by Unity itself: the assembly is listed in ScriptingAssemblies.json and Init() in
// RuntimeInitializeOnLoads.json (see tools/install_overlay.py).
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;

public class RecompOverlay : MonoBehaviour
{
    const string PrefFps = "recomp_fps_counter";
    const string PrefPad = "recomp_virtual_pad";
    const string PrefScale = "recomp_render_scale";
    static readonly int[] Scales = { 35, 50, 75, 100 };

    bool menuOpen, showFps, showPad;
    int scale;                         // Android render resolution, % of the native screen
    AndroidPad pad;
    int frames;
    float acc, fps, frameMs;
    CursorLockMode prevLock;
    bool prevVisible;
    GUIStyle boxStyle, labelStyle, titleStyle, buttonStyle, toggleStyle;

    // Android: every log message (engine errors included, e.g. shaders the GPU driver rejects) also goes to
    // <persistentDataPath>/raft.log (Android/data/com.raftrecomp.raft/files/raft.log), so a tester can send it
    // without a PC/adb. Started as early as possible (SubsystemRegistration); the previous run is kept as
    // raft.prev.log; capped at 8 MB.
    static System.IO.StreamWriter log;
    static long logBytes;
    static readonly object logLock = new object();

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    static void StartLog()
    {
        if (Application.platform != RuntimePlatform.Android || log != null) return;
        try
        {
            string dir = Application.persistentDataPath, p = System.IO.Path.Combine(dir, "raft.log");
            if (System.IO.File.Exists(p)) System.IO.File.Copy(p, System.IO.Path.Combine(dir, "raft.prev.log"), true);
            log = new System.IO.StreamWriter(p, false) { AutoFlush = true };
            log.WriteLine("Raft Recomp log " + System.DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
            log.WriteLine("device: " + SystemInfo.deviceModel + " | " + SystemInfo.operatingSystem + " | RAM " + SystemInfo.systemMemorySize + " MB");
            Application.logMessageReceivedThreaded += OnLog;
        }
        catch (System.Exception) { log = null; }
    }

    static void OnLog(string msg, string stack, LogType type)
    {
        lock (logLock)
        {
            if (log == null || logBytes > 8 << 20) return;
            string line = System.DateTime.Now.ToString("HH:mm:ss.fff") + " " + type + ": " + msg;
            if ((type == LogType.Exception || type == LogType.Assert) && !string.IsNullOrEmpty(stack)) line += "\n" + stack;
            logBytes += line.Length + 1;
            try { log.WriteLine(line); if (logBytes > 8 << 20) log.WriteLine("(log cut at 8 MB)"); } catch (System.Exception) { }
        }
    }

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Init()
    {
        var go = new GameObject("RaftRecompOverlay");
        DontDestroyOnLoad(go);
        go.AddComponent<RecompOverlay>();
    }

    void Awake()
    {
        showFps = PlayerPrefs.GetInt(PrefFps, 0) == 1;
        if (Application.platform == RuntimePlatform.Android)
        {
            // logcat: no native stack trace per message (some errors repeat every frame)
            Application.SetStackTraceLogType(LogType.Error, StackTraceLogType.None);
            Application.SetStackTraceLogType(LogType.Warning, StackTraceLogType.None);
            Application.SetStackTraceLogType(LogType.Log, StackTraceLogType.None);
            OnLog("gpu: " + SystemInfo.graphicsDeviceName + " | " + SystemInfo.graphicsDeviceVendor + " | " + SystemInfo.graphicsDeviceVersion +
                  " | shader level " + SystemInfo.graphicsShaderLevel, null, LogType.Log);
            showPad = PlayerPrefs.GetInt(PrefPad, 1) == 1;
            pad = new AndroidPad();
            pad.Show(showPad);
            scale = PlayerPrefs.GetInt(PrefScale, 50);
            ApplyScale();
        }
    }

    // The phone renders at a fraction of its native resolution (the PC shaders are heavy). Raft's own
    // graphics settings set PC resolutions when opened (stretched screen): the size is enforced every frame.
    int targetW, targetH;
    float nextFpsLog;
    static float UiScale() { return Screen.height / 1080f * (Application.platform == RuntimePlatform.Android ? 1.15f : 1f); }
    static Rect OptionsRect() { float s = UiScale(); return new Rect(Screen.width - 150 * s, 10 * s, 140 * s, 48 * s); }
    void ApplyScale()
    {
        int nw = Display.main.systemWidth, nh = Display.main.systemHeight;
        if (nw < nh) { int t = nw; nw = nh; nh = t; }        // landscape
        targetW = Mathf.Max(320, nw * scale / 100);
        targetH = Mathf.Max(180, nh * scale / 100);
        Screen.SetResolution(targetW, targetH, FullScreenMode.FullScreenWindow);
    }

    void Update()
    {
        if (pad != null)
        {
            // the Opciones button by touch: in game the cursor is locked and IMGUI ignores the taps
            for (int i = 0; i < Input.touchCount; i++)
            {
                var t = Input.GetTouch(i);
                if (t.phase == UnityEngine.TouchPhase.Began && OptionsRect().Contains(new Vector2(t.position.x, Screen.height - t.position.y))) Toggle();
            }
            if (Time.unscaledTime >= nextFpsLog) { nextFpsLog = Time.unscaledTime + 10f; Debug.Log(string.Format("[RaftRecomp] fps {0:0.0} ({1:0.0} ms)", fps, frameMs)); }
            pad.Poll();
            if (showPad && Time.frameCount % 120 == 0) pad.Show(true);
            // 32-bit process (Mono on Android is armeabi-v7a only): loading a world runs out of address space
            // with full-size textures. Skip the top mip of every texture (Raft's texture setting can't go lower).
            if (QualitySettings.masterTextureLimit < 1) QualitySettings.masterTextureLimit = 1;
            if (targetW > 0 && (Screen.width != targetW || Screen.height != targetH) && Time.frameCount % 30 == 0)
                Screen.SetResolution(targetW, targetH, FullScreenMode.FullScreenWindow);
        }
        frames++;
        acc += Time.unscaledDeltaTime;
        if (acc >= 0.5f)
        {
            fps = frames / acc;
            frameMs = acc * 1000f / frames;
            frames = 0;
            acc = 0f;
        }
        if (menuOpen)
        {
            // the game re-locks the cursor every frame while playing
            Cursor.lockState = CursorLockMode.None;
            Cursor.visible = true;
        }
    }

    void Toggle()
    {
        menuOpen = !menuOpen;
        if (menuOpen)
        {
            prevLock = Cursor.lockState;
            prevVisible = Cursor.visible;
        }
        else
        {
            Cursor.lockState = prevLock;
            Cursor.visible = prevVisible;
        }
    }

    float stylesScale;
    Texture2D bg;
    void Styles(float s)
    {
        if (boxStyle != null && Mathf.Approximately(s, stylesScale)) return;   // rebuilt when the resolution changes
        stylesScale = s;
        if (bg == null)
        {
            bg = new Texture2D(1, 1) { hideFlags = HideFlags.HideAndDontSave };   // survives UnloadUnusedAssets on scene changes
            bg.SetPixel(0, 0, new Color(0f, 0f, 0f, 0.72f));
            bg.Apply();
        }
        boxStyle = new GUIStyle(GUI.skin.box) { normal = { background = bg } };
        labelStyle = new GUIStyle(GUI.skin.label) { fontSize = Mathf.RoundToInt(20 * s), normal = { textColor = Color.white } };
        titleStyle = new GUIStyle(labelStyle) { fontSize = Mathf.RoundToInt(26 * s), fontStyle = FontStyle.Bold, alignment = TextAnchor.MiddleCenter };
        buttonStyle = new GUIStyle(GUI.skin.button) { fontSize = Mathf.RoundToInt(20 * s) };
        toggleStyle = new GUIStyle(GUI.skin.toggle) { fontSize = Mathf.RoundToInt(22 * s), normal = { textColor = Color.white }, onNormal = { textColor = Color.white } };
    }

    void OnGUI()
    {
        GUI.depth = -10000;
        float s = UiScale();   // phone: a bit bigger for fingers
        Styles(s);

        var e = Event.current;
        if (e.type == EventType.KeyDown && e.keyCode == KeyCode.F1)
        {
            Toggle();
            e.Use();
        }

        if (showFps)
        {
            var r = new Rect(10 * s, 10 * s, 210 * s, 34 * s);
            GUI.Box(r, GUIContent.none, boxStyle);
            GUI.Label(new Rect(r.x + 10 * s, r.y + 3 * s, r.width, r.height), string.Format("FPS: {0:0}  ({1:0.0} ms)", fps, frameMs), labelStyle);
        }

        if (Application.isMobilePlatform)
        {
            GUI.Box(OptionsRect(), "Opciones", buttonStyle);   // taps handled in Update
        }

        if (!menuOpen) return;
        float w = 460 * s, h = (pad != null ? 350 : 230) * s;
        var win = new Rect((Screen.width - w) / 2, (Screen.height - h) / 2, w, h);
        GUI.Box(win, GUIContent.none, boxStyle);
        GUI.Label(new Rect(win.x, win.y + 14 * s, w, 36 * s), "Raft Recomp - Opciones", titleStyle);
        bool f = GUI.Toggle(new Rect(win.x + 40 * s, win.y + 80 * s, w - 80 * s, 40 * s), showFps, "  Contador de FPS", toggleStyle);
        if (f != showFps)
        {
            showFps = f;
            PlayerPrefs.SetInt(PrefFps, f ? 1 : 0);
            PlayerPrefs.Save();
        }
        if (pad != null)
        {
            bool p = GUI.Toggle(new Rect(win.x + 40 * s, win.y + 135 * s, w - 80 * s, 40 * s), showPad, "  Mando en pantalla", toggleStyle);
            if (p != showPad)
            {
                showPad = p;
                pad.Show(p);
                PlayerPrefs.SetInt(PrefPad, p ? 1 : 0);
                PlayerPrefs.Save();
            }
            GUI.Label(new Rect(win.x + 40 * s, win.y + 190 * s, w - 80 * s, 36 * s), "Resolucion: " + scale + "%", labelStyle);
            float bw = (w - 80 * s) / Scales.Length;
            for (int i = 0; i < Scales.Length; i++)
                if (GUI.Button(new Rect(win.x + 40 * s + i * bw, win.y + 225 * s, bw - 6 * s, 44 * s), Scales[i] + "%", buttonStyle) && scale != Scales[i])
                {
                    scale = Scales[i];
                    PlayerPrefs.SetInt(PrefScale, scale);
                    PlayerPrefs.Save();
                    ApplyScale();
                }
        }
        if (GUI.Button(new Rect(win.x + (w - 200 * s) / 2, win.y + h - 66 * s, 200 * s, 46 * s), "Cerrar (F1)", buttonStyle)) Toggle();
    }
}

// Android: Raft's Unity.InputSystem.dll is the Windows build (no Android gamepad support). The Java side
// (com.raftrecomp.VirtualPad / RaftActivity) merges the on-screen pad with a physical controller; its state
// is polled every frame and fed to a virtual Input System Gamepad, which the game reads like any other.
class AndroidPad
{
    readonly AndroidJavaClass java = new AndroidJavaClass("com.raftrecomp.VirtualPad");
    Gamepad device;
    bool sentZero = true;

    // XInput bit (Java side) -> GamepadButton bit
    static readonly int[,] Map = {
        { 0x1, 0 }, { 0x2, 1 }, { 0x4, 2 }, { 0x8, 3 },            // d-pad up/down/left/right
        { 0x8000, 4 }, { 0x2000, 5 }, { 0x1000, 6 }, { 0x4000, 7 }, // Y north, B east, A south, X west
        { 0x40, 8 }, { 0x80, 9 }, { 0x100, 10 }, { 0x200, 11 },     // L3, R3, LB, RB
        { 0x10, 12 }, { 0x20, 13 },                                 // start, back/select
    };

    public void Show(bool on) { java.CallStatic("show", on); }

    public void Poll()
    {
        float[] v = java.CallStatic<float[]>("state");
        if (v == null || v.Length < 7) return;
        int xb = (int)v[0];
        uint buttons = 0;
        for (int i = 0; i < Map.GetLength(0); i++)
            if ((xb & Map[i, 0]) != 0) buttons |= 1u << Map[i, 1];
        var st = new GamepadState { buttons = buttons, leftStick = new Vector2(v[1], v[2]), rightStick = new Vector2(v[3], v[4]),
                                    leftTrigger = v[5], rightTrigger = v[6] };
        bool zero = buttons == 0 && st.leftStick == Vector2.zero && st.rightStick == Vector2.zero && st.leftTrigger == 0 && st.rightTrigger == 0;
        if (zero && sentZero) return;
        if (device == null) device = InputSystem.AddDevice<Gamepad>("RaftRecomp Pad");
        if (!zero || !sentZero) InputSystem.QueueStateEvent(device, st);
        sentZero = zero;
    }
}
