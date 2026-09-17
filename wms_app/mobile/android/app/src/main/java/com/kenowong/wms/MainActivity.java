package com.kenowong.wms;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeWebViewClient;

import android.content.SharedPreferences;
import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.JavascriptInterface;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.FrameLayout;
import android.widget.Toast;
import androidx.appcompat.app.AlertDialog;
import android.graphics.Color;
import android.view.Gravity;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

public class MainActivity extends BridgeActivity {

    private static final String PREFS = "wms_prefs";
    private static final String KEY_URL = "server_url";
    private static final String KEY_HISTORY = "server_history";
    private static final String KEY_REMEMBER = "remember_cred";
    private static final String KEY_USER = "saved_user";
    private static final String KEY_PASS = "saved_pass";

    private SharedPreferences prefs;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);

        Bridge bridge = getBridge();
        WebView webView = bridge.getWebView();
        WebSettings ws = webView.getSettings();
        // NAS 是 http 明文，允许混合内容加载
        ws.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        ws.setDomStorageEnabled(true);
        ws.setDatabaseEnabled(true);
        // 关闭系统「强制深色」，防止夜间模式把网页/设置弹窗反色成看不清
        if (android.os.Build.VERSION.SDK_INT >= 29) {
            ws.setForceDark(WebSettings.FORCE_DARK_OFF);
            webView.setForceDarkAllowed(false);
        }

        // 暴露 JS 桥，供网页登录时回传账号密码（记住进销存登录）
        webView.addJavascriptInterface(new WmsJsBridge(), "WmsApp");
        // 必须继承 BridgeWebViewClient，否则 Capacitor 的扫码桥不会注入
        webView.setWebViewClient(new BridgeWebViewClient(bridge) {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                injectCredentials();
            }
        });

        // 左下角悬浮齿轮：重新连接服务器
        addGearButton();

        String saved = prefs.getString(KEY_URL, "");
        if (saved.isEmpty()) {
            // 首次启动：预填 NAS 默认地址并弹出连接设置
            showSettings("http://192.168.194.34:8899");
        } else {
            webView.loadUrl(normalize(saved));
        }
    }

    private void addGearButton() {
        FrameLayout root = (FrameLayout) findViewById(android.R.id.content);
        if (root == null) return;
        ImageButton gear = new ImageButton(this);
        gear.setImageResource(android.R.drawable.ic_menu_manage);
        gear.setBackgroundColor(Color.parseColor("#00000000"));
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT);
        lp.gravity = Gravity.BOTTOM | Gravity.START;
        lp.bottomMargin = 28;
        lp.leftMargin = 24;
        gear.setLayoutParams(lp);
        gear.setOnClickListener(v -> showSettings(prefs.getString(KEY_URL, "http://192.168.194.34:8899")));
        root.addView(gear);
    }

    private String normalize(String raw) {
        if (!raw.startsWith("http://") && !raw.startsWith("https://")) {
            return "http://" + raw;
        }
        return raw;
    }

    private void showSettings(String preset) {
        AlertDialog.Builder b = new AlertDialog.Builder(this);
        b.setTitle("连接服务器（进销存 NAS 地址）");
        final EditText et = new EditText(this);
        et.setHint("http://192.168.194.34:8899");
        et.setText(preset == null ? "" : preset);
        b.setView(et);
        b.setPositiveButton("连接", (d, w) -> {
            String raw = et.getText().toString().trim();
            if (raw.isEmpty()) {
                Toast.makeText(this, "请输入服务器地址", Toast.LENGTH_SHORT).show();
                return;
            }
            saveHistory(raw);
            prefs.edit().putString(KEY_URL, raw).apply();
            getBridge().getWebView().loadUrl(normalize(raw));
        });
        b.setNegativeButton("取消", null);
        b.show();
    }

    private void saveHistory(String url) {
        Set<String> old = prefs.getStringSet(KEY_HISTORY, new LinkedHashSet<>());
        LinkedHashSet<String> set = new LinkedHashSet<>(old);
        set.remove(url);
        List<String> list = new ArrayList<>(set);
        list.add(0, url);
        if (list.size() > 10) list = list.subList(0, 10);
        prefs.edit().putStringSet(KEY_HISTORY, new LinkedHashSet<>(list)).apply();
    }

    /**
     * 自动填入已保存的账号密码（仅当 App 内「记住进销存登录账号密码」开启时）。
     * 同时 hook 全局 doLogin，登录成功时把用户名/密码回传给 App 保存。
     */
    private void injectCredentials() {
        if (!prefs.getBoolean(KEY_REMEMBER, true)) return;
        String user = prefs.getString(KEY_USER, "");
        String pass = prefs.getString(KEY_PASS, "");
        String js = "javascript:(function(){"
                + "var u=document.getElementById('login-user');"
                + "var p=document.getElementById('login-pwd');"
                + "if(u&&p){"
                + "if(" + JSONObject.quote(user) + "){u.value=" + JSONObject.quote(user) + ";}"
                + "if(" + JSONObject.quote(pass) + "){p.value=" + JSONObject.quote(pass) + ";}"
                + "}"
                + "if(!window.__wmsHooked){window.__wmsHooked=true;"
                + "var _orig=window.doLogin;"
                + "window.doLogin=function(){"
                + "try{var uu=document.getElementById('login-user');var pp=document.getElementById('login-pwd');"
                + "if(uu&&pp&&uu.value){WmsApp.saveLogin(uu.value,pp.value);}}catch(e){}"
                + "if(typeof _orig==='function'){return _orig.apply(this,arguments);}}}"
                + "})();";
        getBridge().getWebView().postDelayed(() -> getBridge().getWebView().loadUrl(js), 800);
    }

    /**
     * JS 桥：网页登录（doLogin）时回传账号密码，由 App 保存到 SharedPreferences。
     * 仅当「记住进销存登录账号密码」开启时生效。
     */
    private class WmsJsBridge {
        @JavascriptInterface
        public void saveLogin(String user, String pass) {
            if (!prefs.getBoolean(KEY_REMEMBER, true)) return;
            if (user == null || user.trim().isEmpty()) return;
            prefs.edit()
                    .putString(KEY_USER, user.trim())
                    .putString(KEY_PASS, pass == null ? "" : pass)
                    .apply();
        }
    }
}
