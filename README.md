# Windows 多总线 Replay / 诊断平台

面向 Windows 的多总线回放与诊断桌面工具，支持围绕 ZLG 设备构建 CAN、CANFD、J1939、DoIP 的统一回放、场景管理、信号在线改值和 DTC 诊断流程。

当前仓库已经实现一版可运行的 MVP 工程骨架，重点覆盖：

- 多通道统一时间轴回放
- 回放启停、暂停、恢复
- Trace 文件导入、缓存、场景持久化
- DBC / J1939 DBC 信号覆盖
- ZLG 设备适配层
- CAN UDS 与 DoIP 诊断
- DTC 读取 / 清除 / 解析
- PySide6 桌面界面

## 1. 项目目标

本项目用于构建一个统一的总线回放与诊断平台，满足以下业务目标：

- 支持多路 CAN 和 ETH 的 Replay
- 支持 CAN、CANFD、J1939
- 支持周立功（ZLG）设备
- 为同星设备预留统一适配接口
- 支持按原始时间戳精准回放
- 支持启停、暂停、恢复
- 支持回放文件管理、场景管理
- 支持在线修改信号值，例如车速、档位、方向盘转角、制动、灯光等
- 支持 CAN / CANFD 断连恢复
- 支持 ETH 链路断开 / 重连
- 支持 DoIP 诊断与 DTC 解析

## 2. 当前实现范围

### 2.1 已实现

- 桌面应用框架：`PySide6`
- 领域模型与统一时间轴事件模型
- 回放运行时：支持 `start / pause / resume / stop / seek_to_start`
- ZLG 设备适配器：
  - `OpenDevice`
  - `InitCAN`
  - `StartCAN`
  - `ResetCAN`
  - `Receive / ReceiveFD / ReceiveData`
  - `ZCAN_SetValue / ZCAN_GetValue`
  - `DeviceOnLine`
  - DLL 原始导出 `ZCAN_UDS_Request / EX / Control / EX`
- CAN UDS 客户端
- DoIP TCP 诊断客户端
- DTC 状态位解析
- `ASC` 导入
- `BLF` 导入与录制接口
- SQLite 文件库 / 场景库
- 手动信号覆盖能力
- 场景 JSON 编辑与启动
- Mock 适配器和基础自动化测试

### 2.2 已预留但未完成

- 同星 SDK 具体接入
- 原始以太网帧抓包与回放
- ARXML / FIBEX
- 更完整的图形化场景编辑器
- 基于真实硬件能力的 ZLG 硬件 UDS 高级参数联调

### 2.3 重要边界

- V1 的 ETH 重点是 DoIP 诊断，不是通用原始以太网回放
- 在线信号编辑依赖 DBC / J1939 DBC
- 未绑定数据库的报文，只支持原始帧回放，不支持信号级改值
- 断连恢复策略为“不中断全局时间轴，不补发过期帧，不做追赶”

## 3. 技术方案概览

项目分为 4 层：

1. UI 层：桌面界面、文件导入、场景编辑、回放控制、日志查看
2. 应用服务层：文件库、场景库、信号覆盖服务、应用控制器
3. 运行时层：统一逻辑时钟、时间轴调度、链路动作执行、回放状态机
4. 设备 / 诊断适配层：ZLG、Mock、Tongxing 占位、CAN UDS、DoIP、DTC 解析

统一时间轴事件模型包括三类：

- `FrameEvent`：总线帧事件
- `DiagnosticAction`：诊断动作，例如读 VIN、读 DTC、清 DTC
- `LinkAction`：链路断开 / 重连动作

这三类事件都运行在同一条逻辑时间轴上，因此可以在一个场景中同时表达：

- 某个时刻发送报文
- 某个时刻执行 DoIP 诊断
- 某个时刻注入断连
- 某个时刻恢复通道

## 4. 目录结构

```text
replay/
├── examples/                      示例场景
├── src/replay_platform/           主代码
│   ├── adapters/                  设备适配层
│   ├── diagnostics/               诊断与 DTC
│   ├── runtime/                   回放运行时
│   ├── services/                  文件库/场景库/信号服务
│   ├── ui/                        PySide6 界面
│   ├── app_controller.py          应用编排入口
│   ├── core.py                    核心类型定义
│   └── __main__.py                启动入口
├── tests/                         单元测试
└── zlgcan_python_251211/          ZLG SDK 及 DLL
```

## 5. 核心模块说明

### 5.1 `core.py`

定义全局共用的领域对象：

- `BusType`
- `ReplayState`
- `AdapterCapabilities`
- `FrameEvent`
- `DiagnosticAction`
- `LinkAction`
- `SignalOverride`
- `ScenarioSpec`
- `UdsRequest / UdsResponse`
- `DtcRecord`

这是整套系统的数据契约中心。

### 5.2 `runtime/engine.py`

统一回放引擎，负责：

- 构建时间轴
- 维护运行状态
- 按目标时间戳调度事件
- 执行帧发送
- 执行诊断动作
- 执行断连 / 恢复动作
- 处理暂停和恢复时的时间基准重绑定

### 5.3 `adapters/zlg.py`

ZLG 适配器负责：

- 动态加载仓库内置 `zlgcan.py`
- 打开指定设备类型
- 配置通道波特率、终端电阻、回显、合并接收
- 启动 / 停止通道
- 发送 CAN / CANFD / J1939 帧
- 接收普通或合并接收报文
- 健康检查与重连
- 暴露底层 UDS 导出接口

### 5.4 `services/library.py`

文件库 / 场景库服务，负责：

- 导入 trace 文件到本地库
- 建立 SQLite 元数据索引
- 生成标准化缓存
- 保存 / 加载场景

### 5.5 `services/signal_catalog.py`

信号覆盖服务，负责：

- 绑定 DBC / J1939 DBC
- 识别可编辑信号
- 在发送前对报文执行：
  - `decode`
  - `patch`
  - `encode`

### 5.6 `diagnostics/can_uds.py`

通过适配器实现应用层 ISO-TP / UDS，适用于：

- 读 DID
- 读 DTC
- 清 DTC
- 后续扩展安全访问、下载、例程控制等

### 5.7 `diagnostics/doip.py`

直接使用 PC 网卡实现 DoIP：

- 建立 TCP 连接
- Routing Activation
- Alive Check
- UDS over DoIP 请求 / 响应

### 5.8 `diagnostics/dtc.py`

负责：

- 构造 `ReadDTCInformation`
- 构造 `ClearDiagnosticInformation`
- 解析 UDS DTC 响应
- 结合可选 CSV / JSON 字典输出更友好的描述

## 6. 环境要求

### 6.1 操作系统

- Windows 10 x64 / Windows 11 x64

当前项目的 ZLG SDK 加载方式是 Windows-only。

### 6.2 Python

- 建议：Python 3.12
- 当前 `pyproject.toml` 最低要求：Python 3.9

### 6.3 依赖

核心依赖：

- `PySide6`
- `python-can`
- `cantools`

开发依赖：

- `pytest` 或直接使用内置 `unittest`

## 7. 安装步骤

### 7.1 创建虚拟环境

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
```

### 7.2 安装项目

```powershell
python -m pip install -e .[dev]
```

### 7.3 启动程序

```powershell
python -m replay_platform
```

如果你只想运行测试：

```powershell
python -m unittest discover -s tests -v
```

## 8. ZLG 使用前准备

在接入真实硬件前，建议先用 ZLG 官方工具完成基础验证：

- `ZCANPRO`
- `ZXDOC`
- 网络类设备还可能需要先使用网络配置工具配置 IP / 端口 / 工作模式

建议流程：

1. 先在官方工具上确认设备工作正常
2. 确认设备型号与 `OpenDevice` 的类型一致
3. 确认波特率、采样点、终端电阻设置正确
4. CANFDNET / CANET 设备先确认 IP、目标端口、本地端口、客户端 / 服务器模式
5. 在官方工具中确认可以正常收发后，再用本项目接入

## 9. 快速开始

### 9.1 无硬件验证

可以直接使用 Mock 场景：

- 打开程序
- 载入 `examples/mock_scenario.json`
- 导入一个 `ASC` 文件或直接通过 UI 选择 trace
- 启动回放，验证 UI、场景管理、日志、暂停恢复等能力

### 9.2 ZLG + DoIP 场景模板

可以参考：

- `examples/zlg_canfdnet_doip_scenario.json`

注意先按实际环境修改：

- `device_type`
- `device_index`
- `network.ip`
- `network.work_port`
- `network.local_port`
- `database_bindings.path`
- `diagnostic_targets.host`

## 10. 场景文件说明

场景文件采用 JSON，核心字段如下：

### 10.1 `trace_file_ids`

表示已导入文件库中的 trace 记录 ID。

### 10.2 `bindings`

逻辑通道到物理设备通道的映射，典型字段：

- `adapter_id`：适配器实例名
- `driver`：`zlg` / `mock` / `tongxing`
- `logical_channel`
- `physical_channel`
- `bus_type`
- `device_type`
- `device_index`
- `sdk_root`
- `nominal_baud`
- `data_baud`
- `resistance_enabled`
- `listen_only`
- `tx_echo`
- `merge_receive`
- `network`

### 10.3 `database_bindings`

数据库绑定列表：

- `logical_channel`
- `path`
- `format`

### 10.4 `signal_overrides`

初始信号覆盖列表：

- `logical_channel`
- `message_id_or_pgn`
- `signal_name`
- `value`

### 10.5 `diagnostic_targets`

诊断目标配置，支持两类传输：

- `CAN`
- `DOIP`

CAN 典型字段：

- `adapter_id`
- `logical_channel`
- `tx_id`
- `rx_id`
- `timeout_ms`

DoIP 典型字段：

- `host`
- `port`
- `source_address`
- `target_address`
- `activation_type`
- `timeout_ms`

### 10.6 `diagnostic_actions`

统一时间轴上的诊断动作：

- `ts_ns`
- `target`
- `service_id`
- `payload`
- `transport`
- `timeout_ms`
- `description`

### 10.7 `link_actions`

统一时间轴上的链路动作：

- `ts_ns`
- `adapter_id`
- `action`
- `logical_channel`
- `description`

## 11. Trace 文件支持

### 11.1 已支持

- `ASC`
- `BLF`（依赖 `python-can`）

### 11.2 当前策略

- 导入时统一转换为内部 `FrameEvent`
- 同时写入本地缓存 JSON，提升后续加载速度
- SQLite 仅保存元数据，不直接保存大体量原始报文

### 11.3 录制输出

默认目标为：

- `BLF`

录制服务代码在：

- `src/replay_platform/runtime/recorder.py`

## 12. 信号在线修改

在线修改流程为：

1. 为逻辑通道绑定 DBC
2. 配置 `SignalOverride`
3. 回放发送前先用数据库解码
4. 修改指定信号值
5. 重新编码并下发

当前已支持：

- 通过场景 JSON 配置初始覆盖
- 通过 UI 手工追加覆盖项

后续可扩展方向：

- 常用信号快捷面板
- 批量滑块 / 拨杆控件
- 计数器 / 校验和自动修正

## 13. 诊断能力说明

### 13.1 CAN UDS

通过应用层 ISO-TP 实现，当前适合：

- 读 DID
- 读 DTC
- 清 DTC

优点：

- 不依赖尚未完全硬件验证的底层 UDS 函数签名
- 更容易调试

### 13.2 ZLG 原始 UDS 导出

已在仓库内的 `zlgcan.py` 中补充：

- `UDS_Request`
- `UDS_RequestEX`
- `UDS_Control`
- `UDS_ControlEX`

这部分已经打通 DLL 导出入口，后续可在真实硬件环境下继续细化参数结构与更高性能路径。

### 13.3 DoIP

当前 DoIP 支持：

- TCP 连接
- Routing Activation
- Alive Check
- Diagnostic Message
- UDS 正负响应解析

## 14. DTC 解析说明

当前 DTC 实现基于 UDS：

- `0x19 ReadDTCInformation`
- `0x14 ClearDiagnosticInformation`

已支持：

- 解析 DTC 编码
- 解析状态位
- 结合 JSON / CSV 字典补充描述

已内建的状态位解释包括：

- `test_failed`
- `pending_dtc`
- `confirmed_dtc`
- `warning_indicator_requested`

## 15. 回放时序说明

回放引擎使用统一逻辑时间轴：

- 每个事件都带 `ts_ns`
- 启动时记录 `perf_counter_ns()` 为基准
- 暂停时冻结逻辑时间
- 恢复时重新绑定基准时间

因此能保证：

- 帧事件、诊断动作、链路动作在同一时间体系下执行
- 暂停 / 恢复后时序继续保持一致

当前策略：

- 普通间隔用软件调度
- ZLG 适配层保留队列发送 / 时间戳相关能力入口，便于后续进一步利用硬件精度路径

## 16. 文件库与运行数据

程序运行后会在工作目录下生成：

```text
.replay_platform/
├── cache/          标准化缓存
├── databases/      预留数据库目录
├── traces/         导入后的 trace 文件副本
└── library.sqlite3 元数据库
```

作用说明：

- `traces/`：保存导入后的原始文件副本
- `cache/`：保存标准化帧缓存
- `library.sqlite3`：保存 trace 与 scenario 元信息

## 17. 测试

当前已包含的测试：

- `test_trace_loader.py`：`ASC` 导入解析
- `test_signal_catalog.py`：信号覆盖
- `test_library.py`：文件库 / 场景库
- `test_engine.py`：回放状态机、暂停恢复、链路动作
- `test_dtc.py`：DTC 解析

运行命令：

```powershell
python -m unittest discover -s tests -v
```

## 18. 当前已知限制

- 当前开发机不是 Windows，真实 UI 和硬件联调未在本机执行
- 仓库未自带 Python 依赖，需要自行安装
- ZLG 原始 UDS 导出已暴露，但高级调用参数仍建议在真实硬件环境下进一步校验
- `BLF` 和 `DBC` 能力依赖：
  - `python-can`
  - `cantools`
- 暂未实现原始 ETH 报文回放
- 暂未实现同星设备具体接入

## 19. 推荐联调顺序

建议按以下顺序推进：

1. 在 Windows 上安装依赖并启动 UI
2. 用 Mock 场景验证界面与回放控制
3. 导入 `ASC` 文件验证 trace 管理
4. 接入 ZLG USB 设备验证 CAN / CANFD 单通道收发
5. 接入 CANFDNET 验证网络类设备
6. 绑定 DBC 验证信号改值
7. 验证 CAN UDS
8. 验证 DoIP
9. 验证 DTC 读取 / 清除
10. 验证断连恢复

## 20. 后续建议

如果要把这版 MVP 继续做成可交付工具，下一阶段优先建议：

1. 增强场景编辑器，减少手改 JSON
2. 增加常用信号快捷控制面板
3. 对接真实 ZLG 硬件时间戳能力，完善精准回放路径
4. 增加录制 UI 和在线总线监视
5. 接入同星 SDK
6. 增加 OEM DTC 字典管理
7. 增加更细粒度的健康状态和重连策略

## 21. 相关文件

关键入口：

- `src/replay_platform/__main__.py`
- `src/replay_platform/app_controller.py`
- `src/replay_platform/runtime/engine.py`
- `src/replay_platform/adapters/zlg.py`
- `src/replay_platform/ui/main_window.py`

示例：

- `examples/mock_scenario.json`
- `examples/zlg_canfdnet_doip_scenario.json`

SDK：

- `zlgcan_python_251211/zlgcan.py`
- `zlgcan_python_251211/zlgcan.dll`

---

如果你准备继续推进，我建议下一步直接补两类内容：

- 一类是“Windows 真机部署文档”，把驱动、依赖、启动、常见错误写全
- 一类是“场景 JSON 可视化编辑器”，避免业务人员手工维护 JSON
