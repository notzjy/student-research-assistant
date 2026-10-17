# GitHub公开发布整理

更新：2026-10-17。

## 收尾增量发布（2026-10-17）

当前代码已补邮件积压下的新信优先处理；[最新Actions](https://github.com/notzjy/student-research-assistant/actions/runs/39026274490)实际通过350项Windows回归，以及Docker镜像构建、Compose双服务、APK下载和重启持久性验收。真实QQ附件与最近新信通道的检查结果见[收尾报告](results/closeout_status.json)。以下317文件/337项是初次发布节点。

## 初次发布状态

本地源码、公开版首页、架构图、使用说明、验收证据、离线回归工作流和简历项目介绍已整理。Git版本记录使用GitHub账号`notzjy`及GitHub noreply邮箱，不把私人联系邮箱写入新提交作者信息。

**已公开发布：** [https://github.com/notzjy/student-research-assistant](https://github.com/notzjy/student-research-assistant)，默认分支`main`。已通过GitHub连接器上传初版317个文件，完整源码提交为[`18c2ca6`](https://github.com/notzjy/student-research-assistant/commit/18c2ca6f0e4f53c6f34309df65f6eb6d2b64cadc)。远端源码树`a22d201021a0ad4974b1cb11ca8af9179d5bcd4e`与审核过的本地源码树完全一致；后续发布说明更新单独提交。

## 已完成的整理

- [首页](../README.md)突出产品能力、技术架构、启动方式、测试证据和待验边界；原首页保存在[开发历史](DEVELOPMENT_HISTORY.md)。
- [简历项目介绍](RESUME_PROJECT.md)沿用提供的PDF中日期与项目名、简介、技术方案、成果的段落结构。没有改动原PDF，没有把未验证能力写成已经上线。
- 上游固定快照、原MIT许可证与[第三方声明](../THIRD_PARTY_NOTICES.md)保留；新增代码没有擅自补写独立开源许可。
- [Git排除规则](../.gitignore)排除本机凭证、用户邮件、数据库、附件目录、缓存、日志与签名私钥；仅上传示例配置和合成验收材料。
- Android构建所对应的源码字节哈希与安装包哈希已复核；对应源码禁用Git换行转换，避免克隆后失配。
- 已添加[Windows离线回归工作流](../.github/workflows/tests.yml)，不需要模型或邮箱凭证；远端首次运行已通过：根264项＋教学脚本73项，共337项，依赖检查也通过。结果见[Actions记录](https://github.com/notzjy/student-research-assistant/actions/runs/39022303737)和[发布验收](results/github_publication.json)。

## 本轮检查

候选源码约4.3MB。敏感内容检查同时核对已知本机私密配置值和常见Token/私钥模式，仅记录文件与问题类型，不输出匹配内容；待提交文件未发现命中。本机详细检查记录留在被忽略的`local-data/publication_audit.json`，不上传原简历、真实账号或凭证。

本轮主要调整文档、Git配置与发布流程，保留此前337项回归及29项实际联调的证据，远端源码发布已完成；GitHub Actions实际运行已确认337项通过及依赖检查通过，详见公开运行记录。Android实机、QQ真实附件/新信与通知延迟仍待验；项目83%、个人学习400/1300暂缓的记录保持。
