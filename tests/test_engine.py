import time
import unittest
from typing import Optional

import tests.bootstrap  # noqa: F401

from replay_platform.adapters.mock import MockDeviceAdapter
from replay_platform.core import (
    BusType,
    DeviceChannelBinding,
    FrameEvent,
    LinkAction,
    LinkActionType,
    ReplayState,
    ScenarioSpec,
    SignalOverride,
)
from replay_platform.runtime.engine import ReplayEngine
from replay_platform.services.signal_catalog import (
    SignalOverrideService,
    StaticMessageCodec,
    StaticMessageDefinition,
)


class RecordingMockDeviceAdapter(MockDeviceAdapter):
    def __init__(self, adapter_id: str = "mock", channel_count: int = 4) -> None:
        super().__init__(adapter_id, channel_count=channel_count)
        self.send_batches: list[list[FrameEvent]] = []

    def send(self, batch):
        self.send_batches.append([item.clone() for item in batch])
        return super().send(batch)


class PartialSendMockDeviceAdapter(RecordingMockDeviceAdapter):
    def __init__(self, adapter_id: str = "mock", channel_count: int = 4, sent_count: int = 0) -> None:
        super().__init__(adapter_id, channel_count=channel_count)
        self._sent_count = sent_count

    def send(self, batch):
        self.send_batches.append([item.clone() for item in batch])
        accepted = max(0, min(self._sent_count, len(batch)))
        self.sent_frames.extend(batch[:accepted])
        return accepted


class ReplayEngineTests(unittest.TestCase):
    def _run_engine_to_completion(
        self,
        scenario: ScenarioSpec,
        frames: list[FrameEvent],
        adapters,
        timeout_s: float = 0.2,
        signal_overrides: Optional[SignalOverrideService] = None,
        logger=None,
    ) -> ReplayEngine:
        engine = ReplayEngine(
            signal_overrides=signal_overrides or SignalOverrideService(),
            logger=logger,
        )
        engine.configure(scenario, frames, adapters, {})
        engine.start()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if engine.state == ReplayState.STOPPED:
                return engine
            time.sleep(0.005)
        try:
            engine.stop()
        finally:
            self.fail("回放未在预期时间内结束。")

    def test_start_opens_all_device_channels(self):
        adapter = MockDeviceAdapter("mock-1", channel_count=4)
        scenario = ScenarioSpec(
            scenario_id="s-open-all",
            name="Open all channels",
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
            self.assertEqual({0, 1, 2, 3}, set(adapter.health().per_channel))
        finally:
            engine.stop()

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

        self._run_engine_to_completion(scenario, frames, {"mock-1": adapter}, logger=logs.append)

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


if __name__ == "__main__":
    unittest.main()
