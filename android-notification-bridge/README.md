# 个人助理通知桥

将手机**新产生的通知摘要**经网络发送给电脑助理。已提供可安装APK，不要求手机一直插USB。APK基于Android系统API构建，没有第三方运行库。

- 安装包：[`dist/student-assistant-bridge.apk`](dist/student-assistant-bridge.apk)
- 使用、权限和局域网连接步骤：[`../docs/CHANNEL_INTEGRATION.md`](../docs/CHANNEL_INTEGRATION.md)
- APK编译、签名和校验记录：[`dist/build_result.json`](dist/build_result.json)

支持Android 8.0及以上。应用首次打开默认暂停采集，默认应用范围为微信、QQ、BOSS直聘；保存配置、勾选启用并由用户在系统设置中授予通知使用权后才采集。电脑应运行项目后端并允许专用通知接口的局域网连接。

通知不等于聊天全文。昵称不能保证唯一发件人，通知时间不等于原消息发送时间，通知中提到的文件也没有被下载。此应用不读取聊天数据库、不执行通知操作、不自动打开其他应用或下载文件。

断网时SQLite队列最多保留500条通知，满后淘汰最旧待传并显示丢弃数。相同通知键和相同内容去重，通知内容更新保留为新版本；MessagingStyle已有消息时间戳时，按会话、最新文本和消息时刻去重，避免历史聚合变化重复上传。联网回调、重新启用或下一条通知会尝试立即补传，系统JobScheduler提供15分钟周期的兜底重试；Android省电、厂商后台限制和强行停止可能进一步推迟，不能承诺秒级实时。

从源码重新构建：在项目根目录执行以下命令。工具存于项目`local-data/android-sdk-tooling/`，不会修改全局PATH。

```powershell
.\.venv\Scripts\python.exe -X utf8 .\android-notification-bridge\build_apk.py
```

需要JDK及官方Android API 35平台、Build Tools 35.0.0。`build_apk.py --sdk <目录>`也可指定已安装的SDK。构建使用本地开发证书；证书仅存SDK工具目录，重新生成证书后无法直接覆盖原证书安装的同包应用。

交付记录必须区分：APK编译和签名验证已完成；当前没有连接到可用Android设备，安装、系统授权、真实通知和不同机型后台行为尚未实机验证。
