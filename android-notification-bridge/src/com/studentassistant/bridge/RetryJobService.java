package com.studentassistant.bridge;

import android.app.job.JobParameters;
import android.app.job.JobService;

/** 让Android在网络可用时重试持久队列，不依赖电脑USB或应用页面持续打开。 */
public final class RetryJobService extends JobService {
    /** 启动异步上传，结束后显式交还系统调度权。 */
    @Override public boolean onStartJob(JobParameters parameters) {
        BridgeTransport.flushAsync(this, () -> jobFinished(parameters, false)); return true;
    }

    /** 系统中止时保留SQLite队列，后续由系统重新安排。 */
    @Override public boolean onStopJob(JobParameters parameters) { return true; }
}
