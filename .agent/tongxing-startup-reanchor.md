# 同星首帧同步重锚点

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

本次改动让同星设备在回放开始时，用“首个真正发出去的帧”重新对齐运行时钟。用户得到的直接效果是：当同星设备用于回放时，启动阶段不再只按 Python 线程第一次进入循环的时间做锚定，而是先把首个真正发送的帧走同步发送，再用该帧发送完成后的时间重算基准时间。这样可以减少启动阶段因为 async 接口和线程调度带来的首拍偏差，同时保留现有同星后续 async 发送和现有场景结构不变。

可观察结果是：启动有延迟时，首帧之后的下一帧会更贴近源 trace 的相对间隔；原来会在首个 `2ms` slice 内一起下发的同星首批帧，会被拆成“首帧同步发送 + 剩余帧按新锚点重新调度”。

## Progress

- [x] (2026-04-13 15:10Z) 创建 ExecPlan 并记录本次实现范围。
- [x] (2026-04-13 15:14Z) 实现 `AdapterCapabilities.sync_send` 与 `DeviceAdapter.send_sync()` 钩子。
- [x] (2026-04-13 15:16Z) 为同星适配器补 `send_sync()` 并保持普通 `send()` 仍为 async。
- [x] (2026-04-13 15:19Z) 在 `ReplayEngine` 中实现启动同步、重锚点、首批拆分与失败回退。
- [x] (2026-04-13 15:20Z) 为引擎和同星适配器补单元测试。
- [x] (2026-04-13 15:22Z) 运行 `python -m compileall src tests` 与 `python -m unittest discover -s tests -v` 并确认通过。

## Surprises & Discoveries

- Observation: 启动同步失败后，如果测试适配器同时支持 `queue_send`，运行循环会再次命中 scheduled send，而不是当前批次的 immediate fallback。
  Evidence: 新增的失败回退测试最初没有得到 `send_batches=[0x100, 0x101]`，而是被排到 scheduled path。

- Observation: 只重算 `_base_perf_ns` 不够，必须同时把首个 `2ms` batch 只推进到“首个真正发送帧”为止，剩余帧才能按新锚点重新调度。
  Evidence: `test_startup_sync_splits_initial_2ms_batch` 依赖这一点，要求首帧走 sync、剩余帧单独进入下一轮发送。

## Decision Log

- Decision: 本次只做启动阶段首帧同步重锚点，不额外关闭同星后续所有 `2ms` 批量直发。
  Rationale: 这是当前用户确认的范围，可以先修正启动阶段偏差，同时把运行期行为变化控制在最小范围。
  Date/Author: 2026-04-13 / Codex

- Decision: `send_sync()` 只做单帧接口，不扩展成批量同步发送。
  Rationale: 引擎只需要一个首帧同步锚点，单帧接口更直接，适配器职责也更清晰。
  Date/Author: 2026-04-13 / Codex

- Decision: 启动同步失败时，引擎使用一次性的 `_startup_sync_force_immediate_batch` 标志强制当前批次走 immediate async send。
  Rationale: 这能满足“失败后回退当前批次”的预期，同时避免被已有 scheduled-send 逻辑再次接管。
  Date/Author: 2026-04-13 / Codex

## Outcomes & Retrospective

已完成同星首帧同步重锚点。现在 `ReplayEngine` 会在启动阶段对首个真正发送的 enabled frame 尝试同步发送；成功后按该帧完成时刻重算 `_base_perf_ns`，并只推进时间线到该帧位置，从而让原首批剩余帧按新锚点继续调度。同步失败时，当前批次会立即回退到 async path，不会卡住或误入 scheduled send。

本次没有修改场景 JSON、UI、ZLG 行为或同星运行期后续 batching 规则。自动化已覆盖编译、引擎时序与同星适配器封装，但仍未做 Qt 手工点击验证，也未做 Windows 同星真机验证。

## Context and Orientation

`src/replay_platform/runtime/engine.py` 负责统一回放时钟、按时间线调度帧、诊断动作和链路动作。它当前会在运行循环第一次进入时，通过 `_bind_start_anchor_if_needed()` 把 `_base_perf_ns` 设成当前 `perf_counter_ns()`，然后以后续 `target_ns = _base_perf_ns + item.ts_ns` 的方式安排所有事件。

`src/replay_platform/adapters/tongxing.py` 是同星 TSMaster 适配器。当前普通发送全部通过 `tsapp_transmit_can_async()` 或 `tsapp_transmit_canfd_async()` 完成，`close()` 时会尝试排空 TX buffer，但运行中不会等待设备真正发完。TSMaster 包装层还提供了 `tsapp_transmit_can_sync()` 和 `tsapp_transmit_canfd_sync()`，可以作为首帧同步发送能力的底层实现。

`tests/test_engine.py` 已覆盖 `ReplayEngine` 的 batching、scheduled send、启动延迟与循环回放；`tests/test_tongxing_adapter.py` 已覆盖同星适配器的 async 发送、close drain 和 receive FIFO。当前仓库里还没有“首帧同步重锚点”的引擎测试，也没有同星 `send_sync()` 的测试。

本次改动不能修改场景 JSON 结构，也不能新增 UI 配置项。所有用户可见变化都必须来自引擎内部调度和适配器发送路径。

## Plan of Work

先在 `src/replay_platform/core.py` 的 `AdapterCapabilities` 中增加 `sync_send` 能力位，再在 `src/replay_platform/adapters/base.py` 的 `DeviceAdapter` 增加默认 `send_sync()`。默认实现直接调用 `send([event])`，但引擎只会在 `capabilities().sync_send` 为 `True` 时尝试走它。

然后修改 `src/replay_platform/adapters/tongxing.py`。普通 `send()` 保持现状；新增 `send_sync()`，要求打开设备、确认通道已启动、构造和 async 相同的帧结构，再改为调用 `tsapp_transmit_can_sync()` 或 `tsapp_transmit_canfd_sync()`。如果底层返回非零，抛现有 `AdapterOperationError`。

最后修改 `src/replay_platform/runtime/engine.py`。增加一个启动同步状态位，在 `start()` 和循环回放重启时置为待处理，在 `stop()`、完成首帧同步、发现适配器不支持或同步失败后清掉。运行循环在处理 frame batch 时先检查是否需要做启动同步：找到当前 batch 中第一个真正 enabled 的 frame；如果它的适配器支持 `sync_send`，则等到该帧自己的目标时间点、单独同步发送、成功后按 `time.perf_counter_ns() - frame.ts_ns` 重算 `_base_perf_ns`，并只推进时间线到这个 frame 为止。这样原首批剩余帧会在下一轮循环按新锚点重新调度。同步失败时，记录错误并回退到当前整批异步发送路径。

测试部分分两块。`tests/test_tongxing_adapter.py` 要验证 `send_sync()` 的 CAN 成功、CANFD 成功和失败抛错，同时保证普通 `send()` 仍走 async。`tests/test_engine.py` 要新增一个支持 `sync_send` 的测试适配器，覆盖启动延迟下的重锚点、首个 `2ms` batch 拆分、首帧 disabled 时的候选选择，以及同步失败后回退到当前异步批发送路径。

## Concrete Steps

在仓库根目录 `C:\code\replay` 执行：

    python -m compileall src tests

预期输出包含：

    Compiling 'src\\replay_platform\\...'
    Compiling 'tests\\...'

然后执行：

    python -m unittest discover -s tests -v

预期输出中包含新增测试名，并以 `OK` 结束。

本次实际结果：

    python -m compileall src tests
    # 成功，包含 src\replay_platform\runtime\engine.py 与新增测试文件的编译输出

    python -m unittest discover -s tests -v
    # Ran 133 tests in 1.760s
    # OK (skipped=3)

## Validation and Acceptance

自动化验收必须证明四件事。第一，同星适配器的普通 `send()` 仍然只调用 async API。第二，同星 `send_sync()` 能命中 sync API，并在底层返回错误时抛出异常。第三，引擎在支持 `sync_send` 的适配器上会把首个真正发送帧单独同步发送，成功后将原首个 `2ms` batch 拆开。第四，启动同步失败时，引擎不会卡死，会退回当前异步路径，后续仍能完成回放。

Windows 真机与 Qt 手工点击验证不在本次自动化覆盖范围内，交付说明必须明确写出这两个边界未验证。

## Idempotence and Recovery

本次所有代码修改都应当是可重复执行的纯代码变更，不涉及迁移或数据清理。测试失败时，优先根据失败用例修正逻辑后重新运行相同命令。若新增状态位导致回放停不下来，回退检查应集中在 `start()`、`stop()`、`_restart_loop_playback()` 和运行循环里对该状态位的读写是否一致。

## Artifacts and Notes

关键自动化结果：

    python -m unittest tests.test_engine tests.test_tongxing_adapter -v
    # Ran 52 tests in 1.286s
    # OK

    python -m unittest discover -s tests -v
    # Ran 133 tests in 1.760s
    # OK (skipped=3)

补充说明：3 个跳过用例都来自 `tests/test_ui_dialog.py`，原因为本地未安装 `PySide6`，与本次同星回放时序改动无直接关系。

## Interfaces and Dependencies

在 `src/replay_platform/core.py` 中，`AdapterCapabilities` 结束时必须包含：

    sync_send: bool = False

在 `src/replay_platform/adapters/base.py` 中，`DeviceAdapter` 必须包含：

    def send_sync(self, event: FrameEvent, timeout_ms: int) -> int:
        return self.send([event])

在 `src/replay_platform/adapters/tongxing.py` 中，`TongxingDeviceAdapter.capabilities()` 必须返回 `sync_send=True`，且 `send_sync()` 必须最终调用 TSMaster 的 `tsapp_transmit_can_sync()` 或 `tsapp_transmit_canfd_sync()`。

在 `src/replay_platform/runtime/engine.py` 中，必须存在一个启动阶段专用的同步发送路径，满足以下约束：只尝试一次；只针对首个真正 enabled 的帧；成功后重算 `_base_perf_ns`；失败或不支持时清掉待处理状态并回退到当前行为；循环回放每一圈重新启用该路径。

## Revision Note

2026-04-13：完成实现并回写了实际决策、测试结果和验证边界。新增了 failure fallback 的一次性 immediate 标志，这是实现过程中为满足“当前批次回退”而补充的细节。
