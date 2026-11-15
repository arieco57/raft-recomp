package com.raftrecomp;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

// Runs the converter (android_convert.py on the bundled Python) as a foreground service, so it keeps going
// with the screen off or the installer in the background. InstallerActivity polls the static state.
public class ConvertService extends Service {
    public static volatile boolean running, finished, failed;
    public static volatile int percent;
    public static volatile String status = "";
    static final String CHANNEL = "raft_convert";

    public static void start(Context c, String raftDir) {
        Intent i = new Intent(c, ConvertService.class).putExtra("dir", raftDir);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i); else c.startService(i);
    }

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent intent, int flags, int id) {
        if (running) return START_NOT_STICKY;
        running = true; finished = false; failed = false; percent = 0; status = "Preparando";
        startForeground(1, note("Preparando"));
        final String dir = intent.getStringExtra("dir");
        new Thread(new Runnable() { public void run() { work(dir); } }, "raft-convert").start();
        return START_NOT_STICKY;
    }

    Notification note(String text) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        Notification.Builder b;
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Instalación", NotificationManager.IMPORTANCE_LOW));
            b = new Notification.Builder(this, CHANNEL);
        } else b = new Notification.Builder(this);
        PendingIntent pi = PendingIntent.getActivity(this, 0, new Intent(this, InstallerActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return b.setContentTitle("Raft Recomp: convirtiendo tu copia de Raft").setContentText(text)
                .setSmallIcon(android.R.drawable.stat_sys_download).setContentIntent(pi).setOngoing(true)
                .setProgress(100, percent, false).build();
    }

    void update(String text) {
        status = text;
        ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).notify(1, note(text));
    }

    void work(String dir) {
        PowerManager.WakeLock wl = ((PowerManager) getSystemService(POWER_SERVICE))
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "raftrecomp:convert");
        wl.acquire(4 * 3600 * 1000L);
        try {
            File py = unpackPython();
            String lib = getApplicationInfo().nativeLibraryDir;
            File ext = getExternalFilesDir(null);
            File work = new File(ext, "work");
            String conv = new File(py, "raft/android_convert.py").getPath(), zip = new File(ext, "raft-data.zip").getPath();
            // dir == null: new APK build over existing data, only its code parts are replaced (no Raft copy needed)
            ProcessBuilder pb = dir == null
                ? new ProcessBuilder(lib + "/libpyexec.so", "-u", "-s", conv, "--refresh", zip, getPackageCodePath())
                : new ProcessBuilder(lib + "/libpyexec.so", "-u", "-s", conv, dir, zip, new File(ext, "banks").getPath(), getPackageCodePath(), work.getPath());
            Map<String, String> env = pb.environment();
            env.put("PYTHONHOME", py.getPath());
            env.put("LD_LIBRARY_PATH", lib);
            env.put("RAFT_PY", lib + "/libpyexec.so");
            env.put("RAFT_PYFLAGS", "-s");   // not -I: isolated mode ignores PYTHONHOME
            env.put("RAFT_JOBS", String.valueOf(Math.max(2, Math.min(4, Runtime.getRuntime().availableProcessors() / 2))));
            env.put("RAFT_DXBC2GLSL", lib + "/libdxbc2glsl.so");
            env.put("TMPDIR", getCacheDir().getPath());
            env.put("HOME", getFilesDir().getPath());
            pb.redirectErrorStream(true);
            Process p = pb.start();
            File log = new File(ext, "conversion.log");
            String err = "", tail = "";
            try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()));
                 FileOutputStream lf = new FileOutputStream(log)) {
                String line;
                while ((line = r.readLine()) != null) {
                    lf.write((line + "\n").getBytes()); lf.flush();
                    android.util.Log.i("RaftConvert", line);
                    if (line.startsWith("PROGRESS ")) {
                        String[] s = line.split(" ", 3);
                        try { percent = Integer.parseInt(s[1]); } catch (NumberFormatException e) { }
                        update(s.length > 2 ? s[2] : "");
                    } else if (line.startsWith("ERROR ")) {
                        err = line.substring(6);
                    } else if (!line.trim().isEmpty()) {
                        tail = line;
                    }
                }
            }
            int rc = p.waitFor();
            if (rc == 0 && new File(ext, "raft-data.zip").isFile()) {
                InstallerActivity.markInstalled(this);
                finished = true; status = "Listo";
            } else {
                failed = true; status = "Falló la conversión: " + (err.isEmpty() ? tail : err) + " (código " + rc + ", ver Android/data/" + getPackageName() + "/files/conversion.log)";
            }
        } catch (Throwable t) {
            failed = true; status = "Falló la conversión: " + t;
            android.util.Log.e("RaftConvert", "convert", t);
        } finally {
            running = false;
            wl.release();
            stopForeground(true);
            stopSelf();
        }
    }

    // PYTHONHOME (stdlib, packages, the converter) from the APK's assets/python.zip, once per APK version
    File unpackPython() throws Exception {
        File py = new File(getFilesDir(), "py");
        File stamp = new File(py, ".stamp");
        String want = InstallerActivity.apkStamp(this);
        if (stamp.isFile() && want.equals(new String(java.nio.file.Files.readAllBytes(stamp.toPath())))) return py;
        update("Preparando el convertidor");
        deleteTree(py); py.mkdirs();
        try (InputStream in = getAssets().open("python.zip"); ZipInputStream z = new ZipInputStream(in)) {
            ZipEntry e; byte[] buf = new byte[1 << 16];
            while ((e = z.getNextEntry()) != null) {
                File f = new File(py, e.getName());
                if (!f.getCanonicalPath().startsWith(py.getCanonicalPath())) continue;
                if (e.isDirectory()) { f.mkdirs(); continue; }
                f.getParentFile().mkdirs();
                try (FileOutputStream o = new FileOutputStream(f)) { int n; while ((n = z.read(buf)) > 0) o.write(buf, 0, n); }
            }
        }
        try (FileOutputStream o = new FileOutputStream(stamp)) { o.write(want.getBytes()); }
        return py;
    }

    static void deleteTree(File f) {
        File[] l = f.listFiles();
        if (l != null) for (File c : l) deleteTree(c);
        f.delete();
    }
}
