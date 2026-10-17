package com.studentassistant.bridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** 手机重启后恢复已由用户启用的网络重试任务。 */
public final class BootReceiver extends BroadcastReceiver {
    /** 只恢复调度，不请求系统授权、不读取旧通知。 */
    @Override public void onReceive(Context context, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) BridgeTransport.schedule(context);
    }
}
