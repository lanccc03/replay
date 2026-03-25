# 项目 Agent 指南

本文件面向在本仓库中执行任务的工程代理，目标是减少误改、重复探索和与项目边界冲突的实现。

## 1. 项目定位

- 项目类型：Windows 多总线 Replay / 诊断桌面工具
- 技术栈：Python、PySide6、SQLite、python-can、cantools
- 当前重点：ZLG 设备上的 CAN / CANFD / J1939 / DoIP 场景回放与诊断
- 当前 UI 语言：中文

## 2. 先读哪里

开始改动前，优先读这些文件：

- `README.md`
- `src/replay_platform/core.py`
- `src/replay_platform/app_controller.py`
- `src/replay_platform/runtime/engine.py`
- `src/replay_platform/ui/main_window.py`

如果任务与具体能力相关，再按主题继续读：

- Trace 导入/缓存：`src/replay_platform/services/library.py`、`src/replay_platform/services/trace_loader.py`
- 信号覆盖 / DBC：`src/replay_platform/services/signal_catalog.py`
- ZLG 设备：`src/replay_platform/adapters/zlg.py`
- CAN UDS / DoIP / DTC：`src/replay_platform/diagnostics/`
- 录制：`src/replay_platform/runtime/recorder.py`

## 3. 目录职责

- `src/replay_platform/core.py`
  定义核心数据契约。任何场景结构、时间轴事件、诊断对象的修改，都要先看这里。
- `src/replay_platform/app_controller.py`
  UI 与底层能力的编排入口。不要把复杂业务逻辑堆到界面层。
- `src/replay_platform/runtime/`
  回放状态机、统一时间轴、录制逻辑。
- `src/replay_platform/services/`
  文件库、场景库、信号编解码、数据库绑定。
- `src/replay_platform/adapters/`
  设备抽象层。ZLG 是已实现路径，同星当前只有占位接口。
- `src/replay_platform/diagnostics/`
  CAN UDS、DoIP、DTC 解析。
- `src/replay_platform/ui/`
  Qt 桌面界面。当前主界面和二级场景编辑器都在 `main_window.py`。
- `tests/`
  单元测试。改动解析、场景结构、运行时逻辑时必须补测试。

## 4. 当前关键边界

- ZLG 真实硬件能力只能在 Windows 上验证。
- 当前机器如果不是 Windows，允许做结构开发、单元测试和语法检查，但不能声称已完成硬件联调。
- ETH 在 V1 中主要指 DoIP 诊断链路，不是通用原始以太网帧回放。
- 在线信号改值依赖 DBC / J1939 DBC；没有数据库绑定时，不支持信号级编辑。
- 同星适配器当前是占位接口，不要伪造“已支持同星硬件”。
- 场景结构必须兼容 `ScenarioSpec.from_dict()` / `to_dict()`。
- 当前产品界面是中文；新增 UI 文案默认保持中文。

## 5. Agent 改动原则

- 先沿现有架构扩展，不要平白再造一层抽象。
- 涉及场景结构变更时，先确认 `core.py`、UI、持久化、运行时三处是否都要同步。
- 涉及回放时序的改动，重点检查：
  - `pause / resume`
  - `trace_file_ids` 为空时的回退逻辑
  - 链路断开 / 恢复后是否错误补发过期帧
- 涉及 UI 改动时，不要把业务判断塞进控件槽函数里；优先抽成小函数。
- 只有在确有必要时才修改 `zlgcan_python_251211/` 下的 SDK 封装。
- 不要提交或依赖 `__pycache__` 内容。

## 6. 常用命令

安装开发依赖：

```bash
python -m pip install -e .[dev]
```

启动程序：

```bash
python -m replay_platform
```

运行单元测试：

```bash
PYTHONPYCACHEPREFIX=/tmp/replay-pyc python3 -m unittest discover -s tests -v
```

做语法编译检查：

```bash
PYTHONPYCACHEPREFIX=/tmp/replay-pyc python3 -m compileall src tests
```

## 7. 改动后最低验证要求

### 7.1 纯文档改动

- 检查路径、模块名、命令是否与仓库一致

### 7.2 纯 UI 改动

- `python -m compileall src tests`
- 如果改动涉及表单解析或场景编辑逻辑，补或更新 `tests/test_ui_helpers.py`

### 7.3 运行时 / 解析 / 场景结构改动

- 运行全部 `unittest`
- 补对应模块测试
- 明确说明是否未做 Qt 手工点击验证 / Windows 硬件验证

## 8. 场景编辑器特别说明

- 当前主窗口只做：
  - 文件库
  - 场景列表
  - 当前场景摘要
  - 回放控制
  - 手动信号覆盖
  - 日志查看
- 场景编辑器在二级窗口中，负责：
  - 表单编辑
  - JSON 编辑
  - 场景保存
  - 场景内容与主窗口摘要同步
- 若修改场景编辑器，请同步检查：
  - 二级窗口导出的场景是否仍能直接 `ScenarioSpec.from_dict()`
  - 主窗口开始回放时是否读取到最新场景

## 9. 测试文件提示

- `tests/test_engine.py`
  回放引擎启停、暂停、链路动作
- `tests/test_library.py`
  trace 导入与场景保存
- `tests/test_trace_loader.py`
  ASC 导入
- `tests/test_signal_catalog.py`
  信号覆盖
- `tests/test_dtc.py`
  DTC 解析
- `tests/test_ui_helpers.py`
  场景编辑表单的解析辅助函数

## 10. 提交前自检清单

- 是否破坏了 `ScenarioSpec` 的 JSON 兼容性
- 是否新增了英文 UI 文案
- 是否把 Windows 专属能力误写成跨平台可用
- 是否修改了 ZLG / DoIP 行为但没有补测试或说明限制
- 是否在最终说明中明确了“已验证”和“未验证”的边界

