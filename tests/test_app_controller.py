from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tests.bootstrap  # noqa: F401

from replay_platform.app_controller import (
    LOG_BUFFER_LIMIT,
    LOG_LEVEL_PRESET_DEBUG_ALL,
    LOG_LEVEL_PRESET_DEBUG_SAMPLED,
    LOG_LEVEL_PRESET_INFO,
    LOG_LEVEL_PRESET_WARNING,
    ReplayApplication,
)
from replay_platform.core import (
    BusType,
    DeviceChannelBinding,
    FrameEnableRule,
    ReplayFrameLogMode,
    ReplayLaunchSource,
    ReplayLogConfig,
    ReplayLogLevel,
    ReplayState,
    ScenarioSpec,
)
from replay_platform.ui.main_window import _plan_log_refresh


class ReplayApplicationLogTests(unittest.TestCase):
    def test_log_buffer_keeps_recent_entries_and_cursor_continues_after_trim(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            for index in range(LOG_BUFFER_LIMIT + 5):
                app.log(f"log {index}")

            base_index, entries = app.log_snapshot()
            self.assertEqual(5, base_index)
            self.assertEqual(LOG_BUFFER_LIMIT, len(entries))
            self.assertEqual("log 5", entries[0])
            self.assertEqual(f"log {LOG_BUFFER_LIMIT + 4}", entries[-1])

            cursor = base_index + len(entries)
            app.log(f"log {LOG_BUFFER_LIMIT + 5}")
            app.log(f"log {LOG_BUFFER_LIMIT + 6}")

            next_base, next_entries = app.log_snapshot()
            self.assertEqual(7, next_base)
            self.assertEqual(LOG_BUFFER_LIMIT, len(next_entries))

            mode, offset = _plan_log_refresh(cursor, next_base, len(next_entries))
            self.assertEqual("append", mode)
            self.assertEqual(
                [f"log {LOG_BUFFER_LIMIT + 5}", f"log {LOG_BUFFER_LIMIT + 6}"],
                next_entries[offset:],
            )

    def test_clear_logs_resets_entries_and_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            app.log("log 1")
            app.log("log 2")

            app.clear_logs()
            base_index, entries = app.log_snapshot()

            self.assertEqual(0, base_index)
            self.assertEqual([], entries)

    def test_apply_log_level_preset_updates_level_and_frame_log_mode(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace), log_config=ReplayLogConfig())

            expected = {
                LOG_LEVEL_PRESET_WARNING: (ReplayLogLevel.WARNING, ReplayFrameLogMode.OFF, 10),
                LOG_LEVEL_PRESET_INFO: (ReplayLogLevel.INFO, ReplayFrameLogMode.OFF, 10),
                LOG_LEVEL_PRESET_DEBUG_SAMPLED: (ReplayLogLevel.DEBUG, ReplayFrameLogMode.SAMPLED, 10),
                LOG_LEVEL_PRESET_DEBUG_ALL: (ReplayLogLevel.DEBUG, ReplayFrameLogMode.ALL, 10),
            }

            for preset, (expected_level, expected_frame_mode, expected_sample_rate) in expected.items():
                with self.subTest(preset=preset):
                    app.apply_log_level_preset(preset)
                    self.assertEqual(expected_level, app.log_config.level)
                    self.assertEqual(expected_frame_mode, app.log_config.frame_mode)
                    self.assertEqual(expected_sample_rate, app.log_config.frame_sample_rate)
                    self.assertEqual(expected_level, app.engine.log_config.level)
                    self.assertEqual(expected_frame_mode, app.engine.log_config.frame_mode)
                    self.assertEqual(expected_sample_rate, app.engine.log_config.frame_sample_rate)

    def test_current_log_level_preset_reflects_debug_frame_mode(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace), log_config=ReplayLogConfig(level=ReplayLogLevel.DEBUG))

            app.log_config.frame_mode = ReplayFrameLogMode.ALL
            self.assertEqual(LOG_LEVEL_PRESET_DEBUG_ALL, app.current_log_level_preset())

            app.log_config.frame_mode = ReplayFrameLogMode.SAMPLED
            self.assertEqual(LOG_LEVEL_PRESET_DEBUG_SAMPLED, app.current_log_level_preset())

    def test_level_aware_app_logs_respect_warning_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace), log_config=ReplayLogConfig(level=ReplayLogLevel.WARNING))

            app.log_info("info log")
            app.log_warning("warning log")

            _, entries = app.log_snapshot()
            self.assertEqual(["warning log"], entries)

    def test_runtime_snapshot_exposes_launch_source(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-1",
                name="示例场景",
                bindings=[
                    DeviceChannelBinding(
                        adapter_id="mock0",
                        driver="mock",
                        logical_channel=0,
                        physical_channel=0,
                        bus_type=BusType.CAN,
                        device_type="MOCK",
                    )
                ],
            )

            app.start_replay(scenario, launch_source=ReplayLaunchSource.SELECTED_FALLBACK)
            snapshot = app.runtime_snapshot()

            self.assertIn(snapshot.state, {ReplayState.RUNNING, ReplayState.STOPPED})
            self.assertEqual(ReplayLaunchSource.SELECTED_FALLBACK, snapshot.launch_source)

    def test_start_replay_clears_runtime_frame_enable_rules(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            app.frame_enables.set_rule(FrameEnableRule(logical_channel=0, message_id=0x123, enabled=False))
            scenario = ScenarioSpec(
                scenario_id="scenario-1",
                name="示例场景",
                bindings=[
                    DeviceChannelBinding(
                        adapter_id="mock0",
                        driver="mock",
                        logical_channel=0,
                        physical_channel=0,
                        bus_type=BusType.CAN,
                        device_type="MOCK",
                    )
                ],
            )

            app.start_replay(scenario)

            self.assertEqual([], app.frame_enables.list_rules())

    def test_stop_replay_clears_runtime_frame_enable_rules(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-1",
                name="示例场景",
                bindings=[
                    DeviceChannelBinding(
                        adapter_id="mock0",
                        driver="mock",
                        logical_channel=0,
                        physical_channel=0,
                        bus_type=BusType.CAN,
                        device_type="MOCK",
                    )
                ],
            )

            app.start_replay(scenario)
            app.frame_enables.set_rule(FrameEnableRule(logical_channel=0, message_id=0x123, enabled=False))

            app.stop_replay()

            self.assertEqual([], app.frame_enables.list_rules())


if __name__ == "__main__":
    unittest.main()
