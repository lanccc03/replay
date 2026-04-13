# 文档导航

本目录只放长期专题说明；仓库入口与执行约束分工如下：

- [`README.md`](../README.md)：项目定位、环境、启动方式、目录概览
- [`agents.md`](../agents.md)：工程代理的阅读顺序、硬边界、验证与交付要求
- `docs/`：按主题拆分的长期说明，不重复维护执行清单

建议阅读顺序：

1. 先读 [`README.md`](../README.md)
2. 如果要改代码或文档，再读 [`agents.md`](../agents.md)
3. 再看 [`architecture.md`](./architecture.md) 理解整体分层
4. 按任务主题继续阅读对应专题文档

专题文档：

- [`architecture.md`](./architecture.md)：项目分层、统一时间轴、核心模块职责、扩展约束
- [`scenario-and-trace.md`](./scenario-and-trace.md)：场景 JSON 结构、trace 导入缓存、信号覆盖、运行数据目录
- [`diagnostics.md`](./diagnostics.md)：CAN UDS、DoIP、DTC、ZLG 原始 UDS 导出
- [`windows-hardware.md`](./windows-hardware.md)：Windows / ZLG / 同星 环境准备、发送路径注意事项、已知限制、联调顺序
- [`testing.md`](./testing.md)：验证命令、测试映射、最低验证要求与验证边界
- [`text-encoding.md`](./text-encoding.md)：Windows / PowerShell 下中文源码乱码的排查顺序、判断标准与预防约定
