package com.studentassistant.bridge;

import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONObject;

/** 负责Bearer鉴权上传、短时异步发送和系统网络重试，不需要USB连接。 */
public final class BridgeTransport {
    private static final ExecutorService EXECUTOR = Executors.newSingleThreadExecutor();
    private static final AtomicBoolean UPLOADING = new AtomicBoolean(false);
    private static final int JOB_ID = 10421;

    /** 安排持久化的联网重试；Android可因省电策略推迟任务，不承诺离线时实时。 */
    public static void schedule(Context context) {
        JobScheduler scheduler = context.getSystemService(JobScheduler.class);
        if (scheduler == null) return;
        if (!new BridgeConfig(context).enabled()) { scheduler.cancel(JOB_ID); return; }
        JobInfo job = new JobInfo.Builder(JOB_ID, new ComponentName(context, RetryJobService.class))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPersisted(true)
                .setPeriodic(15 * 60 * 1000L).build();
        scheduler.schedule(job);
    }

    /** 在单工作线程中上传；completion用于通知系统JobService本次运行已经结束。 */
    public static void flushAsync(Context context, Runnable completion) {
        Context app = context.getApplicationContext();
        // 合并密集通知的唤醒，不把每次回调都堆成一个网络任务。
        if (!UPLOADING.compareAndSet(false, true)) {
            if (completion != null) completion.run();
            return;
        }
        EXECUTOR.execute(() -> {
            boolean canContinue = false;
            try { canContinue = flush(app); }
            finally {
                UPLOADING.set(false);
                if (completion != null) completion.run();
                // 一批成功后若仍有新记录，继续发送；失败时等待联网事件或系统重试。
                if (canContinue) try (BridgeQueue queue = new BridgeQueue(app)) {
                    if (queue.count() > 0) flushAsync(app, null);
                }
            }
        });
    }

    /** 顺序发送最多30条，失败保留队列；重试仍使用相同内容交由后端幂等。 */
    private static boolean flush(Context context) {
        BridgeConfig config = new BridgeConfig(context);
        if (!config.enabled() || config.server().isEmpty() || config.token().isEmpty()) return false;
        try (BridgeQueue queue = new BridgeQueue(context)) {
            for (BridgeQueue.Entry row : queue.pending(30)) {
                if (!config.enabled()) return false;
                // 取消勾选某应用后，它之前缓存的通知也不再上传。
                if (!config.packages().contains(new JSONObject(row.payload).optString("package_name"))) {
                    queue.acknowledge(row); config.addDropped();
                    config.status("已跳过当前未勾选应用的缓存通知"); continue;
                }
                HttpURLConnection connection = null;
                try {
                    // 禁止重定向，避免服务器错误配置把令牌转发给另一个主机。
                    URL endpoint = new URL(BridgeConfig.validateServer(config.server()) + "/notifications");
                    connection = (HttpURLConnection) endpoint.openConnection();
                    connection.setInstanceFollowRedirects(false);
                    connection.setConnectTimeout(10000); connection.setReadTimeout(15000);
                    connection.setRequestMethod("POST"); connection.setDoOutput(true);
                    connection.setRequestProperty("Authorization", "Bearer " + config.token());
                    connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                    byte[] body = row.payload.getBytes(StandardCharsets.UTF_8);
                    connection.setFixedLengthStreamingMode(body.length);
                    try (OutputStream stream = connection.getOutputStream()) { stream.write(body); }
                    int status = connection.getResponseCode();
                    if (status >= 200 && status < 300) {
                        queue.acknowledge(row); config.status("已上传；待传 " + queue.count() + " 条");
                    } else if (status == 400 || status == 413 || status == 422) {
                        queue.acknowledge(row); config.addDropped(); config.status("载荷被拒绝(" + status + ")，请更新应用并检查设置");
                    } else {
                        config.status(status == 401 || status == 403 ? "令牌无效或接入未启用，请重新配置" : "服务器暂不可用(" + status + ")；已保留待传");
                        return false;
                    }
                } catch (Exception error) {
                    config.status("连接失败；通知保留本机，联网后自动重试"); return false;
                } finally { if (connection != null) connection.disconnect(); }
            }
            return true;
        } catch (Exception error) { config.status("队列读取失败；请打开应用检查状态"); return false; }
    }
}
