# 项目 Agent 指南

本文件只保留工程代理执行任务时必须先知道的约束。项目背景、架构细节和专题说明请看 `README.md` 与 `docs/`。

## 1. 开始前先读

- 必读：
  - `README.md`
  - `docs/architecture.md`
  - `src/replay_platform/core.py`
  - `src/replay_platform/app_controller.py`
  - `src/replay_platform/runtime/engine.py`
  - `src/replay_platform/ui/main_window.py`
- 按任务补读：
  - 场景 / trace / 信号覆盖：`docs/scenario-and-trace.md`
  - 诊断：`docs/diagnostics.md`
  - Windows / ZLG 边界：`docs/zlg-hardware.md`
  - 验证要求：`docs/testing.md`
  - Trace 导入：`src/replay_platform/services/library.py`、`src/replay_platform/services/trace_loader.py`
  - DBC / 信号覆盖：`src/replay_platform/services/signal_catalog.py`
  - ZLG 设备：`src/replay_platform/adapters/zlg.py`
  - 录制：`src/replay_platform/runtime/recorder.py`

## 2. 不要越界

- ZLG 真机能力只能在 Windows 上验证；非 Windows 只能做结构开发、单元测试和语法检查。
- V1 的 `ETH` 主要指 DoIP 诊断链路，不是通用原始以太网帧回放。
- 在线信号改值依赖 DBC / J1939 DBC；未绑定数据库时不要声称支持信号级编辑。
- 同星适配器当前仍是占位路径，不要伪造“已支持同星硬件”。
- 场景结构必须继续兼容 `ScenarioSpec.from_dict()` / `to_dict()`。
- 新增 UI 文案默认保持中文。
- `zlgcan_python_251211/` 只在确有必要时修改。
- 不要提交或依赖 `__pycache__` 内容。

## 3. 改动原则

- 先沿现有架构扩展，不平白增加新抽象层。
- 涉及场景结构时，同时检查 `core.py`、UI、持久化、运行时是否要同步。
- 涉及回放时序时，重点检查：
  - `pause / resume`
  - `trace_file_ids` 为空时的回退逻辑
  - 链路断开 / 恢复后是否错误补发过期帧
- 涉及 UI 改动时，把业务判断抽成小函数，不堆在槽函数里。
- 涉及场景编辑器时，确认导出结果仍能直接 `ScenarioSpec.from_dict()`，且主窗口开始回放时能拿到最新场景。

## 4. 验证与交付

- 详细命令和测试映射统一看 `docs/testing.md`。
- 纯文档改动：至少检查路径、模块名、命令与仓库一致。
- 纯 UI 改动：至少做 `python -m compileall src tests`；若涉及表单解析或场景编辑逻辑，补或更新 `tests/test_ui_helpers.py`。
- 运行时 / 解析 / 场景结构改动：运行全部 `unittest`，并补对应模块测试。
- 最终说明里必须写清楚：
  - 已验证了什么
  - 未验证什么
  - 是否未做 Qt 手工点击验证 / Windows 硬件验证

## 5. 交付前自检

- 是否破坏 `ScenarioSpec` 的 JSON 兼容性
- 是否新增英文 UI 文案
- 是否把 Windows 专属能力误写成跨平台可用
- 是否修改了 ZLG / DoIP 行为却没有补测试或说明限制
- 是否明确写出“已验证 / 未验证”的边界
