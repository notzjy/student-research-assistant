package com.studentassistant.bridge;

import android.content.Context;
import android.content.SharedPreferences;
import java.net.URI;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

/** 管理仅存于手机应用私有目录的服务器、令牌和采集开关。 */
public final class BridgeConfig {
    public static final String[] PACKAGES = {"com.tencent.mm", "com.tencent.mobileqq", "com.hpbr.bosszhipin"};
    private final SharedPreferences prefs;

    /** 打开应用私有配置，不访问电脑环境文件或其他应用数据。 */
    public BridgeConfig(Context context) {
        prefs = context.getApplicationContext().getSharedPreferences("bridge", Context.MODE_PRIVATE);
    }

    /** 读取后台采集总开关；初始状态关闭，由用户保存配置后启用。 */
    public boolean enabled() { return prefs.getBoolean("enabled", false); }

    /** 返回用户选择的应用包，默认只列出微信、QQ和BOSS直聘。 */
    public Set<String> packages() {
        return new HashSet<>(prefs.getStringSet("packages", new HashSet<>(Arrays.asList(PACKAGES))));
    }

    /** 返回不含路径和鉴权信息的服务器根地址。 */
    public String server() { return prefs.getString("server", ""); }

    /** 读取私有令牌，仅供网络请求设置Authorization头。 */
    public String token() { return prefs.getString("token", ""); }

    /** 创建稳定的随机设备标识，不读取硬件标识或手机账号。 */
    public synchronized String deviceId() {
        String value = prefs.getString("device_id", "");
        if (value.isEmpty()) {
            value = UUID.randomUUID().toString();
            prefs.edit().putString("device_id", value).commit();
        }
        return value;
    }

    /** 检查地址形式并限制明文HTTP只用于用户明确配置的局域网或回环地址。 */
    public static String validateServer(String value) throws Exception {
        URI uri = new URI(value.trim());
        String scheme = uri.getScheme();
        String host = uri.getHost();
        if (!("https".equals(scheme) || "http".equals(scheme)) || host == null
                || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null
                || (uri.getPath() != null && !uri.getPath().isEmpty() && !"/".equals(uri.getPath()))
                || uri.getPort() < -1 || uri.getPort() > 65535 || uri.getPort() == 0) {
            throw new IllegalArgumentException("请填写服务器根地址，例如 http://192.168.1.8:8002");
        }
        if ("http".equals(scheme) && !isLocalHost(host)) {
            throw new IllegalArgumentException("跨网连接必须使用HTTPS；HTTP仅支持明确的局域网IP或localhost");
        }
        return scheme + "://" + uri.getRawAuthority();
    }

    /** 只按字面值识别私网IP，不解析域名以避免HTTP令牌被发往公网。 */
    private static boolean isLocalHost(String host) {
        if ("localhost".equalsIgnoreCase(host) || "127.0.0.1".equals(host) || "[::1]".equals(host)) return true;
        String[] parts = host.split("\\.");
        if (parts.length != 4) return false;
        try {
            int[] values = new int[4];
            for (int i = 0; i < 4; i++) {
                values[i] = Integer.parseInt(parts[i]);
                if (values[i] < 0 || values[i] > 255) return false;
            }
            return values[0] == 10 || (values[0] == 192 && values[1] == 168)
                    || (values[0] == 172 && values[1] >= 16 && values[1] <= 31);
        } catch (NumberFormatException error) { return false; }
    }

    /** 保存用户主动提交的配置，令牌不写日志、不做系统备份。 */
    public void save(String server, String token, boolean enabled, Set<String> packages) throws Exception {
        String cleanServer = validateServer(server);
        String cleanToken = token.trim();
        if (!cleanToken.matches("[A-Za-z0-9_\\-]{20,256}")) throw new IllegalArgumentException("请粘贴电脑上生成的完整通知接入令牌");
        prefs.edit().putString("server", cleanServer).putString("token", cleanToken)
                .putBoolean("enabled", enabled).putStringSet("packages", new HashSet<>(packages)).commit();
    }

    /** 记录不含原文或凭证的状态，供配置页面显示。 */
    public void status(String value) { prefs.edit().putString("status", value).apply(); }

    /** 返回最近一次上传状态，不把服务端响应正文显示给用户。 */
    public String status() { return prefs.getString("status", "尚未上传"); }

    /** 记录队列达到上限或无效载荷导致的丢弃计数。 */
    public synchronized void addDropped() { prefs.edit().putInt("dropped", dropped() + 1).commit(); }

    /** 返回累计丢弃数，防止限量队列造成静默的数据丢失。 */
    public int dropped() { return prefs.getInt("dropped", 0); }
}
