# 架构总览

本文描述项目的分层职责、统一时间轴模型，以及扩展时需要优先遵守的结构约束。

## 1. 分层结构

项目当前分为 4 层：

1. UI 层：桌面界面、文件导入、场景编辑、回放控制、日志查看
2. 应用服务层：文件库、场景库、信号覆盖服务、应用控制器
3. 运行时层：统一逻辑时钟、时间轴调度、链路动作执行、回放状态机
4. 设备 / 诊断适配层：ZLG、Mock、同星占位、CAN UDS、DoIP、DTC 解析

推荐的职责边界：

- `ui/` 负责展示和交互，不堆复杂业务逻辑
- `app_controller.py` 负责 UI 与底层能力编排
- `runtime/` 负责统一回放状态机与时间轴
- `services/` 负责文件库、场景库、数据库绑定、信号编解码
- `adapters/` 负责硬件抽象与实际设备收发
- `diagnostics/` 负责 CAN UDS、DoIP、DTC 等诊断链路

## 2. 统一时间轴与核心数据契约

核心数据契约集中定义在 `src/replay_platform/core.py`。常见对象包括：

- `FrameEvent`
- `DiagnosticAction`
- `LinkAction`
- `SignalOverride`
- `ScenarioSpec`
- `UdsRequest`
- `UdsResponse`
- `DtcRecord`

统一时间轴事件模型包含三类事件：

- `FrameEvent`：总线帧事件
- `DiagnosticAction`：诊断动作，例如读 VIN、读 DTC、清 DTC
- `LinkAction`：链路断开 / 恢复动作

这些事件都运行在同一条逻辑时间轴上，因此一个场景可以同时表达：

- 某个时刻发送报文
- 某个时刻执行 DoIP 或 CAN 诊断
- 某个时刻注入断连
- 某个时刻恢复通道

## 3. 核心模块职责

重点模块如下：

- `src/replay_platform/core.py`
  数据契约中心；场景结构、时间轴事件、诊断对象的修改都应先从这里评估影响面。
- `src/replay_platform/app_controller.py`
  应用编排入口；负责调度 UI、场景、信号覆盖、运行时与适配器。
- `src/replay_platform/runtime/engine.py`
  统一回放引擎；负责构建时间轴、维护运行状态、调度帧发送 / 诊断动作 / 链路动作，以及暂停恢复时的时间基准重绑定。
- `src/replay_platform/services/library.py`
  trace 文件导入、本地缓存、SQLite 元数据索引、场景保存加载。
- `src/replay_platform/services/signal_catalog.py`
  DBC / J1939 DBC 绑定，发送前的 `decode -> patch -> encode`。
- `src/replay_platform/adapters/zlg.py`
  ZLG 设备加载、通道配置、收发、健康检查、底层 UDS DLL 导出入口。
- `src/replay_platform/ui/main_window.py`
  主窗口与二级场景编辑器所在位置；UI 文案默认保持中文。

## 4. 扩展约束

扩展或重构时优先遵守以下约束：

- 先沿现有架构扩展，不要无必要再造一层抽象。
- 涉及场景结构变更时，同时检查 `core.py`、UI、持久化、运行时是否都要同步。
- 任何场景变更都要继续兼容 `ScenarioSpec.from_dict()` / `to_dict()`。
- 涉及回放时序的改动，要重点验证 `pause / resume`、链路断开 / 恢复，以及事件在统一时间轴上的顺序。
- 涉及 UI 改动时，优先把业务判断抽成小函数，而不是堆在槽函数里。

更多主题说明见：

- [`scenario-and-trace.md`](./scenario-and-trace.md)
- [`diagnostics.md`](./diagnostics.md)
- [`testing.md`](./testing.md)
