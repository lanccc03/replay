# Windows 多总线 Replay / 诊断平台

面向 Windows 的多总线回放与诊断桌面工具，围绕 ZLG 设备覆盖 CAN、CANFD、J1939、DoIP 的统一回放、场景管理、信号在线改值和 DTC 诊断流程。

更详细的专题说明已经拆分到 [`docs/`](docs/README.md)；如果你是工程代理，请先读 [`agents.md`](agents.md)。

## 1. 项目定位与范围

当前仓库提供一版可运行的 MVP 骨架，重点覆盖：

- 多通道统一时间轴回放
- 回放启停、暂停、恢复
- Trace 文件导入、缓存、场景持久化
- DBC / J1939 DBC 信号覆盖
- ZLG 设备适配层
- CAN UDS、DoIP、DTC 诊断
- PySide6 中文桌面界面

当前边界：

- ETH 在 V1 中主要指 DoIP 诊断链路，不是通用原始以太网帧回放
- 在线信号改值依赖 DBC / J1939 DBC；未绑定数据库时只支持原始帧回放
- 同星适配器当前仍是占位接口
- ZLG 真实硬件能力只能在 Windows 上完成联调验证

## 2. 环境要求与启动

### 2.1 操作系统

- Windows 10 x64 / Windows 11 x64

如果当前机器不是 Windows，仍可进行结构开发、单元测试和语法检查，但不要把结果表述为已完成硬件联调。

### 2.2 Python

- 建议：Python 3.12
- 当前 `pyproject.toml` 最低要求：Python 3.9

### 2.3 安装

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
```

### 2.4 启动与测试

```powershell
python -m replay_platform
python -m unittest discover -s tests -v
```

## 3. 快速开始

### 3.1 无硬件验证

可以直接使用 Mock 场景：

- 启动程序
- 载入 `examples/mock_scenario.json`
- 导入一个 `ASC` 文件，或通过 UI 选择 trace
- 启动回放，验证 UI、场景管理、日志、暂停 / 恢复等能力

### 3.2 ZLG + DoIP 场景模板

可参考 `examples/zlg_canfdnet_doip_scenario.json`，并按实际环境修改：

- `device_type`
- `device_index`
- `network.ip`
- `network.work_port`
- `network.local_port`
- `database_bindings.path`
- `diagnostic_targets.host`

ZLG 真机接入前的准备与限制说明见 [`docs/zlg-hardware.md`](docs/zlg-hardware.md)。

## 4. 目录概览

```text
replay/
├── docs/                          项目专题文档
├── examples/                      示例场景
├── src/replay_platform/           主代码
│   ├── adapters/                  设备适配层
│   ├── diagnostics/               诊断与 DTC
│   ├── runtime/                   回放运行时
│   ├── services/                  文件库、场景库、信号服务
│   ├── ui/                        PySide6 界面
│   ├── app_controller.py          应用编排入口
│   ├── core.py                    核心类型定义
│   └── __main__.py                启动入口
├── tests/                         单元测试
└── zlgcan_python_251211/          ZLG SDK 及 DLL
```

## 5. 文档导航

详细专题说明见：

- [`docs/README.md`](docs/README.md)：文档导航与阅读顺序
- [`docs/architecture.md`](docs/architecture.md)：分层架构、核心模块职责、统一时间轴模型
- [`docs/scenario-and-trace.md`](docs/scenario-and-trace.md)：场景 JSON、trace 导入、信号覆盖、运行数据目录
- [`docs/diagnostics.md`](docs/diagnostics.md)：CAN UDS、DoIP、DTC、ZLG 原始 UDS 导出
- [`docs/zlg-hardware.md`](docs/zlg-hardware.md)：Windows / ZLG 环境准备、已知限制、联调顺序
- [`docs/testing.md`](docs/testing.md)：验证命令、测试映射、最低验证要求与验证边界

如果你是在本仓库中执行改动任务的工程代理，请额外阅读：

- [`agents.md`](agents.md)

## 6. 关键入口

主代码入口：

- `src/replay_platform/__main__.py`
- `src/replay_platform/app_controller.py`
- `src/replay_platform/runtime/engine.py`
- `src/replay_platform/adapters/zlg.py`
- `src/replay_platform/ui/main_window.py`

示例场景：

- `examples/mock_scenario.json`
- `examples/zlg_canfdnet_doip_scenario.json`
