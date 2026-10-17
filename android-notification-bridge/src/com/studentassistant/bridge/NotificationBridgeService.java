package com.studentassistant.bridge;

import android.app.Notification;
import android.os.Bundle;
import android.os.Build;
import android.os.Parcelable;
import android.net.ConnectivityManager;
import android.net.Network;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import org.json.JSONArray;
import org.json.JSONObject;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.List;

/** 只读取用户已授权且已勾选应用的新通知摘要，不打开应用、不抓取聊天库、不读取附件。 */
public final class NotificationBridgeService extends NotificationListenerService {
    private ConnectivityManager.NetworkCallback networkCallback;

    /** 系统完成绑定后恢复联网重试，不主动导入历史活动通知。 */
    @Override public void onListenerConnected() {
        BridgeTransport.schedule(this); BridgeTransport.flushAsync(this, null);
        ConnectivityManager manager = getSystemService(ConnectivityManager.class);
        if (manager != null && networkCallback == null) {
            networkCallback = new ConnectivityManager.NetworkCallback() {
                /** 手机重新联网后立即尝试补传；省电模式下仍以系统实际回调为准。 */
                @Override public void onAvailable(Network network) { BridgeTransport.flushAsync(NotificationBridgeService.this, null); }
            };
            manager.registerDefaultNetworkCallback(networkCallback);
        }
    }

    /** 服务销毁时注销网络监听，避免泄漏系统回调；持久队列仍由系统任务兜底。 */
    @Override public void onDestroy() {
        ConnectivityManager manager = getSystemService(ConnectivityManager.class);
        if (manager != null && networkCallback != null) manager.unregisterNetworkCallback(networkCallback);
        super.onDestroy();
    }

    /** 接收系统通知，过滤其他应用和群组汇总，然后持久入队再异步发送。 */
    @Override public void onNotificationPosted(StatusBarNotification item) {
        BridgeConfig config = new BridgeConfig(this);
        if (item == null || !config.enabled() || !config.packages().contains(item.getPackageName())) return;
        Notification notification = item.getNotification();
        if (notification == null || (notification.flags & Notification.FLAG_GROUP_SUMMARY) != 0) return;
        try {
            Bundle extras = notification.extras == null ? Bundle.EMPTY : notification.extras;
            JSONObject payload = new JSONObject();
            payload.put("device_id", config.deviceId()); payload.put("package_name", item.getPackageName());
            payload.put("notification_key", clip(item.getKey(), 500));
            // 此时刻是系统发布通知的时刻，绝不冒充发送者原消息的发送时间。
            payload.put("posted_at", postedAt(item.getPostTime()));
            payload.put("title", clip(extras.getCharSequence(Notification.EXTRA_TITLE), 300));
            payload.put("text", clip(extras.getCharSequence(Notification.EXTRA_TEXT), 4000));
            payload.put("big_text", clip(extras.getCharSequence(Notification.EXTRA_BIG_TEXT), 4000));
            JSONArray lines = new JSONArray();
            CharSequence[] sourceLines = extras.getCharSequenceArray(Notification.EXTRA_TEXT_LINES);
            if (sourceLines != null) for (int i = 0; i < Math.min(sourceLines.length, 12); i++) lines.put(clip(sourceLines[i], 1000));
            payload.put("text_lines", lines);
            LatestMessage latest = latestMessage(extras);
            if (latest != null && !latest.text.isEmpty()) {
                // 只取当前MessagingStyle数组的最新一条，不把历史聚合当成新发的多条消息。
                payload.put("text", clip(latest.text, 4000));
                payload.put("text_source", "latest_message");
                payload.put("message_sent_at", latest.timestamp > 0 ? postedAt(latest.timestamp) : JSONObject.NULL);
            } else {
                payload.put("text_source", payload.optString("text").isEmpty() && lines.length() > 0 ? "aggregate" : "notification_text");
                payload.put("message_sent_at", JSONObject.NULL);
            }
            String shortcut = notification.getShortcutId();
            payload.put("conversation_id", clip(shortcut == null || shortcut.trim().isEmpty() ? item.getKey() : "shortcut:" + shortcut, 500));
            if (payload.optString("text").isEmpty() && payload.optString("big_text").isEmpty() && lines.length() == 0) return;
            try (BridgeQueue queue = new BridgeQueue(this)) { queue.enqueue(payload); }
            BridgeTransport.flushAsync(this, null);
        } catch (Exception error) { config.status("有通知无法解析，已跳过；请检查应用版本"); }
    }

    /** 截断平台可见字段，控制单条通知的内存和网络用量。 */
    public static String clip(CharSequence value, int limit) {
        String result = value == null ? "" : value.toString();
        return result.length() <= limit ? result : result.substring(0, limit);
    }

    /** 按设备当地时区输出有明确UTC偏移的ISO时间。 */
    public static String postedAt(long timestamp) {
        return new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSXXX", Locale.US).format(new Date(timestamp));
    }

    /** 提取当前消息数组中最新一条；Android11使用公开解析方法，旧版仅尽力读取平台Bundle字段。 */
    private static LatestMessage latestMessage(Bundle extras) {
        Parcelable[] messages = extras.getParcelableArray(Notification.EXTRA_MESSAGES);
        if (messages == null || messages.length == 0) return null;
        LatestMessage result = null;
        if (Build.VERSION.SDK_INT >= 30) {
            List<Notification.MessagingStyle.Message> parsed = Notification.MessagingStyle.Message.getMessagesFromBundleArray(messages);
            for (Notification.MessagingStyle.Message message : parsed) {
                LatestMessage candidate = new LatestMessage(clip(message.getText(), 4000), message.getTimestamp());
                if (result == null || candidate.timestamp >= result.timestamp) result = candidate;
            }
        } else {
            // API26-29没有公开的getMessagesFromBundleArray；只接受平台实际提供的text/time字段。
            for (Parcelable message : messages) if (message instanceof Bundle) {
                Bundle fields = (Bundle) message;
                if (!fields.containsKey("text")) continue;
                LatestMessage candidate = new LatestMessage(clip(fields.getCharSequence("text"), 4000), fields.getLong("time", 0));
                if (result == null || candidate.timestamp >= result.timestamp) result = candidate;
            }
        }
        return result;
    }

    /** 保留平台实际给出的消息文本与时间戳，时间未知时保持0而不改用通知时间伪造。 */
    private static final class LatestMessage {
        final String text;
        final long timestamp;

        /** 创建一次通知消息快照。 */
        LatestMessage(String text, long timestamp) { this.text = text; this.timestamp = timestamp; }
    }
}
