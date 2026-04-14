# 启动同步改为每通道固定同步帧

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

本次改动把启动同步从“拿 trace 的首个启用帧去做同步发送”改成“回放启动时，给每个已启动的可发帧通道发送一帧固定同步帧”。同步帧固定为 `ID=0x1`、`DLC=8`、`DATA=00 00 00 00 00 00 00 00`。用户可见效果是：启动后先完成各通道统一打点，再按 trace 的原始 `ts_ns` 相对这个新起点发送真实回放帧；真实首帧不再被拿去做同步，也不会再因为旧的首帧同步逻辑被拆走。

可观察结果是：支持 `sync_send` 的适配器会在启动和循环回放重启时，各通道先打一帧固定同步帧；真实 trace 帧继续按原时间轴发送；统计里的“已发帧”仍只统计真实回放帧，不把人工同步帧算进去。

## Progress

- [x] (2026-04-14 06:50Z) 重新阅读 `src/replay_platform/runtime/engine.py`、`tests/test_engine.py` 和现有 ExecPlan，确认旧实现仍是“首个启用帧同步发送 + 失败时强制当前批次 immediate fallback”。
- [x] (2026-04-14 06:52Z) 在 `ReplayEngine` 中删除旧的首帧同步路径与 `_startup_sync_force_immediate_batch` 状态，改成“启动后立即按通道发送固定同步帧”。
- [x] (2026-04-14 06:54Z) 增加按适配器/物理通道去重的同步目标收集逻辑，只覆盖 `CAN`、`CANFD`、`J1939` 且 `capabilities().sync_send=True` 的通道。
- [x] (2026-04-14 06:56Z) 调整 frame batch 的等待基准，改为等待当前批次“第一个真正启用的帧”的时间，避免批次头被禁用时把后续真实帧提前发送。
- [x] (2026-04-14 06:58Z) 更新 `tests/test_engine.py`，覆盖固定同步帧内容、延迟启动重锚点、多通道同步、禁用批次头、失败后保持真实批次原样、循环回放重复同步。
- [x] (2026-04-14 06:59Z) 运行 `python -m compileall src tests` 与 `python -m unittest discover -s tests -v`，确认通过。

## Surprises & Discoveries

- Observation: 把启动同步改成“固定同步帧”后，旧的批次等待逻辑会暴露一个已有时序问题：如果批次头帧被 runtime frame-enable 禁用，后面的真实启用帧会被按批次头时间提前发送。
  Evidence: 新增的“禁用批次头”测试在修改前无法满足“同步帧后约 1ms 才发真实帧”的断言。

- Observation: 启动同步失败后，真实首批帧的“正常路径”不一定是 immediate send；如果适配器本身支持 `queue_send` 且时间窗口满足条件，真实批次应该继续走 scheduled send。
  Evidence: 新的失败回归测试在 Mock 适配器上得到的是 `scheduled_batches=[0x100, 0x101]`，这与新的产品语义一致，也证明旧的 immediate fallback 标志不该保留。

## Decision Log

- Decision: 固定同步帧只对声明 `sync_send=True` 的适配器生效，不对普通 `send()` 适配器额外模拟同步发送。
  Rationale: 用户明确要求保留现有能力边界，不把这次改动扩散成所有适配器的通用启动前置发送。
  Date/Author: 2026-04-14 / Codex

- Decision: “每个通道”按实际发送端点解释，同一适配器下同一物理通道只发一帧同步帧。
  Rationale: 这与场景里可能存在的多逻辑通道复用同一物理发送端点的情况兼容，避免重复打点。
  Date/Author: 2026-04-14 / Codex

- Decision: 启动同步帧不计入 `ReplayStats.sent_frames`，只通过单独日志反映。
  Rationale: UI 当前把 `sent_frames` 解释成真实回放帧数量，直接把人工同步帧计进去会改变现有统计口径。
  Date/Author: 2026-04-14 / Codex

- Decision: 启动同步失败后不再强制把当前真实批次 immediate send，而是直接回到普通时间轴和既有 scheduled-send 判定。
  Rationale: 需求要求“避免污染真实批次内容”，也要求真实 trace 继续按正常规则发送。
  Date/Author: 2026-04-14 / Codex

## Outcomes & Retrospective

本次实现已经完成。`ReplayEngine` 现在会在启动锚点绑定后，收集所有支持同步发送的已绑定物理通道，并给每个端点发送固定同步帧；只有这些同步帧发送完成后，才把完成时刻作为新的 `_base_perf_ns`，后续真实 trace 帧按原始 `ts_ns` 相对这个新起点发送。循环回放重启时会重复同样的流程。

旧的“首个启用帧同步发送”“首批 2ms batch 被拆成 sync + 剩余帧”和“同步失败强制 immediate fallback”语义都已经移除。自动化验证已经覆盖编译、引擎时序回归和全量 `unittest`。本次没有做 Qt 手工点击验证，也没有做 Windows 真机硬件验证。

## Context and Orientation

`src/replay_platform/runtime/engine.py` 是统一回放时间轴的核心模块。它负责准备绑定通道、维护 `_base_perf_ns` 时间基准，并在 `_run_loop()` 中按时间线调度 `FrameEvent`、`DiagnosticAction` 和 `LinkAction`。本次改动全部发生在这里，外部场景结构、UI 和适配器接口都没有新增字段。

`tests/test_engine.py` 是本次行为定义的主要自动化出口。里面的 `StartupSyncRecordingMockAdapter` 通过实现 `send_sync()` 和继承 Mock 适配器的 `queue_send` 能力，能够同时观察同步发送、普通发送和 scheduled-send 是否仍按预期工作。因为当前真实实现里具备 `sync_send=True` 的主要是同星路径，这组测试也等价于为未来真机联调提供一个稳定的结构验证基础。

本次改动不能破坏 `ScenarioSpec.from_dict()` / `to_dict()` 兼容性，也不能新增 UI 配置项或英文 UI 文案。同步帧必须是运行时内部行为，而不是新配置项。

## Plan of Work

首先，修改 `ReplayEngine` 的启动同步入口。旧实现是在 `_run_loop()` 中看到首个 frame batch 后，挑选第一个启用帧做 `send_sync()`，成功后重算 `_base_perf_ns`，失败后用 `_startup_sync_force_immediate_batch` 把当前真实批次强行改成 immediate send。新实现删除这一整套分支，改为在启动锚点绑定之后、真正处理时间线项之前，统一收集同步目标并逐个发送固定同步帧。

然后，补上新的同步目标收集与日志逻辑。同步目标来自场景绑定本身，而不是 trace 批次；它按 `(adapter_id, physical_channel)` 去重，并只覆盖 `CAN`、`CANFD`、`J1939`。同步帧通过新的 `_build_startup_sync_target()` 构造，消息内容固定，且不走 signal override、不受 frame enable 规则影响。同步成功后只写独立日志，不写普通逐帧日志，也不增加 `sent_frames` 统计。

最后，修正批次等待时刻和测试。由于真实发送应该跟“第一个启用帧”对齐，`_run_loop()` 里的等待目标改成 `_frame_dispatch_ts_ns()`，这样即使批次头被禁用，也不会把后面的真实帧提前。`tests/test_engine.py` 相应改成验证新语义：固定同步帧内容、延迟启动后的重锚点、多通道去重同步、同步失败后的普通时间轴行为，以及循环回放每圈重新同步。

## Concrete Steps

在仓库根目录 `C:\code\replay` 执行：

    python -m compileall src tests

本次实际结果：

    Listing 'src'...
    Compiling 'src\\replay_platform\\runtime\\engine.py'...
    Compiling 'tests\\test_engine.py'...

然后执行：

    python -m unittest discover -s tests -v

本次实际结果：

    Ran 136 tests in 1.851s
    OK (skipped=3)

## Validation and Acceptance

验收标准如下。第一，启动时发送的同步帧必须固定为 `ID=0x1`、8 字节全 0，且按实际发送端点每通道一帧。第二，真实 trace 帧必须继续按原始 `ts_ns` 相对同步完成时刻发送，真实首帧不能被拿去做同步。第三，首批 2ms 批次必须保留原有 scheduled-send 规则；同步失败时也必须回到真实批次的正常发送路径，而不是强制 immediate fallback。第四，循环回放进入下一圈时要重新发送同步帧。

自动化层面，本次已经通过 `tests/test_engine.py` 的新增回归和全量 `unittest`。未验证范围必须明确写出：没有做 Qt 手工点击验证，没有做 Windows / 同星 / ZLG 真机验证。

## Idempotence and Recovery

本次变更完全是代码和测试层面的可重复修改，不涉及数据迁移或场景文件改写。若后续再次调整启动同步行为，应先重新运行 `python -m unittest tests.test_engine -v` 快速验证，再跑全量 `python -m unittest discover -s tests -v`。如果出现时序回归，优先检查 `_run_loop()`、`_handle_startup_sync()` 和 `_frame_dispatch_ts_ns()` 三处是否仍然保持同一套时间基准。

## Artifacts and Notes

关键自动化证据：

    python -m unittest tests.test_engine -v
    # Ran 44 tests in 1.333s
    # OK

    python -m unittest discover -s tests -v
    # Ran 136 tests in 1.851s
    # OK (skipped=3)

补充说明：3 个跳过用例仍然来自 `tests/test_ui_dialog.py`，原因是本地未安装 `PySide6`，与本次运行时启动同步改动无直接关系。

## Interfaces and Dependencies

`src/replay_platform/runtime/engine.py` 在本次修改后必须满足以下接口和行为约束：

- 继续使用现有 `DeviceAdapter.send_sync(event, timeout_ms)` 单帧同步发送接口，不新增批量同步接口。
- 新增模块级常量 `STARTUP_SYNC_MESSAGE_ID`、`STARTUP_SYNC_DLC`、`STARTUP_SYNC_PAYLOAD` 和 `STARTUP_SYNC_BUS_TYPES`，用于定义固定同步帧。
- `ReplayEngine._handle_startup_sync()` 不再接收 frame batch 参数，而是在启动后按通道发送固定同步帧。
- `ReplayEngine._frame_dispatch_ts_ns()` 必须返回当前批次中首个启用帧的时间；若整个批次都被禁用，则回退到批次头时间。
- `ReplayEngine._startup_sync_targets()` 必须按 `(adapter_id, physical_channel)` 去重，只返回支持 `sync_send` 的帧总线通道。

`tests/test_engine.py` 在本次修改后必须继续拥有一个支持 `sync_send` 的测试适配器，并至少覆盖以下场景：延迟启动重锚点、多通道固定同步帧、批次头禁用时的时间保持、同步失败后的正常批次行为、循环回放再次同步。

## Revision Note

2026-04-14：新增了针对“每通道固定同步帧”任务的 ExecPlan，并回写了设计决策、测试覆盖和实际验证结果。
