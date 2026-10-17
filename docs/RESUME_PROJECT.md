2026.10-至今　　　　　个人研究与日程智能助理

简介：个人实践项目，面向研究、学生生活与求职消息，整合QQ邮箱、手工消息与文件入口，完成重点联系人匹配、消息分类、时间地点提取、截止提醒与研究资料问答。

智能体方案：基于agent-service-toolkit扩展个人助理服务，采用LangGraph编排模型与工具，LangChain对接DeepSeek，以Function Calling完成事项查询和操作；通过thread_id与SQLite Checkpoint保存会话上下文。

检索与服务方案：采用RAG、BGE中文Embedding与Qdrant完成文档分块、向量检索和带出处问答；FastAPI＋Pydantic提供接口与结构化校验，SQLite保存业务及提醒队列，Streamlit提供交互界面，支持人工纠正、失败重试与Trace追踪。

成果：通过337项自动化测试及29项实际联调检查，验证真实模型调用、文档检索、消息修订、提醒重排与服务重启持久性；已交付安卓无线通知采集安装包，手机实机采集和QQ真实附件提醒闭环仍待验收。
