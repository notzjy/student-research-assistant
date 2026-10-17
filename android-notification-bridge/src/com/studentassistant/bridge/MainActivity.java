package com.studentassistant.bridge;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.os.Bundle;
import android.provider.Settings;
import android.net.Uri;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

/** 提供本地配置、应用选择、系统授权入口、队列状态和显式测试上传。 */
public final class MainActivity extends Activity {
    private BridgeConfig config;
    private EditText server;
    private EditText token;
    private CheckBox enabled;
    private final CheckBox[] packages = new CheckBox[3];
    private TextView status;

    /** 创建原生设置页，初次打开不会请求系统通知权限或发送数据。 */
    @Override public void onCreate(Bundle state) {
        super.onCreate(state); config = new BridgeConfig(this);
        ScrollView scroll = new ScrollView(this); LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL); layout.setPadding(32, 32, 32, 32); scroll.addView(layout); setContentView(scroll);
        // Android15强制边到边显示，为状态栏和导航栏保留可点击空间。
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(0, insets.getSystemWindowInsetTop(), 0, insets.getSystemWindowInsetBottom());
            return insets;
        });
        addText(layout, "个人研究与日程助理 · 通知桥", 23);
        addText(layout, "日常通过网络传输，无需USB。仅采集勾选应用的通知摘要，无法保证聊天全文、真实发件人ID或附件。", 16);
        addText(layout, "电脑服务器地址（同WiFi时填写电脑局域网IP）", 16);
        server = new EditText(this); server.setSingleLine(true); server.setHint("http://192.168.1.8:8002"); server.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI); server.setText(config.server()); layout.addView(server);
        addText(layout, "通知接入令牌（在电脑端生成）", 16);
        token = new EditText(this); token.setSingleLine(true); token.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD); token.setText(config.token()); layout.addView(token);
        enabled = new CheckBox(this); enabled.setText("启用通知采集与联网上传"); enabled.setChecked(config.enabled()); layout.addView(enabled);
        String[] labels = {"微信", "QQ", "BOSS直聘"}; Set<String> selected = config.packages();
        for (int i = 0; i < packages.length; i++) {
            packages[i] = new CheckBox(this); packages[i].setText(labels[i]); packages[i].setChecked(selected.contains(BridgeConfig.PACKAGES[i])); layout.addView(packages[i]);
        }
        addButton(layout, "1. 保存本机配置", view -> save());
        addButton(layout, "2. 打开系统通知使用权设置", view -> startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)));
        addButton(layout, "3. 上传一条合成测试通知", view -> sendTest());
        addButton(layout, "立即重试待传队列", view -> { BridgeTransport.flushAsync(this, () -> runOnUiThread(this::refresh)); });
        addButton(layout, "刷新授权和上传状态", view -> refresh());
        status = new TextView(this); status.setTextSize(16); layout.addView(status);
        addText(layout, "断网时最多缓存500条；容量满后淘汰最旧待传，并显示丢弃数量。网络恢复后系统会重试，但省电/强行停止/厂商后台策略可能延迟。跨网需要电脑上配置可安全访问的HTTPS地址。", 15);
        refresh(); applyPairing(getIntent());
    }

    /** 应用已打开时接收配对链接，同样只预填表单，仍须用户点击保存才生效。 */
    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent); setIntent(intent); applyPairing(intent);
    }

    /** 读取电脑界面配对二维码的深链，校验地址和令牌后仅预填，不自动启用或上传。 */
    private void applyPairing(Intent intent) {
        if (intent == null || !Intent.ACTION_VIEW.equals(intent.getAction()) || intent.getData() == null) return;
        Uri link = intent.getData();
        if (!"student-assistant".equals(link.getScheme()) || !"pair".equals(link.getHost())) return;
        try {
            String address = link.getQueryParameter("server"); String secret = link.getQueryParameter("token");
            if (address == null || secret == null) throw new IllegalArgumentException("配对链接不完整");
            // 兼容电脑传入完整POST地址，设置页仍保存服务器根地址。
            if (address.endsWith("/notifications")) address = address.substring(0, address.length() - "/notifications".length());
            address = BridgeConfig.validateServer(address);
            if (!secret.matches("[A-Za-z0-9_\\-]{20,256}")) throw new IllegalArgumentException("配对令牌格式不正确");
            server.setText(address); token.setText(secret); enabled.setChecked(true);
            Toast.makeText(this, "配对信息已填入，请核对地址后点击“保存本机配置”", Toast.LENGTH_LONG).show();
        } catch (Exception error) { Toast.makeText(this, "配对链接无效，请在电脑手机接入页重新生成", Toast.LENGTH_LONG).show(); }
    }

    /** 页面重新进入时刷新权限和队列统计，不自动导入旧通知。 */
    @Override protected void onResume() { super.onResume(); if (status != null) refresh(); }

    /** 添加不含动态HTML的原生文字标签。 */
    private void addText(LinearLayout layout, String text, int size) { TextView view = new TextView(this); view.setText(text); view.setTextSize(size); view.setPadding(0, 12, 0, 8); layout.addView(view); }

    /** 添加显式操作按钮，避免绘制页面时触发网络或授权。 */
    private void addButton(LinearLayout layout, String text, View.OnClickListener action) { Button button = new Button(this); button.setText(text); button.setOnClickListener(action); layout.addView(button); }

    /** 校验并保存表单，只有用户显式启用后才安排后台上传。 */
    private boolean save() {
        try {
            Set<String> selected = new HashSet<>();
            for (int i = 0; i < packages.length; i++) if (packages[i].isChecked()) selected.add(BridgeConfig.PACKAGES[i]);
            config.save(server.getText().toString(), token.getText().toString(), enabled.isChecked(), selected);
            BridgeTransport.schedule(this);
            if (config.enabled()) BridgeTransport.flushAsync(this, () -> runOnUiThread(this::refresh));
            Toast.makeText(this, "已保存在手机本机", Toast.LENGTH_SHORT).show(); refresh(); return true;
        } catch (Exception error) { Toast.makeText(this, error.getMessage(), Toast.LENGTH_LONG).show(); return false; }
    }

    /** 只在点击按钮后构造明确标记的合成通知，便于验证连通性，不冒充私人消息。 */
    private void sendTest() {
        if (!save()) return;
        if (!config.enabled()) { Toast.makeText(this, "请先勾选启用通知采集", Toast.LENGTH_LONG).show(); return; }
        try {
            JSONObject payload = new JSONObject(); payload.put("device_id", config.deviceId());
            payload.put("package_name", "com.tencent.mm"); payload.put("notification_key", "bridge-self-test-" + UUID.randomUUID());
            payload.put("posted_at", NotificationBridgeService.postedAt(System.currentTimeMillis()));
            payload.put("title", "通知桥连通测试（合成）"); payload.put("text", "这是一条由用户点击生成的连接测试，没有活动、截止事项或真实附件。");
            payload.put("big_text", ""); payload.put("text_lines", new JSONArray()); payload.put("conversation_id", "bridge-self-test");
            try (BridgeQueue queue = new BridgeQueue(this)) { queue.enqueue(payload); }
            BridgeTransport.flushAsync(this, () -> runOnUiThread(this::refresh)); refresh();
        } catch (Exception error) { Toast.makeText(this, "测试通知构造失败", Toast.LENGTH_SHORT).show(); }
    }

    /** 读取本机授权、有限队列和安全状态，不展示令牌或通知原文。 */
    private void refresh() {
        boolean authorized = false;
        String raw = Settings.Secure.getString(getContentResolver(), "enabled_notification_listeners");
        if (raw != null) for (String item : raw.split(":")) {
            ComponentName name = ComponentName.unflattenFromString(item);
            if (name != null && getPackageName().equals(name.getPackageName())) authorized = true;
        }
        int count; try (BridgeQueue queue = new BridgeQueue(this)) { count = queue.count(); }
        status.setText("系统通知使用权：" + (authorized ? "已开启" : "未开启") + "\n采集：" + (config.enabled() ? "已启用" : "已暂停")
                + "\n待上传：" + count + " 条\n累计丢弃：" + config.dropped() + " 条\n最近状态：" + config.status());
    }
}
