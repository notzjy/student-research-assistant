package com.studentassistant.bridge;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;

/** 以SQLite保存待上传通知和有限的已确认指纹，应用退出或网络断开后仍可重试。 */
public final class BridgeQueue extends SQLiteOpenHelper implements AutoCloseable {
    private final BridgeConfig config;
    private static final int MAX_PENDING = 500;
    private static final int MAX_SEEN = 2000;

    /** 打开应用私有数据库，队列不备份到其他应用或云服务。 */
    public BridgeQueue(Context context) {
        super(context.getApplicationContext(), "notifications.db", null, 1);
        config = new BridgeConfig(context);
    }

    /** 初始化待传队列与去重表，只有成功上传后才从待传队列删除。 */
    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE queue(id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)");
        db.execSQL("CREATE TABLE seen(fingerprint TEXT PRIMARY KEY, seen_at INTEGER NOT NULL)");
    }

    /** 当前仅有第一版结构；未来升级必须提供显式迁移。 */
    @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        throw new IllegalStateException("没有可用的数据库迁移");
    }

    /** 计算内容指纹；不含重复回调时可能变化的posted_at，因此相同通知不会无限重传。 */
    public static String fingerprint(JSONObject payload) throws Exception {
        String value;
        if ("latest_message".equals(payload.optString("text_source")) && !payload.isNull("message_sent_at")) {
            // 已有明确最新消息时，不让聚合历史或标题未读数量变化造成重复上传。
            value = payload.optString("package_name") + "\n" + payload.optString("conversation_id")
                    + "\n" + payload.optString("text") + "\n" + payload.optString("message_sent_at");
        } else {
            value = payload.optString("package_name") + "\n" + payload.optString("notification_key")
                    + "\n" + payload.optString("title") + "\n" + payload.optString("text")
                    + "\n" + payload.optString("big_text") + "\n" + payload.optJSONArray("text_lines")
                    + "\n" + payload.optString("conversation_id") + "\n" + payload.optString("message_sent_at")
                    + "\n" + payload.optString("text_source", "notification_text");
        }
        byte[] bytes = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
        StringBuilder hex = new StringBuilder();
        for (byte item : bytes) hex.append(String.format("%02x", item & 0xff));
        return hex.toString();
    }

    /** 原子去重和入队；超过500条时淘汰最旧待传记录并累计可见丢弃数。 */
    public synchronized boolean enqueue(JSONObject payload) throws Exception {
        SQLiteDatabase db = getWritableDatabase();
        String fingerprint = fingerprint(payload);
        db.beginTransaction();
        try {
            try (Cursor cursor = db.rawQuery("SELECT 1 FROM seen WHERE fingerprint=?", new String[]{fingerprint})) {
                if (cursor.moveToFirst()) { db.setTransactionSuccessful(); return false; }
            }
            ContentValues row = new ContentValues();
            row.put("fingerprint", fingerprint); row.put("payload", payload.toString());
            long id = db.insertWithOnConflict("queue", null, row, SQLiteDatabase.CONFLICT_IGNORE);
            if (id == -1) { db.setTransactionSuccessful(); return false; }
            while (countIn(db) > MAX_PENDING) {
                db.execSQL("DELETE FROM queue WHERE id=(SELECT MIN(id) FROM queue)");
                config.addDropped();
            }
            db.setTransactionSuccessful(); return true;
        } finally { db.endTransaction(); }
    }

    /** 读取最早的有限批次，不在持有游标时执行网络操作。 */
    public synchronized List<Entry> pending(int limit) {
        List<Entry> rows = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query("queue", new String[]{"id", "fingerprint", "payload"}, null, null, null, null, "id ASC", Integer.toString(limit))) {
            while (cursor.moveToNext()) rows.add(new Entry(cursor.getLong(0), cursor.getString(1), cursor.getString(2)));
        }
        return rows;
    }

    /** 成功或明确不可重试的载荷才确认，并把指纹保留在有界去重表。 */
    public synchronized void acknowledge(Entry row) {
        SQLiteDatabase db = getWritableDatabase(); db.beginTransaction();
        try {
            ContentValues seen = new ContentValues(); seen.put("fingerprint", row.fingerprint); seen.put("seen_at", System.currentTimeMillis());
            db.insertWithOnConflict("seen", null, seen, SQLiteDatabase.CONFLICT_REPLACE);
            db.delete("queue", "id=?", new String[]{Long.toString(row.id)});
            db.execSQL("DELETE FROM seen WHERE fingerprint NOT IN (SELECT fingerprint FROM seen ORDER BY seen_at DESC LIMIT " + MAX_SEEN + ")");
            db.setTransactionSuccessful();
        } finally { db.endTransaction(); }
    }

    /** 返回队列数量，用于设置页和上传调度。 */
    public synchronized int count() { return countIn(getReadableDatabase()); }

    /** 读取指定连接的队列大小，调用方负责事务。 */
    private int countIn(SQLiteDatabase db) {
        try (Cursor cursor = db.rawQuery("SELECT COUNT(*) FROM queue", null)) { cursor.moveToFirst(); return cursor.getInt(0); }
    }

    /** 保存一次读取到的队列记录，网络请求不能擅自改变它的内容。 */
    public static final class Entry {
        public final long id;
        public final String fingerprint;
        public final String payload;

        /** 创建待上传记录快照。 */
        Entry(long id, String fingerprint, String payload) { this.id = id; this.fingerprint = fingerprint; this.payload = payload; }
    }
}
