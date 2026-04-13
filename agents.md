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
  - 复杂功能 / 大型重构 / 方案设计：`.agent/PLANS.md`
  - 场景 / trace / 信号覆盖：`docs/scenario-and-trace.md`
  - 诊断：`docs/diagnostics.md`
  - Windows / ZLG / 同星 硬件边界：`docs/windows-hardware.md`
  - 验证要求：`docs/testing.md`
  - Trace 导入：`src/replay_platform/services/library.py`、`src/replay_platform/services/trace_loader.py`
  - DBC / 信号覆盖：`src/replay_platform/services/signal_catalog.py`
  - ZLG 设备：`src/replay_platform/adapters/zlg.py`
  - 同星设备：`src/replay_platform/adapters/tongxing.py`
  - 录制：`src/replay_platform/runtime/recorder.py`

## 2. 不要越界

- ZLG / 同星 真机能力只能在 Windows 上验证；非 Windows 只能做结构开发、单元测试和语法检查。
- V1 的 `ETH` 主要指 DoIP 诊断链路，不是通用原始以太网帧回放。
- 在线信号改值依赖 DBC / J1939 DBC；未绑定数据库时不要声称支持信号级编辑。
- 同星适配器已经接入 TSMaster 路径；不要把 Windows 真机能力误写成跨平台已验证。
- 如果用户让你分析回放偏差或发送性能问题，先把以下结论当成已排查前提：切换到 `sync` 发送不行；调整 2ms 切片内的帧间隔也不行。除非拿到新的代码证据或 Windows 真机证据，否则不要重复把这两个方向当主方案。
- 场景结构必须继续兼容 `ScenarioSpec.from_dict()` / `to_dict()`。
- 新增 UI 文案默认保持中文。
- `zlgcan_python_251211/` 与 `TSMasterApi/` 只在确有必要时修改。
- 不要提交或依赖 `__pycache__` 内容。

## 3. 改动原则

- 编写复杂功能或显著重构时，必须先按 `.agent/PLANS.md` 编写 ExecPlan，并从设计、原型验证、实现到交付全程维护。
- ExecPlan 必须保持自包含、可落地、可验证；执行过程中持续更新 `Progress`、`Surprises & Discoveries`、`Decision Log`、`Outcomes & Retrospective`。
- 如果需求存在较大未知或风险，先在 ExecPlan 中拆出原型 / 试验性里程碑验证可行性，再推进正式实现。
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
- 若任务采用 ExecPlan，交付前同步更新对应计划文档，确保计划中的进度、决策、验证结论与实际代码一致。
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
- 是否修改了 ZLG / 同星 / DoIP 行为却没有补测试或说明限制
- 是否明确写出“已验证 / 未验证”的边界
