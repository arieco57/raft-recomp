package com.raftrecomp;

import android.os.Bundle;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;

import com.unity3d.player.UnityPlayerActivity;

// Raft's Unity.InputSystem.dll was built for Windows: it has no Android gamepad layouts, so a physical
// controller never reaches the game. Gamepad events are taken here instead and merged with the on-screen
// pad (VirtualPad); RaftRecomp.dll feeds the result to a virtual Input System Gamepad every frame.
public class RaftActivity extends UnityPlayerActivity {
    // FMOD on Android needs its Java side up before the game creates the FMOD system (audio output,
    // banks read from the APK assets: file:///android_asset)
    @Override protected void onCreate(Bundle b) {
        try { System.loadLibrary("fmod"); org.fmod.FMOD.init(this); } catch (Throwable t) { android.util.Log.e("RaftRecomp", "FMOD init", t); }
        super.onCreate(b);
    }

    // The game data is not inside this APK: the installer converts the user's own Raft copy into an APK-shaped
    // zip (assets/bin/Data/...) in the app's external files dir. libunity asks the activity for the package
    // path (getPackageCodePath / getApplicationInfo().sourceDir) and reads assets/ from there.
    static java.io.File dataFile(android.content.Context c) {
        java.io.File d = c.getExternalFilesDir(null);
        return d == null ? null : new java.io.File(d, "raft-data.zip");
    }

    String dataPath() {
        java.io.File f = dataFile(this);
        return f != null && f.isFile() ? f.getAbsolutePath() : null;
    }

    @Override public String getPackageCodePath() {
        String p = calledFromUnity() ? dataPath() : null; return p != null ? p : super.getPackageCodePath();
    }

    @Override public String getPackageResourcePath() {
        String p = calledFromUnity() ? dataPath() : null; return p != null ? p : super.getPackageResourcePath();
    }

    @Override public android.content.pm.ApplicationInfo getApplicationInfo() {
        android.content.pm.ApplicationInfo ai = super.getApplicationInfo();
        String p = dataPath();
        if (p == null || !calledFromUnity()) return ai;
        ai = new android.content.pm.ApplicationInfo(ai);
        ai.sourceDir = p; ai.publicSourceDir = p; ai.splitSourceDirs = null; ai.splitPublicSourceDirs = null;
        return ai;
    }

    // only libunity gets the redirected path: its JNI calls come from the render thread, whose Java frame right
    // below ours is com.unity3d.player.UnityPlayer.nativeRender (the framework asks too, from android.* frames)
    static boolean calledFromUnity() {
        StackTraceElement[] st = Thread.currentThread().getStackTrace();
        // [0] VMStack.getThreadStackTrace [1] Thread.getStackTrace [2] calledFromUnity [3] the override [4] caller
        return st.length > 4 && st[4].getClassName().startsWith("com.unity3d.player.UnityPlayer")
            && st[4].getMethodName().startsWith("native");
    }

    @Override protected void onDestroy() {
        super.onDestroy();
        try { org.fmod.FMOD.close(); } catch (Throwable t) { }
    }

    static boolean fromPad(int source) {
        return (source & InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD
            || (source & InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK;
    }

    static int bit(int keyCode) {
        switch (keyCode) {
            case KeyEvent.KEYCODE_DPAD_UP: return VirtualPad.UP;
            case KeyEvent.KEYCODE_DPAD_DOWN: return VirtualPad.DOWN;
            case KeyEvent.KEYCODE_DPAD_LEFT: return VirtualPad.LEFT;
            case KeyEvent.KEYCODE_DPAD_RIGHT: return VirtualPad.RIGHT;
            case KeyEvent.KEYCODE_BUTTON_START: return VirtualPad.START;
            case KeyEvent.KEYCODE_BUTTON_SELECT: return VirtualPad.BACK;
            case KeyEvent.KEYCODE_BUTTON_THUMBL: return VirtualPad.LTHUMB;
            case KeyEvent.KEYCODE_BUTTON_THUMBR: return VirtualPad.RTHUMB;
            case KeyEvent.KEYCODE_BUTTON_L1: return VirtualPad.LB;
            case KeyEvent.KEYCODE_BUTTON_R1: return VirtualPad.RB;
            case KeyEvent.KEYCODE_BUTTON_A: return VirtualPad.A;
            case KeyEvent.KEYCODE_BUTTON_B: return VirtualPad.B;
            case KeyEvent.KEYCODE_BUTTON_X: return VirtualPad.X;
            case KeyEvent.KEYCODE_BUTTON_Y: return VirtualPad.Y;
        }
        return 0;
    }

    @Override public boolean dispatchKeyEvent(KeyEvent e) {
        int src = e.getSource();
        boolean pad = fromPad(src) || ((src & InputDevice.SOURCE_DPAD) == InputDevice.SOURCE_DPAD && e.getDevice() != null && e.getDevice().isExternal());
        if (pad && e.getAction() != KeyEvent.ACTION_MULTIPLE) {
            int k = e.getKeyCode();
            boolean down = e.getAction() == KeyEvent.ACTION_DOWN;
            int b = bit(k);
            if (b != 0) { VirtualPad.physButton(b, down); return true; }
            if (k == KeyEvent.KEYCODE_BUTTON_L2) { VirtualPad.physTriggerKey(0, down); return true; }
            if (k == KeyEvent.KEYCODE_BUTTON_R2) { VirtualPad.physTriggerKey(1, down); return true; }
            if (k == KeyEvent.KEYCODE_BACK) { VirtualPad.physButton(VirtualPad.B, down); return true; }
        }
        return super.dispatchKeyEvent(e);
    }

    @Override public boolean dispatchGenericMotionEvent(MotionEvent e) {
        if (fromPad(e.getSource()) && e.getAction() == MotionEvent.ACTION_MOVE) {
            float lt = Math.max(e.getAxisValue(MotionEvent.AXIS_LTRIGGER), e.getAxisValue(MotionEvent.AXIS_BRAKE));
            float rt = Math.max(e.getAxisValue(MotionEvent.AXIS_RTRIGGER), e.getAxisValue(MotionEvent.AXIS_GAS));
            VirtualPad.physAxes(e.getAxisValue(MotionEvent.AXIS_X), -e.getAxisValue(MotionEvent.AXIS_Y),
                                e.getAxisValue(MotionEvent.AXIS_Z), -e.getAxisValue(MotionEvent.AXIS_RZ), lt, rt,
                                e.getAxisValue(MotionEvent.AXIS_HAT_X), e.getAxisValue(MotionEvent.AXIS_HAT_Y));
            return true;
        }
        return super.dispatchGenericMotionEvent(e);
    }
}
