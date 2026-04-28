from __future__ import annotations

from pathlib import Path
import unittest

import tests.bootstrap  # noqa: F401

from replay_tool.app import ReplayApplication
from replay_tool.domain import BusType, ReplayScenario
from replay_tool.storage import AscTraceReader


ROOT = Path(__file__).resolve().parents[1]


class ScenarioAndPlannerTests(unittest.TestCase):
    def test_scenario_json_parses_minimum_shape(self) -> None:
        payload = {
            "schema_version": 1,
            "name": "demo",
            "traces": [{"id": "trace1", "path": "sample.asc"}],
            "devices": [{"id": "mock0", "driver": "mock"}],
            "channels": [
                {
                    "logical_channel": 0,
                    "trace_id": "trace1",
                    "source_channel": 0,
                    "device_id": "mock0",
                    "physical_channel": 1,
                    "bus": "CANFD",
                }
            ],
        }

        scenario = ReplayScenario.from_dict(payload)

        self.assertEqual("demo", scenario.name)
        self.assertEqual(BusType.CANFD, scenario.channels[0].config.bus)
        self.assertEqual(500000, scenario.channels[0].config.nominal_baud)

    def test_asc_reader_parses_can_and_canfd(self) -> None:
        events = AscTraceReader().read(str(ROOT / "examples" / "sample.asc"))

        self.assertEqual([BusType.CANFD, BusType.CAN], [item.bus for item in events])
        self.assertEqual(0x18DAF110, events[0].message_id)
        self.assertTrue(events[0].extended)
        self.assertTrue(events[0].brs)
        self.assertEqual(0xC, events[0].dlc)
        self.assertEqual(24, len(events[0].payload))
        self.assertEqual(0x123, events[1].message_id)

    def test_planner_maps_trace_source_to_logical_channel(self) -> None:
        app = ReplayApplication()
        plan = app.compile_plan(ROOT / "examples" / "mock_canfd.json")

        self.assertEqual("mock-canfd-demo", plan.name)
        self.assertEqual(1, len(plan.frames))
        self.assertEqual(0, plan.frames[0].channel)
        self.assertEqual("mock0", plan.channels[0].device_id)
        self.assertEqual(0, plan.channels[0].physical_channel)


if __name__ == "__main__":
    unittest.main()
