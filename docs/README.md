# 文档导航

本目录承接项目的专题说明；`README.md` 只保留项目入口信息，`AGENTS.md` 只保留工程代理必须先知道的执行约束。

建议阅读顺序：

1. 先读仓库根目录的 [`README.md`](../README.md)
2. 再看 [`architecture.md`](./architecture.md) 理解整体分层
3. 按任务主题继续阅读对应专题文档

专题文档：

- [`architecture.md`](./architecture.md)：项目分层、统一时间轴、核心模块职责、扩展约束
- [`scenario-and-trace.md`](./scenario-and-trace.md)：场景 JSON 结构、trace 导入缓存、信号覆盖、运行数据目录
- [`diagnostics.md`](./diagnostics.md)：CAN UDS、DoIP、DTC、ZLG 原始 UDS 导出
- [`zlg-hardware.md`](./zlg-hardware.md)：Windows / ZLG 环境准备、已知限制、联调顺序
- [`testing.md`](./testing.md)：常用命令、测试映射、最低验证要求

如果你是工程代理或自动化工具，请优先阅读 [`../AGENTS.md`](../AGENTS.md)。
