import time
import unittest

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
)
from replay_platform.runtime.engine import ReplayEngine
from replay_platform.services.signal_catalog import SignalOverrideService


class ReplayEngineTests(unittest.TestCase):
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
