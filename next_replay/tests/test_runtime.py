from __future__ import annotations

import time
import unittest

import tests.bootstrap  # noqa: F401

from replay_tool.adapters.mock import MockDevice
from replay_tool.domain import BusType, ChannelConfig, DeviceConfig, Frame, ReplayState
from replay_tool.planning import PlannedChannel, ReplayPlan
from replay_tool.ports import DeviceRegistry
from replay_tool.runtime import ReplayRuntime


class ManualClock:
    def __init__(self) -> None:
        self.now_ns = 0

    def __call__(self) -> int:
        return self.now_ns

    def advance_sleep(self, seconds: float) -> None:
        self.now_ns += max(int(seconds * 1_000_000_000), 1)


class RuntimeTests(unittest.TestCase):
    def _make_plan(self, *, loop: bool = False, ts_ns: int = 0) -> tuple[ReplayPlan, list[MockDevice]]:
        devices: list[MockDevice] = []
        config = DeviceConfig(id="mock0", driver="mock")
        channel_config = ChannelConfig(bus=BusType.CANFD)
        frame = Frame(
            ts_ns=ts_ns,
            bus=BusType.CANFD,
            channel=0,
            message_id=0x123,
            payload=b"\x01\x02",
            dlc=2,
        )
        plan = ReplayPlan(
            name="runtime-demo",
            frames=(frame,),
            devices=(config,),
            channels=(
                PlannedChannel(
                    logical_channel=0,
                    device_id="mock0",
                    physical_channel=1,
                    binding=type(
                        "Binding",
                        (),
                        {
                            "config": channel_config,
                        },
                    )(),
                ),
            ),
            loop=loop,
        )
        registry = DeviceRegistry()
        registry.register("mock", lambda item: devices.append(MockDevice(item)) or devices[-1])
        return plan, devices, registry

    def test_runtime_sends_frames_and_closes_device(self) -> None:
        plan, devices, registry = self._make_plan()
        clock = ManualClock()
        runtime = ReplayRuntime(registry, clock=clock, sleeper=clock.advance_sleep)

        runtime.configure(plan)
        runtime.start()
        self.assertTrue(runtime.wait(timeout=2.0))

        snapshot = runtime.snapshot()
        self.assertEqual(ReplayState.STOPPED, snapshot.state)
        self.assertEqual(1, snapshot.sent_frames)
        self.assertFalse(devices[0].opened)
        self.assertEqual(1, devices[0].sent_frames[0].channel)

    def test_pause_resume_rebinds_time_base(self) -> None:
        plan, _devices, registry = self._make_plan(ts_ns=1_000_000_000)
        clock = ManualClock()
        runtime = ReplayRuntime(registry, clock=clock, sleeper=lambda seconds: time.sleep(0.001))

        runtime.configure(plan)
        runtime.start()
        runtime.pause()
        self.assertEqual(ReplayState.PAUSED, runtime.snapshot().state)
        clock.now_ns = 1_000_000_000
        runtime.resume()
        clock.now_ns = 2_000_000_000

        self.assertTrue(runtime.wait(timeout=2.0))
        self.assertEqual(1, runtime.snapshot().sent_frames)

    def test_loop_restarts_until_stopped(self) -> None:
        plan, _devices, registry = self._make_plan(loop=True)
        clock = ManualClock()
        runtime = ReplayRuntime(registry, clock=clock, sleeper=clock.advance_sleep)

        runtime.configure(plan)
        runtime.start()
        deadline = time.monotonic() + 2.0
        while runtime.snapshot().completed_loops < 1 and time.monotonic() < deadline:
            time.sleep(0.001)
        runtime.stop()

        self.assertGreaterEqual(runtime.snapshot().completed_loops, 1)


if __name__ == "__main__":
    unittest.main()
