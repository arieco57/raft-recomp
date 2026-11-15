package com.raftrecomp;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.DocumentsContract;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.file.Files;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

// First screen of the APK. The APK has no game data: this asks for the folder with the user's own Raft copy
// (the "Raft" folder of the PC/Steam game copied to the phone) and converts it on the phone (ConvertService).
// Once the data zip is there it goes straight to the game (RaftActivity).
public class InstallerActivity extends Activity {
    static final int PICK = 1;
    TextView title, body, status;
    Button pick, play;
    ProgressBar bar;
    String chosen;
    boolean refreshing;
    final Handler h = new Handler(Looper.getMainLooper());

    // the data zip carries the IL2CPP metadata of the APK it was made with: a new APK build needs new data
    static String dataStamp(Context c) {
        try (ZipFile z = new ZipFile(c.getPackageCodePath())) {
            ZipEntry e = z.getEntry("assets/bin/Data/Managed/Metadata/global-metadata.dat");
            return "v1:" + (e == null ? 0 : e.getCrc());
        } catch (Exception e) { return "v1:?"; }
    }

    static String apkStamp(Context c) {
        try {
            PackageInfo pi = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
            return pi.versionCode + ":" + pi.lastUpdateTime;
        } catch (Exception e) { return "?"; }
    }

    static File stampFile(Context c) { return new File(c.getExternalFilesDir(null), "installed.stamp"); }

    static void markInstalled(Context c) {
        try (FileOutputStream o = new FileOutputStream(stampFile(c))) { o.write(dataStamp(c).getBytes()); } catch (Exception e) { }
    }

    static boolean installed(Context c) {
        try {
            File z = new File(c.getExternalFilesDir(null), "raft-data.zip");
            return z.isFile() && dataStamp(c).equals(new String(Files.readAllBytes(stampFile(c).toPath())));
        } catch (Exception e) { return false; }
    }

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        if (installed(this) && !ConvertService.running) { startGame(); return; }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        buildUi();
        // testing over adb: am start -n com.raftrecomp.raft/com.raftrecomp.InstallerActivity --es dir /sdcard/Download/Raft
        String d = getIntent().getStringExtra("dir");
        if (d != null && !ConvertService.running && canReadFiles() && looksLikeRaft(new File(d))) ConvertService.start(this, d);
        // APK updated, data already converted: refresh the code parts of the data zip (seconds, no folder needed)
        else if (!ConvertService.running && !ConvertService.failed && new File(getExternalFilesDir(null), "raft-data.zip").isFile()) {
            body.setText("Actualizando los datos para esta versión del APK...");
            pick.setVisibility(View.GONE);
            refreshing = true;
            ConvertService.start(this, null);
        }
        h.post(tick);
    }

    void startGame() {
        startActivity(new Intent(this, RaftActivity.class));
        finish();
    }

    TextView text(LinearLayout parent, float size, int color) {
        TextView t = new TextView(this);
        t.setTextSize(size); t.setTextColor(color); t.setPadding(0, dp(6), 0, dp(6));
        parent.addView(t);
        return t;
    }

    int dp(int v) { return (int) (v * getResources().getDisplayMetrics().density); }

    void buildUi() {
        ScrollView sv = new ScrollView(this);
        sv.setBackgroundColor(Color.rgb(12, 52, 74));
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        l.setPadding(dp(32), dp(24), dp(32), dp(24));
        sv.addView(l);
        title = text(l, 28, Color.rgb(240, 224, 190));
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setText("Raft Recomp");
        body = text(l, 16, Color.WHITE);
        boolean stale = new File(getExternalFilesDir(null), "raft-data.zip").isFile();
        body.setText((stale ? "Esta versión del APK necesita volver a convertir los datos.\n\n" : "") +
                "Este APK no trae el juego: usa tu propia copia de Raft (la versión de PC de Steam).\n\n" +
                "1. Copiá la carpeta \"Raft\" del juego (en Steam: Raft > Administrar > Ver archivos locales) a este teléfono, " +
                "por ejemplo a Descargas.\n" +
                "2. Tocá \"Elegir carpeta de Raft\" y seleccioná esa carpeta.\n" +
                "3. El teléfono la convierte (puede tardar bastante; se puede apagar la pantalla). " +
                "Necesita unos 4 GB libres. Después podés borrar la copia.");
        pick = new Button(this);
        pick.setText("Elegir carpeta de Raft");
        pick.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { choose(); } });
        l.addView(pick);
        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100); bar.setVisibility(View.GONE);
        l.addView(bar, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(24)));
        status = text(l, 15, Color.rgb(200, 230, 240));
        play = new Button(this);
        play.setText("Jugar");
        play.setVisibility(View.GONE);
        play.setOnClickListener(new View.OnClickListener() { public void onClick(View v) { startGame(); } });
        l.addView(play);
        setContentView(sv);
    }

    boolean canReadFiles() {
        if (Build.VERSION.SDK_INT >= 30) return Environment.isExternalStorageManager();
        return Build.VERSION.SDK_INT < 23 || checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) == 0;
    }

    void choose() {
        // the converter reads the folder by path (thousands of reads from Python, no SAF), which needs
        // "all files access" on Android 11+
        if (!canReadFiles()) {
            status.setText("Dale permiso de acceso a archivos a Raft Recomp y volvé a tocar el botón.");
            if (Build.VERSION.SDK_INT >= 30) {
                try {
                    startActivity(new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:" + getPackageName())));
                } catch (Exception e) { startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION)); }
            } else requestPermissions(new String[]{Manifest.permission.READ_EXTERNAL_STORAGE}, 2);
            return;
        }
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        startActivityForResult(i, PICK);
    }

    // content://.../tree/primary:Download/Raft -> /storage/emulated/0/Download/Raft
    static String treeToPath(Uri tree) {
        String id = DocumentsContract.getTreeDocumentId(tree);
        int c = id.indexOf(':');
        String vol = c < 0 ? id : id.substring(0, c), rel = c < 0 ? "" : id.substring(c + 1);
        String base = "primary".equalsIgnoreCase(vol) ? Environment.getExternalStorageDirectory().getPath() : "/storage/" + vol;
        return rel.isEmpty() ? base : base + "/" + rel;
    }

    static boolean looksLikeRaft(File d) {
        return new File(d, "Raft_Data/globalgamemanagers").isFile() || new File(d, "globalgamemanagers").isFile();
    }

    @Override protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req != PICK || res != RESULT_OK || data == null || data.getData() == null) return;
        String path = treeToPath(data.getData());
        if (!looksLikeRaft(new File(path))) {
            status.setText("En \"" + path + "\" no está Raft_Data. Elegí la carpeta \"Raft\" del juego (la que tiene Raft_Data adentro).");
            return;
        }
        chosen = path;
        ConvertService.start(this, path);
    }

    final Runnable tick = new Runnable() {
        @Override public void run() {
            if (ConvertService.running) {
                pick.setEnabled(false);
                bar.setVisibility(View.VISIBLE);
                bar.setProgress(ConvertService.percent);
                status.setText(ConvertService.percent + "%  " + ConvertService.status);
            } else if (ConvertService.finished && refreshing) {
                startGame();
                return;
            } else if (ConvertService.finished) {
                bar.setProgress(100);
                status.setText("¡Listo! Ya podés borrar la copia de Raft que pasaste al teléfono si querés.");
                pick.setVisibility(View.GONE);
                play.setVisibility(View.VISIBLE);
                return;
            } else if (ConvertService.failed) {
                pick.setEnabled(true);
                pick.setVisibility(View.VISIBLE);
                status.setText(ConvertService.status);
            }
            h.postDelayed(this, 500);
        }
    };

    @Override protected void onDestroy() {
        h.removeCallbacks(tick);
        super.onDestroy();
    }
}
