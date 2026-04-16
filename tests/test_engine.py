import threading
import time
import unittest
from typing import Optional

import tests.bootstrap  # noqa: F401

from replay_platform.adapters.base import DiagnosticClient
from replay_platform.adapters.mock import MockDeviceAdapter
from replay_platform.core import (
    AdapterCapabilities,
    AdapterHealth,
    BusType,
    DeviceChannelBinding,
    DiagnosticAction,
    FrameEnableRule,
    FrameEvent,
    LinkAction,
    LinkActionType,
    ReplayFrameLogMode,
    ReplayLaunchSource,
    ReplayLogConfig,
    ReplayLogLevel,
    ReplayState,
    ScenarioSpec,
    SignalOverride,
    UdsRequest,
    UdsResponse,
)
from replay_platform.runtime.engine import ReplayEngine
from replay_platform.services.frame_enable import FrameEnableService
from replay_platform.services.signal_catalog import (
    SignalOverrideService,
    StaticMessageCodec,
    StaticMessageDefinition,
)


class RecordingMockDeviceAdapter(MockDeviceAdapter):
    def __init__(self, adapter_id: str = "mock", channel_count: int = 4) -> None:
        super().__init__(adapter_id, channel_count=channel_count)
        self.send_batches: list[list[FrameEvent]] = []
        self.send_call_times_ns: list[int] = []
        self.scheduled_call_offsets_ns: list[int] = []

    def send(self, batch):
        self.send_call_times_ns.append(time.perf_counter_ns())
        self.send_batches.append([item.clone() for item in batch])
        return super().send(batch)

    def send_scheduled(self, batch, enqueue_base_ns):
        self.scheduled_call_offsets_ns.append(time.perf_counter_ns() - enqueue_base_ns)
        return super().send_scheduled(batch, enqueue_base_ns)


class StartupSyncRecordingMockAdapter(RecordingMockDeviceAdapter):
    def __init__(
        self,
        adapter_id: str = "mock",
        channel_count: int = 4,
        *,
        sync_failure: bool = False,
    ) -> None:
        super().__init__(adapter_id, channel_count=channel_count)
        self.sync_failure = sync_failure
        self.sync_frames: list[FrameEvent] = []
        self.sync_call_times_ns: list[int] = []
        self.sync_timeouts_ms: list[int] = []

    def send_sync(self, event, timeout_ms):
        self.sync_call_times_ns.append(time.perf_counter_ns())
        self.sync_timeouts_ms.append(int(timeout_ms))
        self.sync_frames.append(event.clone())
        if self.sync_failure:
            raise RuntimeError("startup-sync-failed")
        return MockDeviceAdapter.send(self, [event])

    def capabilities(self) -> AdapterCapabilities:
        capabilities = super().capabilities()
        capabilities.sync_send = True
        return capabilities


class PartialSendMockDeviceAdapter(RecordingMockDeviceAdapter):
    def __init__(self, adapter_id: str = "mock", channel_count: int = 4, sent_count: int = 0) -> None:
        super().__init__(adapter_id, channel_count=channel_count)
        self._sent_count = sent_count

    def send(self, batch):
        self.send_batches.append([item.clone() for item in batch])
        accepted = max(0, min(self._sent_count, len(batch)))
        self.sent_frames.extend(batch[:accepted])
        return accepted


class HealthCountingMockDeviceAdapter(MockDeviceAdapter):
    def __init__(self, adapter_id: str = "mock", channel_count: int = 4) -> None:
        super().__init__(adapter_id, channel_count=channel_count)
        self.health_calls = 0

    def health(self) -> AdapterHealth:
        self.health_calls += 1
        return super().health()


class CloseRecordingMockDeviceAdapter(MockDeviceAdapter):
    def __init__(self, adapter_id: str = "mock", channel_count: int = 4) -> None:
        super().__init__(adapter_id, channel_count=channel_count)
        self.close_threads: list[str] = []

    def close(self) -> None:
        self.close_threads.append(threading.current_thread().name)
        super().close()


class SlowDiagnosticClient(DiagnosticClient):
    def __init__(self, delay_s: float = 0.05) -> None:
        self.delay_s = delay_s
        self.started = threading.Event()
        self.requests: list[UdsRequest] = []

    def connect(self) -> None:
        return None

    def request(self, request: UdsRequest) -> UdsResponse:
        self.started.set()
        self.requests.append(request)
        time.sleep(self.delay_s)
        return UdsResponse(positive=True, service_id=request.service_id + 0x40, payload=b"\x00")

    def read_dtc(self) -> list[object]:
        return []

    def clear_dtc(self, group: int = 0xFFFFFF) -> UdsResponse:
        return UdsResponse(positive=True, service_id=0x54, payload=b"")

    def disconnect(self) -> None:
        return None

    def reconnect(self) -> None:
        return None


class ReplayEngineTests(unittest.TestCase):
    def _wait_for(
        self,
        predicate,
        *,
        timeout_s: float = 0.3,
        interval_s: float = 0.005,
        failure_message: str = "条件在预期时间内未满足。",
    ) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(interval_s)
        self.fail(failure_message)

    def _run_engine_to_completion(
        self,
        scenario: ScenarioSpec,
        frames: list[FrameEvent],
        adapters,
        timeout_s: float = 0.3,
        signal_overrides: Optional[SignalOverrideService] = None,
        frame_enables: Optional[FrameEnableService] = None,
        logger=None,
        diagnostics=None,
        log_config: Optional[ReplayLogConfig] = None,
    ) -> ReplayEngine:
        engine = ReplayEngine(
            signal_overrides=signal_overrides or SignalOverrideService(),
            frame_enables=frame_enables or FrameEnableService(),
            logger=logger,
            log_config=log_config,
        )
        engine.configure(scenario, frames, adapters, diagnostics or {})
        engine.start()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if engine.state == ReplayState.STOPPED:
                engine.finalize_completed_replay()
                return engine
            time.sleep(0.005)
        try:
            engine.stop()
        finally:
            self.fail("回放未在预期时间内结束。")

    @staticmethod
    def _delay_first_runtime_snapshot(engine: ReplayEngine, delay_s: float) -> None:
        original = engine._update_runtime_snapshot_for_item
        state = {"delayed": False}

        def delayed_update(item, timeline_index):
            if not state["delayed"]:
                state["delayed"] = True
                time.sleep(delay_s)
            return original(item, timeline_index)

        engine._update_runtime_snapshot_for_item = delayed_update  # type: ignore[assignment]

    def _assert_startup_sync_frame(
        self,
        frame: FrameEvent,
        *,
        physical_channel: int,
        bus_type: BusType,
    ) -> None:
        self.assertEqual(0, frame.ts_ns)
        self.assertEqual(bus_type, frame.bus_type)
        self.assertEqual(physical_channel, frame.channel)
        self.assertEqual(0x1, frame.message_id)
        self.assertEqual(b"\x00" * 8, frame.payload)
        self.assertEqual(8, frame.dlc)

    def test_start_only_opens_bound_device_channels(self):
        adapter = MockDeviceAdapter("mock-1", channel_count=4)
        scenario = ScenarioSpec(
            scenario_id="s-open-bound",
            name="Open bound channels",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=1,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(
                ts_ns=1_000_000_000,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x123,
                payload=b"\x01\x02\x03\x04\x05\x06\x07\x08",
                dlc=8,
            )
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {})
        engine.start()
        try:
            self.assertEqual({1}, set(adapter.health().per_channel))
        finally:
            engine.stop()

    def test_snapshot_defaults_to_empty_stopped_state(self):
        engine = ReplayEngine(signal_overrides=SignalOverrideService())

        snapshot = engine.snapshot()

        self.assertEqual(ReplayState.STOPPED, snapshot.state)
        self.assertEqual(0, snapshot.timeline_size)
        self.assertEqual("", snapshot.current_source_file)
        self.assertFalse(snapshot.loop_enabled)
        self.assertEqual(0, snapshot.completed_loops)

    def test_snapshot_reports_running_progress_and_source_file(self):
        adapter = MockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-snapshot-running",
            name="Snapshot running",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(
                ts_ns=50_000_000,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x123,
                payload=b"\x01",
                dlc=1,
                source_file="/tmp/body.asc",
            )
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(
            scenario,
            frames,
            {"mock-1": adapter},
            {},
            launch_source=ReplayLaunchSource.SCENARIO_BOUND,
        )
        engine.start()
        try:
            time.sleep(0.005)
            snapshot = engine.snapshot()
            self.assertEqual(ReplayState.RUNNING, snapshot.state)
            self.assertEqual(50_000_000, snapshot.current_ts_ns)
            self.assertEqual(50_000_000, snapshot.total_ts_ns)
            self.assertEqual(1, snapshot.timeline_size)
            self.assertEqual("body.asc", snapshot.current_source_file)
            self.assertIn("mock-1", snapshot.adapter_health)
            self.assertEqual(ReplayLaunchSource.SCENARIO_BOUND, snapshot.launch_source)
            self.assertFalse(snapshot.loop_enabled)
            self.assertEqual(0, snapshot.completed_loops)
        finally:
            engine.stop()

    def test_snapshot_updates_for_pause_completion_and_manual_stop(self):
        adapter = MockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-snapshot-states",
            name="Snapshot states",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(
                ts_ns=30_000_000,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x123,
                payload=b"\x01",
                dlc=1,
                source_file="/tmp/pause.asc",
            )
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {})
        engine.start()
        time.sleep(0.005)
        engine.pause()
        paused_snapshot = engine.snapshot()
        self.assertEqual(ReplayState.PAUSED, paused_snapshot.state)
        engine.stop()
        stopped_snapshot = engine.snapshot()
        self.assertEqual(ReplayState.STOPPED, stopped_snapshot.state)
        self.assertEqual(0, stopped_snapshot.timeline_index)
        self.assertEqual("", stopped_snapshot.current_source_file)

        completed_engine = self._run_engine_to_completion(
            scenario,
            [frames[0].clone(ts_ns=0, source_file="/tmp/done.asc")],
            {"mock-1": MockDeviceAdapter("mock-1", channel_count=1)},
        )
        completed_snapshot = completed_engine.snapshot()
        self.assertEqual(ReplayState.STOPPED, completed_snapshot.state)
        self.assertEqual(1, completed_snapshot.timeline_index)
        self.assertEqual("done.asc", completed_snapshot.current_source_file)
        self.assertFalse(completed_snapshot.loop_enabled)
        self.assertEqual(0, completed_snapshot.completed_loops)

    def test_runtime_snapshot_throttles_adapter_health_refresh_during_frame_progress(self):
        adapter = HealthCountingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-snapshot-health-throttle",
            name="Snapshot health throttle",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=1_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
            FrameEvent(ts_ns=2_000_000, bus_type=BusType.CAN, channel=0, message_id=0x102, payload=b"\x03", dlc=1),
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine._adapter_health_refresh_interval_ns = 1_000_000_000
        engine.configure(scenario, frames, {"mock-1": adapter}, {})
        engine.start()

        self._wait_for(
            lambda: engine.state == ReplayState.STOPPED,
            timeout_s=0.3,
            failure_message="adapter health throttling 用例未在预期时间内结束。",
        )
        engine.finalize_completed_replay()

        self.assertEqual(4, adapter.health_calls)

    def test_completed_replay_defers_adapter_close_until_finalization(self):
        adapter = CloseRecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-complete-finalize",
            name="Complete finalize",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(
                ts_ns=0,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x321,
                payload=b"\x01",
                dlc=1,
            )
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {})
        engine.start()

        self._wait_for(
            lambda: engine.state == ReplayState.STOPPED,
            timeout_s=0.2,
            failure_message="自然播放结束后未进入停止状态。",
        )

        self.assertTrue(engine.has_pending_completion_cleanup())
        self.assertEqual([], adapter.close_threads)

        self.assertTrue(engine.finalize_completed_replay())
        self.assertFalse(engine.has_pending_completion_cleanup())
        self.assertEqual([threading.current_thread().name], adapter.close_threads)
        self.assertNotIn("replay-engine", adapter.close_threads)
        self.assertFalse(engine.finalize_completed_replay())
        self.assertEqual([threading.current_thread().name], adapter.close_threads)

    def test_manual_stop_closes_adapters_without_pending_completion_cleanup(self):
        adapter = CloseRecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-manual-stop-finalize",
            name="Manual stop finalize",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(
                ts_ns=100_000_000,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x322,
                payload=b"\x01",
                dlc=1,
            )
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {})
        engine.start()
        time.sleep(0.005)

        engine.stop()

        self.assertFalse(engine.has_pending_completion_cleanup())
        self.assertEqual([threading.current_thread().name], adapter.close_threads)
        self.assertFalse(engine.finalize_completed_replay())
        self.assertEqual([threading.current_thread().name], adapter.close_threads)

    def test_loop_playback_restarts_timeline_and_accumulates_stats(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-loop-basic",
            name="Loop basic",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(
                ts_ns=0,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x120,
                payload=b"\x01",
                dlc=1,
            )
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {}, loop_enabled=True)
        engine.start()
        try:
            self._wait_for(
                lambda: engine.snapshot().completed_loops >= 1 and engine.stats.sent_frames >= 2,
                timeout_s=0.2,
                failure_message="循环回放未按预期进入下一圈。",
            )
            snapshot = engine.snapshot()
            self.assertEqual(ReplayState.RUNNING, snapshot.state)
            self.assertTrue(snapshot.loop_enabled)
            self.assertGreaterEqual(snapshot.completed_loops, 1)
            self.assertGreaterEqual(engine.stats.sent_frames, 2)
        finally:
            engine.stop()

    def test_loop_playback_keeps_disconnected_channel_state_after_link_disconnect(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-loop-link-reset",
            name="Loop link reset",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
            link_actions=[
                LinkAction(
                    ts_ns=5_000_000,
                    adapter_id="mock-1",
                    action=LinkActionType.DISCONNECT,
                    logical_channel=0,
                )
            ],
        )
        frames = [
            FrameEvent(
                ts_ns=0,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x121,
                payload=b"\x01",
                dlc=1,
            )
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {}, loop_enabled=True)
        engine.start()
        try:
            self._wait_for(
                lambda: engine.snapshot().completed_loops >= 1,
                timeout_s=0.25,
                failure_message="循环回放未在下一圈重新初始化通道。",
            )
            self.assertEqual(1, adapter.open_count)
            self.assertEqual({}, adapter.health().per_channel)
        finally:
            engine.stop()

    def test_runtime_frame_enable_rules_persist_across_loop_boundaries(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        frame_enables = FrameEnableService()
        scenario = ScenarioSpec(
            scenario_id="s-loop-frame-enable",
            name="Loop frame enable",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
            link_actions=[
                LinkAction(
                    ts_ns=50_000_000,
                    adapter_id="mock-1",
                    action=LinkActionType.RECONNECT,
                    logical_channel=0,
                )
            ],
        )
        frames = [
            FrameEvent(
                ts_ns=0,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x122,
                payload=b"\x01",
                dlc=1,
            )
        ]
        engine = ReplayEngine(
            signal_overrides=SignalOverrideService(),
            frame_enables=frame_enables,
        )
        engine.configure(scenario, frames, {"mock-1": adapter}, {}, loop_enabled=True)
        engine.start()
        try:
            self._wait_for(
                lambda: engine.stats.sent_frames >= 1,
                timeout_s=0.1,
                failure_message="首圈回放帧未发送。",
            )
            frame_enables.set_rule(FrameEnableRule(logical_channel=0, message_id=0x122, enabled=False))
            self._wait_for(
                lambda: engine.snapshot().completed_loops >= 1 and engine.stats.skipped_frames >= 1,
                timeout_s=0.2,
                failure_message="跨圈后未保留运行时帧禁用规则。",
            )
            self.assertEqual(1, engine.stats.sent_frames)
            self.assertGreaterEqual(engine.stats.skipped_frames, 1)
        finally:
            engine.stop()

    def test_loop_enabled_with_empty_timeline_stops_without_spinning(self):
        scenario = ScenarioSpec(scenario_id="s-loop-empty", name="Loop empty")
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, [], {}, {}, loop_enabled=True)
        engine.start()

        self._wait_for(
            lambda: engine.state == ReplayState.STOPPED,
            timeout_s=0.1,
            failure_message="空时间轴在循环模式下未按预期停止。",
        )

        snapshot = engine.snapshot()
        self.assertEqual(ReplayState.STOPPED, snapshot.state)
        self.assertTrue(snapshot.loop_enabled)
        self.assertEqual(0, snapshot.completed_loops)

    def test_conflicting_bindings_on_same_physical_channel_raise(self):
        adapter = MockDeviceAdapter("mock-1", channel_count=2)
        scenario = ScenarioSpec(
            scenario_id="s-conflict",
            name="Conflict",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    nominal_baud=500000,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=1,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    nominal_baud=250000,
                    device_type="MOCK",
                ),
            ],
        )
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, [], {"mock-1": adapter}, {})
        with self.assertRaisesRegex(Exception, "冲突"):
            engine.start()

    def test_matching_bindings_on_same_physical_channel_are_allowed(self):
        adapter = MockDeviceAdapter("mock-1", channel_count=2)
        scenario = ScenarioSpec(
            scenario_id="s-merge-bindings",
            name="Merge bindings",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    nominal_baud=500000,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=1,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    nominal_baud=500000,
                    device_type="MOCK",
                ),
            ],
        )
        self._run_engine_to_completion(scenario, [], {"mock-1": adapter}, timeout_s=0.1)
        self.assertEqual([], adapter.sent_frames)

    def test_file_mapped_bindings_on_same_physical_channel_raise_even_when_configs_match(self):
        adapter = MockDeviceAdapter("mock-1", channel_count=2)
        scenario = ScenarioSpec(
            scenario_id="s-file-map-conflict",
            name="File map conflict",
            bindings=[
                DeviceChannelBinding(
                    trace_file_id="trace-a",
                    source_channel=0,
                    source_bus_type=BusType.CANFD,
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CANFD,
                    nominal_baud=500000,
                    data_baud=2000000,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    trace_file_id="trace-b",
                    source_channel=1,
                    source_bus_type=BusType.CANFD,
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=1,
                    physical_channel=0,
                    bus_type=BusType.CANFD,
                    nominal_baud=500000,
                    data_baud=2000000,
                    device_type="MOCK",
                ),
            ],
        )
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, [], {"mock-1": adapter}, {})
        with self.assertRaisesRegex(Exception, "文件映射占用|占用"):
            engine.start()

    def test_frames_within_2ms_are_sent_in_one_batch(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-batch-merge",
            name="Batch merge",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=1_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]
        engine = self._run_engine_to_completion(scenario, frames, {"mock-1": adapter})
        self.assertEqual(1, len(adapter.send_batches))
        self.assertEqual([0x100, 0x101], [frame.message_id for frame in adapter.send_batches[0]])
        self.assertEqual(2, engine.stats.sent_frames)
        self.assertEqual(0, engine.stats.skipped_frames)

    def test_disabled_frame_id_is_skipped_during_batch_send(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        frame_enables = FrameEnableService()
        frame_enables.set_rule(FrameEnableRule(logical_channel=0, message_id=0x101, enabled=False))
        scenario = ScenarioSpec(
            scenario_id="s-frame-enable-skip",
            name="Frame enable skip",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=1_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]

        engine = self._run_engine_to_completion(
            scenario,
            frames,
            {"mock-1": adapter},
            frame_enables=frame_enables,
        )

        self.assertEqual(1, len(adapter.send_batches))
        self.assertEqual([0x100], [frame.message_id for frame in adapter.send_batches[0]])
        self.assertEqual(1, engine.stats.sent_frames)
        self.assertEqual(1, engine.stats.skipped_frames)

    def test_prepare_frame_groups_maps_enabled_frames_by_adapter_and_skips_disabled_frames(self):
        signal_overrides = SignalOverrideService()
        signal_overrides.bind_codec(
            0,
            StaticMessageCodec(
                {
                    0x100: StaticMessageDefinition(
                        name="Msg100",
                        signal_bytes={"speed": 0},
                    )
                }
            ),
        )
        signal_overrides.set_override(
            SignalOverride(
                logical_channel=0,
                message_id_or_pgn=0x100,
                signal_name="speed",
                value=0x7F,
            )
        )
        frame_enables = FrameEnableService()
        frame_enables.set_rule(FrameEnableRule(logical_channel=2, message_id=0x300, enabled=False))
        scenario = ScenarioSpec(
            scenario_id="s-prepare-groups",
            name="Prepare groups",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=2,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    adapter_id="mock-2",
                    driver="mock",
                    logical_channel=1,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    adapter_id="mock-2",
                    driver="mock",
                    logical_channel=2,
                    physical_channel=1,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=500_000, bus_type=BusType.CAN, channel=1, message_id=0x200, payload=b"\x02", dlc=1),
            FrameEvent(ts_ns=1_000_000, bus_type=BusType.CAN, channel=2, message_id=0x300, payload=b"\x03", dlc=1),
        ]
        engine = ReplayEngine(
            signal_overrides=signal_overrides,
            frame_enables=frame_enables,
        )
        engine.configure(
            scenario,
            frames,
            {
                "mock-1": MockDeviceAdapter("mock-1", channel_count=3),
                "mock-2": MockDeviceAdapter("mock-2", channel_count=2),
            },
            {},
        )

        groups = engine._prepare_frame_groups(frames)

        self.assertEqual({"mock-1", "mock-2"}, set(groups))
        self.assertEqual(1, len(groups["mock-1"]))
        self.assertEqual(1, len(groups["mock-2"]))

        prepared_a = groups["mock-1"][0]
        self.assertEqual(0, prepared_a.logical_channel)
        self.assertEqual(2, prepared_a.physical_channel)
        self.assertEqual(2, prepared_a.frame.channel)
        self.assertEqual(b"\x7F", prepared_a.frame.payload)

        prepared_b = groups["mock-2"][0]
        self.assertEqual(1, prepared_b.logical_channel)
        self.assertEqual(0, prepared_b.physical_channel)
        self.assertEqual(0, prepared_b.frame.channel)
        self.assertEqual(b"\x02", prepared_b.frame.payload)

        self.assertEqual(0, engine.stats.sent_frames)
        self.assertEqual(1, engine.stats.skipped_frames)

    def test_frames_at_2ms_boundary_are_split_into_two_batches(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-batch-boundary",
            name="Batch boundary",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=2_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]
        self._run_engine_to_completion(scenario, frames, {"mock-1": adapter})
        self.assertEqual(2, len(adapter.send_batches))
        self.assertEqual([1, 1], [len(batch) for batch in adapter.send_batches])

    def test_link_action_breaks_frame_batch(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-batch-link-break",
            name="Batch link break",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
            link_actions=[
                LinkAction(
                    ts_ns=500_000,
                    adapter_id="mock-1",
                    action=LinkActionType.RECONNECT,
                    logical_channel=0,
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=1_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]
        engine = self._run_engine_to_completion(scenario, frames, {"mock-1": adapter})
        self.assertEqual(2, len(adapter.send_batches))
        self.assertEqual([1, 1], [len(batch) for batch in adapter.send_batches])
        self.assertGreaterEqual(adapter.reconnect_count, 1)
        self.assertEqual(1, engine.stats.link_actions)

    def test_same_adapter_batch_keeps_original_order_across_channels(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=3)
        scenario = ScenarioSpec(
            scenario_id="s-batch-same-adapter",
            name="Batch same adapter",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=2,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=1,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x200, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=500_000, bus_type=BusType.CAN, channel=1, message_id=0x201, payload=b"\x02", dlc=1),
        ]
        self._run_engine_to_completion(scenario, frames, {"mock-1": adapter})
        self.assertEqual(1, len(adapter.send_batches))
        self.assertEqual([0x200, 0x201], [frame.message_id for frame in adapter.send_batches[0]])
        self.assertEqual([2, 0], [frame.channel for frame in adapter.send_batches[0]])

    def test_same_window_frames_are_grouped_per_adapter(self):
        adapter_a = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        adapter_b = RecordingMockDeviceAdapter("mock-2", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-batch-multi-adapter",
            name="Batch multi adapter",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    adapter_id="mock-2",
                    driver="mock",
                    logical_channel=1,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x300, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=500_000, bus_type=BusType.CAN, channel=1, message_id=0x301, payload=b"\x02", dlc=1),
        ]
        engine = self._run_engine_to_completion(
            scenario,
            frames,
            {"mock-1": adapter_a, "mock-2": adapter_b},
        )
        self.assertEqual(1, len(adapter_a.send_batches))
        self.assertEqual(1, len(adapter_b.send_batches))
        self.assertEqual([0x300], [frame.message_id for frame in adapter_a.send_batches[0]])
        self.assertEqual([0x301], [frame.message_id for frame in adapter_b.send_batches[0]])
        self.assertEqual(2, engine.stats.sent_frames)

    def test_same_message_id_on_other_channel_remains_enabled(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=2)
        frame_enables = FrameEnableService()
        frame_enables.set_rule(FrameEnableRule(logical_channel=0, message_id=0x200, enabled=False))
        scenario = ScenarioSpec(
            scenario_id="s-frame-enable-channel-scope",
            name="Frame enable channel scope",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=1,
                    physical_channel=1,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x200, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=500_000, bus_type=BusType.CAN, channel=1, message_id=0x200, payload=b"\x02", dlc=1),
        ]

        engine = self._run_engine_to_completion(
            scenario,
            frames,
            {"mock-1": adapter},
            frame_enables=frame_enables,
        )

        self.assertEqual(1, len(adapter.send_batches))
        self.assertEqual([0x200], [frame.message_id for frame in adapter.send_batches[0]])
        self.assertEqual([1], [frame.channel for frame in adapter.send_batches[0]])
        self.assertEqual(1, engine.stats.sent_frames)
        self.assertEqual(1, engine.stats.skipped_frames)

    def test_frame_logs_are_disabled_by_default(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-frame-logs-default-off",
            name="Frame logs default off",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        logs: list[str] = []
        self._run_engine_to_completion(
            scenario,
            [FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1)],
            {"mock-1": adapter},
            logger=logs.append,
        )
        self.assertNotIn("回放帧 [CAN]", "\n".join(logs))
        self.assertIn("回放已开始。", logs)

    def test_frame_logs_can_be_sampled_at_debug_level(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-frame-logs-sampled",
            name="Frame logs sampled",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=index * 3_000_000, bus_type=BusType.CAN, channel=0, message_id=0x100 + index, payload=b"\x01", dlc=1)
            for index in range(4)
        ]
        logs: list[str] = []
        self._run_engine_to_completion(
            scenario,
            frames,
            {"mock-1": adapter},
            logger=logs.append,
            log_config=ReplayLogConfig(
                level=ReplayLogLevel.DEBUG,
                frame_mode=ReplayFrameLogMode.SAMPLED,
                frame_sample_rate=2,
            ),
        )
        frame_logs = [entry for entry in logs if entry.startswith("回放帧 [CAN]")]
        self.assertEqual(2, len(frame_logs))
        self.assertIn("ID=0x101", frame_logs[0])
        self.assertIn("ID=0x103", frame_logs[1])

    def test_disabled_frame_logs_skip_formatter(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-frame-log-skip-formatter",
            name="Skip formatter",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        engine = ReplayEngine(
            signal_overrides=SignalOverrideService(),
            logger=lambda _message: None,
        )
        engine._format_sent_frame_log = lambda _item: (_ for _ in ()).throw(AssertionError("formatter called"))  # type: ignore[assignment]
        engine.configure(
            scenario,
            [FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1)],
            {"mock-1": adapter},
            {},
        )
        engine.start()
        deadline = time.time() + 0.1
        while time.time() < deadline and engine.state != ReplayState.STOPPED:
            time.sleep(0.005)
        self.assertEqual(ReplayState.STOPPED, engine.state)

    def test_replayed_frames_are_logged_for_supported_bus_types(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=3)
        scenario = ScenarioSpec(
            scenario_id="s-frame-logs",
            name="Frame logs",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=1,
                    physical_channel=1,
                    bus_type=BusType.CANFD,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=2,
                    physical_channel=2,
                    bus_type=BusType.J1939,
                    device_type="MOCK",
                ),
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=1_000_000, bus_type=BusType.CANFD, channel=1, message_id=0x101, payload=b"\x02\x03", dlc=2),
            FrameEvent(ts_ns=2_000_000, bus_type=BusType.J1939, channel=2, message_id=0x18FF50E5, payload=b"\x04\x05\x06", dlc=3),
        ]
        logs: list[str] = []

        self._run_engine_to_completion(
            scenario,
            frames,
            {"mock-1": adapter},
            logger=logs.append,
            log_config=ReplayLogConfig(level=ReplayLogLevel.DEBUG, frame_mode=ReplayFrameLogMode.ALL),
        )

        self.assertIn(
            "回放帧 [CAN] t=0.000ms 适配器=mock-1 逻辑通道=0 物理通道=0 ID=0x100 DLC=1 DATA=01",
            logs,
        )
        self.assertIn(
            "回放帧 [CANFD] t=1.000ms 适配器=mock-1 逻辑通道=1 物理通道=1 ID=0x101 DLC=2 DATA=0203",
            logs,
        )
        self.assertIn(
            "回放帧 [J1939] t=2.000ms 适配器=mock-1 逻辑通道=2 物理通道=2 ID=0x18FF50E5 DLC=3 DATA=040506",
            logs,
        )

    def test_frame_log_uses_overridden_payload(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-override-log",
            name="Override log",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(
                ts_ns=0,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x123,
                payload=b"\x01\x02",
                dlc=2,
            )
        ]
        signal_overrides = SignalOverrideService()
        signal_overrides.bind_codec(
            0,
            StaticMessageCodec(
                {
                    0x123: StaticMessageDefinition(
                        name="VehicleStatus",
                        signal_bytes={"vehicle_speed": 1},
                    )
                }
            ),
        )
        signal_overrides.set_override(
            SignalOverride(
                logical_channel=0,
                message_id_or_pgn=0x123,
                signal_name="vehicle_speed",
                value=0xAA,
            )
        )
        logs: list[str] = []

        self._run_engine_to_completion(
            scenario,
            frames,
            {"mock-1": adapter},
            signal_overrides=signal_overrides,
            logger=logs.append,
            log_config=ReplayLogConfig(level=ReplayLogLevel.DEBUG, frame_mode=ReplayFrameLogMode.ALL),
        )

        self.assertIn(
            "回放帧 [CAN] t=0.000ms 适配器=mock-1 逻辑通道=0 物理通道=0 ID=0x123 DLC=2 DATA=01AA",
            logs,
        )

    def test_partial_send_logs_only_sent_frames_and_warning(self):
        adapter = PartialSendMockDeviceAdapter("mock-1", channel_count=1, sent_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-partial-send-log",
            name="Partial send log",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x200, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=1_000_000, bus_type=BusType.CAN, channel=0, message_id=0x201, payload=b"\x02", dlc=1),
        ]
        logs: list[str] = []

        engine = self._run_engine_to_completion(
            scenario,
            frames,
            {"mock-1": adapter},
            logger=logs.append,
            log_config=ReplayLogConfig(level=ReplayLogLevel.DEBUG, frame_mode=ReplayFrameLogMode.ALL),
        )

        self.assertIn(
            "回放帧 [CAN] t=0.000ms 适配器=mock-1 逻辑通道=0 物理通道=0 ID=0x200 DLC=1 DATA=01",
            logs,
        )
        self.assertNotIn(
            "回放帧 [CAN] t=1.000ms 适配器=mock-1 逻辑通道=0 物理通道=0 ID=0x201 DLC=1 DATA=02",
            logs,
        )
        self.assertIn("回放帧发送未完成：适配器=mock-1 已发 1/2", logs)
        self.assertEqual(1, engine.stats.sent_frames)
        self.assertEqual(1, engine.stats.skipped_frames)

    def test_future_classic_can_batch_uses_scheduled_send(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-scheduled-send",
            name="Scheduled send",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=20_000_000, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=21_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]
        self._run_engine_to_completion(scenario, frames, {"mock-1": adapter})
        self.assertEqual(1, len(adapter.scheduled_batches))
        self.assertEqual([0x100, 0x101], [frame.message_id for frame in adapter.scheduled_batches[0]])
        self.assertGreaterEqual(adapter.scheduled_call_offsets_ns[0], 18_000_000)

    def test_startup_delay_keeps_original_frame_spacing(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-startup-anchor-spacing",
            name="Startup anchor spacing",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=100_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]
        logs: list[str] = []
        engine = ReplayEngine(
            signal_overrides=SignalOverrideService(),
            logger=logs.append,
            log_config=ReplayLogConfig(level=ReplayLogLevel.DEBUG),
        )
        engine.configure(scenario, frames, {"mock-1": adapter}, {})
        self._delay_first_runtime_snapshot(engine, 0.03)
        engine.start()

        self._wait_for(
            lambda: engine.state == ReplayState.STOPPED,
            timeout_s=0.5,
            failure_message="启动延迟回归用例未在预期时间内结束。",
        )
        engine.finalize_completed_replay()

        self.assertEqual(2, len(adapter.send_call_times_ns))
        send_gap_ns = adapter.send_call_times_ns[1] - adapter.send_call_times_ns[0]
        self.assertGreaterEqual(send_gap_ns, 85_000_000)
        self.assertLess(send_gap_ns, 160_000_000)
        self.assertTrue(any("回放启动延迟：" in entry for entry in logs))

    def test_startup_delay_still_allows_scheduled_send(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-scheduled-send-delayed-start",
            name="Scheduled send delayed start",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=20_000_000, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=21_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {})
        self._delay_first_runtime_snapshot(engine, 0.03)
        engine.start()

        self._wait_for(
            lambda: engine.state == ReplayState.STOPPED,
            timeout_s=0.3,
            failure_message="延迟启动的 scheduled send 用例未在预期时间内结束。",
        )
        engine.finalize_completed_replay()

        self.assertEqual(1, len(adapter.scheduled_batches))
        self.assertEqual([0x100, 0x101], [frame.message_id for frame in adapter.scheduled_batches[0]])
        self.assertGreaterEqual(adapter.scheduled_call_offsets_ns[0], 18_000_000)

    def test_startup_sync_sends_fixed_frame_and_reanchors_after_delayed_start(self):
        adapter = StartupSyncRecordingMockAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-startup-sync-reanchor",
            name="Startup sync reanchor",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=20_000_000, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=120_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {})
        self._delay_first_runtime_snapshot(engine, 0.03)
        engine.start()

        self._wait_for(
            lambda: engine.state == ReplayState.STOPPED,
            timeout_s=0.5,
            failure_message="startup sync 重锚点用例未在预期时间内结束。",
        )
        engine.finalize_completed_replay()

        self.assertEqual(1, len(adapter.sync_frames))
        self._assert_startup_sync_frame(adapter.sync_frames[0], physical_channel=0, bus_type=BusType.CAN)
        self.assertEqual([100], adapter.sync_timeouts_ms)
        self.assertEqual(2, len(adapter.send_batches))
        self.assertEqual([0x100], [frame.message_id for frame in adapter.send_batches[0]])
        self.assertEqual([0x101], [frame.message_id for frame in adapter.send_batches[1]])
        first_send_gap_ns = adapter.send_call_times_ns[0] - adapter.sync_call_times_ns[0]
        second_send_gap_ns = adapter.send_call_times_ns[1] - adapter.sync_call_times_ns[0]
        self.assertGreaterEqual(first_send_gap_ns, 15_000_000)
        self.assertLess(first_send_gap_ns, 80_000_000)
        self.assertGreaterEqual(second_send_gap_ns, 105_000_000)
        self.assertLess(second_send_gap_ns, 180_000_000)
        self.assertEqual(2, engine.stats.sent_frames)
        self.assertEqual(0, engine.stats.skipped_frames)

    def test_startup_sync_keeps_initial_2ms_batch_for_scheduled_send(self):
        adapter = StartupSyncRecordingMockAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-startup-sync-split-batch",
            name="Startup sync split batch",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=1_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]

        engine = self._run_engine_to_completion(scenario, frames, {"mock-1": adapter})

        self.assertEqual(1, len(adapter.sync_frames))
        self._assert_startup_sync_frame(adapter.sync_frames[0], physical_channel=0, bus_type=BusType.CAN)
        self.assertEqual([100], adapter.sync_timeouts_ms)
        self.assertEqual(1, len(adapter.scheduled_batches))
        self.assertEqual([0x100, 0x101], [frame.message_id for frame in adapter.scheduled_batches[0]])
        self.assertEqual(2, engine.stats.sent_frames)
        self.assertEqual(0, engine.stats.skipped_frames)

    def test_startup_sync_does_not_pull_forward_first_enabled_frame_when_batch_head_is_disabled(self):
        adapter = StartupSyncRecordingMockAdapter("mock-1", channel_count=1)
        frame_enables = FrameEnableService()
        frame_enables.set_rule(FrameEnableRule(logical_channel=0, message_id=0x100, enabled=False))
        scenario = ScenarioSpec(
            scenario_id="s-startup-sync-disabled-head",
            name="Startup sync disabled head",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=1_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]

        engine = self._run_engine_to_completion(
            scenario,
            frames,
            {"mock-1": adapter},
            frame_enables=frame_enables,
        )

        self.assertEqual(1, len(adapter.sync_frames))
        self._assert_startup_sync_frame(adapter.sync_frames[0], physical_channel=0, bus_type=BusType.CAN)
        self.assertEqual([], adapter.scheduled_batches)
        self.assertEqual(1, len(adapter.send_batches))
        self.assertEqual([0x101], [frame.message_id for frame in adapter.send_batches[0]])
        send_gap_ns = adapter.send_call_times_ns[0] - adapter.sync_call_times_ns[0]
        self.assertGreaterEqual(send_gap_ns, 500_000)
        self.assertLess(send_gap_ns, 60_000_000)
        self.assertEqual(1, engine.stats.sent_frames)
        self.assertEqual(1, engine.stats.skipped_frames)

    def test_startup_sync_failure_keeps_real_batch_on_normal_timeline(self):
        adapter = StartupSyncRecordingMockAdapter("mock-1", channel_count=1, sync_failure=True)
        scenario = ScenarioSpec(
            scenario_id="s-startup-sync-fallback",
            name="Startup sync fallback",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=20_000_000, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=21_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]
        logs: list[str] = []

        engine = self._run_engine_to_completion(
            scenario,
            frames,
            {"mock-1": adapter},
            logger=logs.append,
        )

        self.assertEqual(1, len(adapter.sync_frames))
        self._assert_startup_sync_frame(adapter.sync_frames[0], physical_channel=0, bus_type=BusType.CAN)
        self.assertEqual(1, len(adapter.scheduled_batches))
        self.assertEqual([0x100, 0x101], [frame.message_id for frame in adapter.scheduled_batches[0]])
        self.assertTrue(any("启动同步帧发送失败" in entry for entry in logs))
        self.assertTrue(any("startup-sync-failed" in entry for entry in logs))
        self.assertEqual(2, engine.stats.sent_frames)
        self.assertEqual(0, engine.stats.skipped_frames)

    def test_startup_sync_sends_one_fixed_frame_per_started_endpoint(self):
        adapter = StartupSyncRecordingMockAdapter("mock-1", channel_count=2)
        scenario = ScenarioSpec(
            scenario_id="s-startup-sync-per-endpoint",
            name="Startup sync per endpoint",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=1,
                    physical_channel=1,
                    bus_type=BusType.CANFD,
                    device_type="MOCK",
                ),
            ],
        )
        frames = [
            FrameEvent(ts_ns=10_000_000, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
        ]

        engine = self._run_engine_to_completion(scenario, frames, {"mock-1": adapter})

        self.assertEqual(2, len(adapter.sync_frames))
        self._assert_startup_sync_frame(adapter.sync_frames[0], physical_channel=0, bus_type=BusType.CAN)
        self._assert_startup_sync_frame(adapter.sync_frames[1], physical_channel=1, bus_type=BusType.CANFD)
        self.assertEqual([100, 100], adapter.sync_timeouts_ms)
        self.assertEqual(1, len(adapter.send_batches))
        self.assertEqual([0x100], [frame.message_id for frame in adapter.send_batches[0]])
        self.assertEqual(1, engine.stats.sent_frames)
        self.assertEqual(0, engine.stats.skipped_frames)

    def test_loop_playback_only_runs_startup_sync_once_per_continuous_session(self):
        adapter = StartupSyncRecordingMockAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-startup-sync-loop",
            name="Startup sync loop",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {}, loop_enabled=True)
        engine.start()
        try:
            self._wait_for(
                lambda: engine.snapshot().completed_loops >= 2,
                timeout_s=0.3,
                failure_message="循环回放未按预期持续进入下一圈。",
            )
            time.sleep(0.03)
        finally:
            engine.stop()

        self.assertEqual(1, len(adapter.sync_frames))
        self._assert_startup_sync_frame(adapter.sync_frames[0], physical_channel=0, bus_type=BusType.CAN)

    def test_manual_stop_then_start_reinitializes_channels_and_startup_sync_again(self):
        adapter = StartupSyncRecordingMockAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-startup-sync-stop-start",
            name="Startup sync stop start",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=1_000_000_000, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {})
        try:
            engine.start()
            self._wait_for(
                lambda: len(adapter.sync_frames) >= 1,
                timeout_s=0.2,
                failure_message="首次启动未按预期发送 startup sync。",
            )
            engine.stop()
            self.assertEqual(ReplayState.STOPPED, engine.state)

            engine.start()
            self._wait_for(
                lambda: len(adapter.sync_frames) >= 2 and adapter.open_count >= 2,
                timeout_s=0.2,
                failure_message="stop/start 后未重新初始化通道并发送 startup sync。",
            )
        finally:
            engine.stop()

        self.assertEqual(2, len(adapter.sync_frames))
        self._assert_startup_sync_frame(adapter.sync_frames[0], physical_channel=0, bus_type=BusType.CAN)
        self._assert_startup_sync_frame(adapter.sync_frames[1], physical_channel=0, bus_type=BusType.CAN)
        self.assertGreaterEqual(adapter.open_count, 2)

    def test_single_enabled_frame_in_batch_falls_back_to_immediate_send(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=2)
        frame_enables = FrameEnableService()
        frame_enables.set_rule(FrameEnableRule(logical_channel=1, message_id=0x101, enabled=False))
        scenario = ScenarioSpec(
            scenario_id="s-scheduled-frame-enable",
            name="Scheduled frame enable",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                ),
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=1,
                    physical_channel=1,
                    bus_type=BusType.CANFD,
                    device_type="MOCK",
                ),
            ],
        )
        frames = [
            FrameEvent(ts_ns=5_000_000, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=5_500_000, bus_type=BusType.CANFD, channel=1, message_id=0x101, payload=b"\x02", dlc=1),
        ]

        engine = self._run_engine_to_completion(
            scenario,
            frames,
            {"mock-1": adapter},
            frame_enables=frame_enables,
        )

        self.assertEqual([], adapter.scheduled_batches)
        self.assertEqual(1, len(adapter.send_batches))
        self.assertEqual([0x100], [frame.message_id for frame in adapter.send_batches[0]])
        self.assertEqual(1, engine.stats.sent_frames)
        self.assertEqual(1, engine.stats.skipped_frames)

    def test_link_action_prevents_scheduled_send_across_batches(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-scheduled-barrier",
            name="Scheduled barrier",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
            link_actions=[
                LinkAction(
                    ts_ns=20_500_000,
                    adapter_id="mock-1",
                    action=LinkActionType.RECONNECT,
                    logical_channel=0,
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=20_000_000, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=21_000_000, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
        ]
        engine = self._run_engine_to_completion(scenario, frames, {"mock-1": adapter})
        self.assertEqual([], adapter.scheduled_batches)
        self.assertEqual([[0x100], [0x101]], [[frame.message_id for frame in batch] for batch in adapter.send_batches])
        self.assertGreaterEqual(adapter.reconnect_count, 1)
        self.assertEqual(1, engine.stats.link_actions)

    def test_canfd_batch_falls_back_to_immediate_send(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        scenario = ScenarioSpec(
            scenario_id="s-scheduled-canfd-fallback",
            name="Scheduled fallback",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CANFD,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=5_000_000, bus_type=BusType.CANFD, channel=0, message_id=0x101, payload=b"\x02\x03", dlc=2),
        ]
        self._run_engine_to_completion(scenario, frames, {"mock-1": adapter})
        self.assertEqual([], adapter.scheduled_batches)
        self.assertEqual(1, len(adapter.send_batches))

    def test_slow_diagnostic_does_not_block_following_frames(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        diagnostic = SlowDiagnosticClient(delay_s=0.06)
        scenario = ScenarioSpec(
            scenario_id="s-async-diagnostic",
            name="Async diagnostic",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
            diagnostic_actions=[
                DiagnosticAction(ts_ns=0, target="ecu", service_id=0x10),
            ],
        )
        frames = [
            FrameEvent(ts_ns=10_000_000, bus_type=BusType.CAN, channel=0, message_id=0x123, payload=b"\x01", dlc=1),
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {"ecu": diagnostic})
        engine.start()
        self.assertTrue(diagnostic.started.wait(timeout=0.05))
        time.sleep(0.02)
        self.assertEqual(1, len(adapter.sent_frames))
        deadline = time.time() + 0.2
        while time.time() < deadline and engine.state != ReplayState.STOPPED:
            time.sleep(0.005)
        self.assertEqual(ReplayState.STOPPED, engine.state)
        self.assertEqual(1, engine.stats.diagnostic_actions)

    def test_stop_cancels_queued_diagnostics(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        diagnostic = SlowDiagnosticClient(delay_s=0.1)
        scenario = ScenarioSpec(
            scenario_id="s-cancel-diagnostic",
            name="Cancel diagnostic",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
            diagnostic_actions=[
                DiagnosticAction(ts_ns=0, target="ecu", service_id=0x10),
                DiagnosticAction(ts_ns=0, target="ecu", service_id=0x11),
            ],
        )
        logs: list[str] = []
        engine = ReplayEngine(signal_overrides=SignalOverrideService(), logger=logs.append)
        engine.configure(scenario, [], {"mock-1": adapter}, {"ecu": diagnostic})
        engine.start()
        self.assertTrue(diagnostic.started.wait(timeout=0.05))
        engine.stop()
        self.assertTrue(any("诊断队列已取消" in entry for entry in logs))

    def test_pause_resume_and_link_action(self):
        adapter = MockDeviceAdapter("mock-1")
        scenario = ScenarioSpec(
            scenario_id="s1",
            name="Runtime test",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
            link_actions=[
                LinkAction(
                    ts_ns=30_000_000,
                    adapter_id="mock-1",
                    action=LinkActionType.RECONNECT,
                    logical_channel=0,
                )
            ],
        )
        frames = [
            FrameEvent(
                ts_ns=0,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x123,
                payload=b"\x01\x02\x03\x04\x05\x06\x07\x08",
                dlc=8,
            ),
            FrameEvent(
                ts_ns=80_000_000,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x124,
                payload=b"\x10\x11\x12\x13\x14\x15\x16\x17",
                dlc=8,
            ),
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {})
        engine.start()
        time.sleep(0.02)
        engine.pause()
        sent_while_paused = len(adapter.sent_frames)
        time.sleep(0.10)
        self.assertEqual(sent_while_paused, len(adapter.sent_frames))
        engine.resume()
        time.sleep(0.15)
        self.assertEqual(2, len(adapter.sent_frames))
        self.assertGreaterEqual(adapter.reconnect_count, 1)
        self.assertEqual(ReplayState.STOPPED, engine.state)

    def test_pause_resume_and_stop_keep_working_in_loop_mode(self):
        adapter = MockDeviceAdapter("mock-1")
        scenario = ScenarioSpec(
            scenario_id="s-loop-pause-resume",
            name="Loop pause resume",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(
                ts_ns=0,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x123,
                payload=b"\x01\x02\x03\x04\x05\x06\x07\x08",
                dlc=8,
            ),
            FrameEvent(
                ts_ns=120_000_000,
                bus_type=BusType.CAN,
                channel=0,
                message_id=0x124,
                payload=b"\x10\x11\x12\x13\x14\x15\x16\x17",
                dlc=8,
            ),
        ]
        engine = ReplayEngine(signal_overrides=SignalOverrideService())
        engine.configure(scenario, frames, {"mock-1": adapter}, {}, loop_enabled=True)
        engine.start()
        time.sleep(0.02)
        engine.pause()
        sent_while_paused = len(adapter.sent_frames)
        time.sleep(0.08)
        self.assertEqual(sent_while_paused, len(adapter.sent_frames))
        engine.resume()
        self._wait_for(
            lambda: len(adapter.sent_frames) >= 2,
            timeout_s=0.25,
            failure_message="循环模式下恢复后未继续发送后续帧。",
        )
        engine.stop()
        self.assertEqual(ReplayState.STOPPED, engine.state)
        self.assertGreaterEqual(len(adapter.sent_frames), 2)

    def test_reenabled_frame_id_only_affects_future_frames(self):
        adapter = RecordingMockDeviceAdapter("mock-1", channel_count=1)
        frame_enables = FrameEnableService()
        frame_enables.set_rule(FrameEnableRule(logical_channel=0, message_id=0x300, enabled=False))
        scenario = ScenarioSpec(
            scenario_id="s-frame-enable-reenable",
            name="Frame enable reenable",
            bindings=[
                DeviceChannelBinding(
                    adapter_id="mock-1",
                    driver="mock",
                    logical_channel=0,
                    physical_channel=0,
                    bus_type=BusType.CAN,
                    device_type="MOCK",
                )
            ],
        )
        frames = [
            FrameEvent(ts_ns=0, bus_type=BusType.CAN, channel=0, message_id=0x300, payload=b"\x01", dlc=1),
            FrameEvent(ts_ns=60_000_000, bus_type=BusType.CAN, channel=0, message_id=0x300, payload=b"\x02", dlc=1),
        ]
        engine = ReplayEngine(
            signal_overrides=SignalOverrideService(),
            frame_enables=frame_enables,
        )
        engine.configure(scenario, frames, {"mock-1": adapter}, {})
        engine.start()
        try:
            time.sleep(0.01)
            frame_enables.set_enabled(0, 0x300, True)
            deadline = time.time() + 0.3
            while time.time() < deadline and engine.state != ReplayState.STOPPED:
                time.sleep(0.005)
            self.assertEqual(ReplayState.STOPPED, engine.state)
            self.assertEqual([0x300], [frame.message_id for frame in adapter.sent_frames])
            self.assertEqual(b"\x02", adapter.sent_frames[0].payload)
            self.assertEqual(1, engine.stats.sent_frames)
            self.assertEqual(1, engine.stats.skipped_frames)
        finally:
            engine.stop()


if __name__ == "__main__":
    unittest.main()
