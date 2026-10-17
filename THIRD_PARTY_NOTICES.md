# 上游与第三方声明

本项目基于[JoshuaC215/agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit)扩展，保留固定上游源码与原MIT许可证，并不声称从零实现整个工具包。

- 上游固定提交：`88ebfa1fadd1a207aa92115635abf7ed20d66a88`。
- 上游版权：Copyright (c) 2024 Joshua Carroll。
- 原许可证：[agent-service-toolkit/LICENSE](agent-service-toolkit/LICENSE)。
- 本项目扩展主要位于`agent-service-toolkit/src/student_assistant/`、根服务与界面入口、Android通知桥及对应测试文档。
- `agent-service-toolkit/src/service/__init__.py`含个人助理服务配置的条件分派。
- 来源记录：[docs/UPSTREAM.md](docs/UPSTREAM.md)。

Python、Java与Android相关依赖分别遵守其自身许可证；依赖名称与版本见根依赖文件及Android构建文档。BGE模型在运行时另行下载，模型权重、Android SDK、JDK及签名私钥不包含在公开仓库内。保留的上游MIT许可适用于对应上游代码，不自动替代其他依赖许可，也不代表本仓库新增代码已经另行授予相同许可。
