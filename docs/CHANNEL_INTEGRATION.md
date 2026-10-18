# 微信、QQ和BOSS直聘的通知接入

Android通知桥通过网络上传新通知，日常运行不需要USB。USB只是一种可选的安装和调试方式：也可以把APK发到自己的手机，保存到本地后点开安装。

## 已交付和验证范围

| 能力 | 当前状态 |
|---|---|
| Java通知桥源码、配置页、应用选择与显式测试按钮 | 已实现 |
| Android API 35编译、DEX转换、APK对齐与v2/v3签名验证 | 已实际执行通过 |
| 手机SQLite离线队列、指纹去重、联网补传、系统周期重试 | 已实现并成功编译，等待真机行为验收 |
| 微信、QQ、BOSS直聘通知字段 | 按系统API接收摘要；没有获得真实设备通知证据 |
| 已下载的QQ邮箱原始邮件与附件 | 另走IMAP接入，不由通知桥下载 |
| 聊天全文、稳定真实发件人ID、微信/QQ/BOSS附件自动下载 | 通知API不提供保证，不能据此声称完成 |

本轮环境原本只有JDK18，没有Android SDK、adb和Gradle。构建所需SDK工具从Google官方仓库下载到项目本地目录，并按仓库清单的SHA1校验；没有修改系统PATH。ADB只执行设备列表检查，可见设备数为0；没有安装应用、授予权限或读取真实通知。

## 安装和连接

1. 手机浏览器可直接打开[GitHub HTTPS安装包](https://raw.githubusercontent.com/notzjy/student-research-assistant/main/android-notification-bridge/dist/student-assistant-bridge.apk)，下载安装；该下载方式不依赖电脑助理是否正在运行。也可将仓库内的[`student-assistant-bridge.apk`](../android-notification-bridge/dist/student-assistant-bridge.apk)主动传到自己的手机安装。最低Android 8.0；无需在手机安装Python。
2. 在电脑助理界面打开“手机接入”页，按页面显式启用通知接入并生成专用接入令牌。令牌保留在本机；配对二维码包含令牌，只用自己的手机扫描，不发到聊天、不截图分享。
3. 让电脑和手机连接同一个可互通的Wi-Fi，在电脑启动控制台勾选局域网接入后点击启动，并保持助理服务运行。手机应用填写电脑的局域网IP与后端端口，例如`http://192.168.1.8:8002`。这个地址只是示例；`127.0.0.1`在手机上指的是手机自己。局域网下载、配对和上传都要求电脑服务在线；电脑未启动时旧局域网下载链接不可用，安装可改用第一步的HTTPS地址。
4. 电脑“手机接入”页提供局域网APK下载地址，手机浏览器也可打开该地址下载，不需要USB。安装后扫描配对二维码，用`student-assistant://pair`链接预填服务器和令牌；如果手机扫码工具不识别自定义链接，仍可在应用配置页手工填写。预填不会自动保存、上传或授予权限；核对服务器地址，勾选需要采集的应用和启用采集后，主动点“保存本机配置”。再点“打开系统通知使用权设置”，在系统页面中自行授予“个人助理通知采集”通知使用权。首次授权不会主动抓取手机原有活动通知。
5. 点击“上传一条合成测试通知”，在手机看最近上传状态，在电脑消息列表核对明确标有“合成”的连接测试。这个按钮只创建测试文本，没有真实私人消息或活动提醒。
6. 授权后由选定应用产生一条新通知，核对电脑是否收到摘要、来源和时间信息；再断网产生新通知，恢复网络，核对补传及去重。此步骤尚待用户设备验证，接口模拟成功不代替真机成功。

电脑服务需要监听局域网地址；只有专用通知入口接受Bearer令牌的远程POST，其余业务接口限制在本机。若Windows防火墙阻挡，由用户在系统中只为当前可信私网允许Python服务所用端口。代码不会自行开放防火墙、配置路由器端口映射或开启公网监听。

Android可能因侧载应用、系统版本或厂商策略限制通知使用权。遇到系统明确阻止时，先查看手机应用信息和系统说明，由用户决定是否允许；项目不会自动点击、绕过权限或关闭系统保护。

## 日常运行与离线处理

手机通知监听服务收到选定应用的新通知后，先提交到手机SQLite，再以后台线程上传。收到HTTP成功响应后才确认移出待传队列，网络失败保留；服务端也按来源和内容去重。因此上传中断后重试不会要求重新创建一条虚假的历史消息。

同一个通知键的内容有变化时作为新版本上传；只有发布时间变化而内容相同时不重复上传。手机最多保存500条待传记录、2000个已确认指纹；达到容量会淘汰最旧待传并显示丢弃数。取消勾选应用后，它之前缓存的记录也不会继续上传。

连接恢复回调和新通知会尝试立即补传，15分钟周期的JobScheduler用于补救没有及时回调的情况。Android可能因为省电策略、后台限制或用户强行停止而延迟执行；通知没有在系统中显示、通知内容被隐藏、应用前台不发通知时，本项目也无法凭空恢复原消息。

USB拔掉不会关闭这条网络链路。电脑关机、助理停止、不同Wi-Fi互相隔离时，手机先缓存。跨网使用需要用户配置经过认证与TLS保护的可达HTTPS入口；本轮没有部署公网服务，不能把局域网HTTP地址当成随处可用。

## HTTP协议与数据边界

```http
POST /notifications
Authorization: Bearer <本机专用令牌>
Content-Type: application/json
```

以下全部为合成示例，不含真实账号或令牌：

```json
{
  "device_id": "随机生成的设备UUID",
  "package_name": "com.tencent.mm",
  "notification_key": "synthetic-notification-key",
  "posted_at": "2026-10-17T10:00:00.000+08:00",
  "title": "示例联系人",
  "text": "明天下午三点在实验楼302开组会。",
  "big_text": "",
  "text_lines": [],
  "conversation_id": "synthetic-conversation",
  "message_sent_at": null,
  "text_source": "notification_text"
}
```

| 字段 | 真实含义 |
|---|---|
| `device_id` | 手机应用私有目录中随机生成的设备UUID，不是手机号或硬件ID |
| `package_name` | 系统报告的通知来源包；第一版只允许微信、QQ、BOSS直聘 |
| `notification_key` | Android通知实例标识，不是聊天消息真实ID |
| `posted_at` | 系统发布该通知的时刻，带明确UTC偏移；不保证等于发件人的发送时刻 |
| `title/text/big_text/text_lines` | 应用提交给系统的通知可见文本，不保证包含全文 |
| `conversation_id` | 可用时采用通知shortcutId，否则以通知键作为范围；不保证真实联系人唯一性 |
| `message_sent_at` | 仅MessagingStyle消息实际提供大于0的时间戳时填写；这是来源应用提供的消息时刻，不填通知发布时间冒充 |
| `text_source` | `latest_message`表示只选当前消息数组最新一条；`notification_text`为普通通知文本；`aggregate`表示只有无法逐条定位的聚合摘要 |

后端保留`completeness=notification_excerpt`及相应`time_basis`，并把昵称/会话范围标成未核实身份。用户手动确认的角色配置才可以标为重点；不能根据“老师”等昵称推断已核实的导师身份。

电脑“手机接入”页提供一个默认关闭的选择：**当应用没有提供消息时刻时，是否允许用通知发布时间估算“明天”“这周”等时间并自动安排提醒**。默认不这样推算：消息仍保存和分类，相关时间事项待用户确认。明确勾选后，单条普通通知才可把`posted_at`作为近似基准，界面仍标明这是通知时刻；这不能把它变成真实发送时间。已有MessagingStyle消息时间戳时使用来源应用提供的时刻；纯聚合摘要即使勾选也不自动建时间提醒。需要准确原消息时间时，应补充原文与时间或使用提供该字段的接入来源。

有MessagingStyle消息时，优先取最新一条文本；只有其中确实提供消息时间戳才传`message_sent_at`，后端据此标记来源消息时间，而不是自行编造。只有折叠多行聚合摘要时标记`aggregate`，后端可保存与分类，但默认不自动建提醒，需用户确认，避免把更新通知里夹带的历史文本重复当成新事项。各应用未必采用MessagingStyle，此能力尚未真机验证。

通知里出现文件名，只能作为附件线索；本应用不会访问微信/QQ聊天目录、不调用通知点击操作、不自动打开或下载附件。真实附件可以由QQ邮箱IMAP或用户主动上传等独立路径取得，再进入现有文档分类与RAG链路。

## 官方依据与构建来源

以下资料于2026-10-17核查，范围为Android平台官方API与SDK仓库，不代表各厂商应用已实测兼容：

- [NotificationListenerService](https://developer.android.com/reference/android/service/notification/NotificationListenerService)：由用户授权的通知监听服务，接收系统发布/移除通知的回调。
- [StatusBarNotification](https://developer.android.com/reference/android/service/notification/StatusBarNotification)：通知键、包名和系统发布时刻的来源。
- [Notification extras](https://developer.android.com/reference/android/app/Notification)：标题、主文本、展开文本及多行文本来自应用提供给系统的字段。
- [MessagingStyle.Message](https://developer.android.com/reference/android/app/Notification.MessagingStyle.Message)：消息文本与来源消息时间戳；公开数组解析方法从API30开始可用。
- [JobInfo.Builder](https://developer.android.com/reference/android/app/job/JobInfo.Builder)：联网条件与周期任务由系统调度，不是精确计时器。
- [Android15通知内容保护](https://developer.android.com/about/versions/15/behavior-changes-all#otp-redaction)：OTP等敏感通知内容可能被系统删减，不能保证读取原始全文。
- [Android SDK官方工具](https://developer.android.com/studio#command-tools)及[仓库清单](https://dl.google.com/android/repository/repository2-1.xml)：本地构建下载来源。具体归档URL、体积和校验值见`local-data/android-sdk-tooling/official-downloads.json`。

APK签名、文件SHA256和未实机标记见[`build_result.json`](../android-notification-bridge/dist/build_result.json)。开发签名不代表已在应用商店发布，也不等同于手机安装或后台行为验收。
