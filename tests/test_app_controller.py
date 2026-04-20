from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tests.bootstrap  # noqa: F401

from replay_platform.app_controller import (
    LOG_BUFFER_LIMIT,
    LOG_LEVEL_PRESET_DEBUG_ALL,
    LOG_LEVEL_PRESET_DEBUG_SAMPLED,
    LOG_LEVEL_PRESET_INFO,
    LOG_LEVEL_PRESET_WARNING,
    ReplayPreparation,
    ReplayApplication,
)
from replay_platform.core import (
    BusType,
    DatabaseBinding,
    DeviceChannelBinding,
    FrameEnableRule,
    FrameEvent,
    ReplayFrameLogMode,
    ReplayLaunchSource,
    ReplayLogConfig,
    ReplayLogLevel,
    ReplayRuntimeSnapshot,
    ReplayState,
    ScenarioSpec,
    SignalOverride,
    TraceFileRecord,
)
from replay_platform.ui.main_window import _plan_log_refresh


class CompletedReplayEngineStub:
    def __init__(self) -> None:
        self._pending_cleanup = True
        self.finalize_calls = 0

    def snapshot(self) -> ReplayRuntimeSnapshot:
        return ReplayRuntimeSnapshot(state=ReplayState.STOPPED)

    def has_pending_completion_cleanup(self) -> bool:
        return self._pending_cleanup

    def finalize_completed_replay(self) -> bool:
        if not self._pending_cleanup:
            return False
        self.finalize_calls += 1
        self._pending_cleanup = False
        return True


class ReplayApplicationLogTests(unittest.TestCase):
    @staticmethod
    def _fixture_path(name: str) -> str:
        return str(Path(__file__).resolve().parent / "fixtures" / name)

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
            self.assertFalse(snapshot.loop_enabled)
            self.assertEqual(0, snapshot.completed_loops)

    def test_runtime_snapshot_exposes_loop_state(self) -> None:
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

            app.start_replay(scenario, loop_enabled=True)
            snapshot = app.runtime_snapshot()

            self.assertIn(snapshot.state, {ReplayState.RUNNING, ReplayState.STOPPED})
            self.assertTrue(snapshot.loop_enabled)
            self.assertEqual(0, snapshot.completed_loops)

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

    def test_prepare_replay_returns_preparation_without_starting_engine(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-prepare-1",
                name="准备回放",
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
            frames = [FrameEvent(ts_ns=1, bus_type=BusType.CAN, channel=0, message_id=0x123, payload=b"\x01", dlc=1)]

            with patch.object(app, "_load_replay_frames", return_value=frames) as load_frames:
                preparation = app.prepare_replay(
                    scenario,
                    launch_source=ReplayLaunchSource.SELECTED_FALLBACK,
                    loop_enabled=True,
                )

            load_frames.assert_called_once_with(scenario)
            self.assertIsInstance(preparation, ReplayPreparation)
            self.assertEqual(scenario, preparation.scenario)
            self.assertEqual(frames, preparation.frames)
            self.assertEqual(ReplayLaunchSource.SELECTED_FALLBACK, preparation.launch_source)
            self.assertTrue(preparation.loop_enabled)
            self.assertEqual(ReplayState.STOPPED, app.engine.state)

    def test_start_prepared_replay_uses_prepared_frames(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-prepare-2",
                name="准备启动",
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
            frames = [FrameEvent(ts_ns=1, bus_type=BusType.CAN, channel=0, message_id=0x123, payload=b"\x01", dlc=1)]
            preparation = ReplayPreparation(
                scenario=scenario,
                frames=frames,
                launch_source=ReplayLaunchSource.SELECTED_FALLBACK,
                loop_enabled=True,
            )

            with patch.object(app, "_build_adapters", return_value={"mock0": object()}) as build_adapters, patch.object(
                app,
                "_build_diagnostics",
                return_value={},
            ) as build_diagnostics, patch.object(app.engine, "configure") as configure, patch.object(
                app.engine,
                "start",
            ) as start:
                app.start_prepared_replay(preparation)

            build_adapters.assert_called_once_with(scenario)
            build_diagnostics.assert_called_once_with(scenario, {"mock0": build_adapters.return_value["mock0"]})
            configure.assert_called_once_with(
                scenario,
                frames,
                {"mock0": build_adapters.return_value["mock0"]},
                {},
                launch_source=ReplayLaunchSource.SELECTED_FALLBACK,
                loop_enabled=True,
            )
            start.assert_called_once_with()

    def test_start_prepared_replay_applies_workspace_overrides_after_scenario_defaults(self) -> None:
        try:
            import cantools  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("cantools 未安装，跳过真实 DBC 解析测试")
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-prepare-dbc",
                name="准备启动 DBC",
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
                database_bindings=[
                    DatabaseBinding(logical_channel=0, path=self._fixture_path("sample_vehicle.dbc"), format="dbc")
                ],
                signal_overrides=[
                    SignalOverride(
                        logical_channel=0,
                        message_id_or_pgn=0x123,
                        signal_name="VehicleSpeed",
                        value=10,
                    )
                ],
            )
            app.replace_workspace_signal_overrides(
                [
                    SignalOverride(
                        logical_channel=0,
                        message_id_or_pgn=0x123,
                        signal_name="VehicleSpeed",
                        value=88,
                    )
                ]
            )
            preparation = ReplayPreparation(scenario=scenario, frames=[], launch_source=ReplayLaunchSource.SCENARIO_BOUND)

            with patch.object(app, "_build_adapters", return_value={"mock0": object()}), patch.object(
                app,
                "_build_diagnostics",
                return_value={},
            ), patch.object(app.engine, "configure"), patch.object(app.engine, "start"):
                app.start_prepared_replay(preparation)

            overrides = app.engine.signal_overrides.list_overrides()
            self.assertEqual(1, len(overrides))
            self.assertEqual(88, overrides[0].value)

    def test_start_prepared_replay_clears_previous_runtime_databases_and_overrides(self) -> None:
        try:
            import cantools  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("cantools 未安装，跳过真实 DBC 解析测试")
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario_with_dbc = ScenarioSpec(
                scenario_id="scenario-has-dbc",
                name="Has DBC",
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
                database_bindings=[
                    DatabaseBinding(logical_channel=0, path=self._fixture_path("sample_vehicle.dbc"), format="dbc")
                ],
                signal_overrides=[
                    SignalOverride(
                        logical_channel=0,
                        message_id_or_pgn=0x123,
                        signal_name="Gear",
                        value=3,
                    )
                ],
            )
            scenario_without_dbc = ScenarioSpec(
                scenario_id="scenario-no-dbc",
                name="No DBC",
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
            preparation_with_dbc = ReplayPreparation(
                scenario=scenario_with_dbc,
                frames=[],
                launch_source=ReplayLaunchSource.SCENARIO_BOUND,
            )
            preparation_without_dbc = ReplayPreparation(
                scenario=scenario_without_dbc,
                frames=[],
                launch_source=ReplayLaunchSource.SCENARIO_BOUND,
            )

            with patch.object(app, "_build_adapters", return_value={"mock0": object()}), patch.object(
                app,
                "_build_diagnostics",
                return_value={},
            ), patch.object(app.engine, "configure"), patch.object(app.engine, "start"):
                app.start_prepared_replay(preparation_with_dbc)
                app.start_prepared_replay(preparation_without_dbc)

            self.assertEqual([], app.engine.signal_overrides.list_overrides())
            self.assertEqual([], app.engine.signal_overrides.list_message_ids(0))

    def test_start_prepared_replay_allows_failed_database_binding_when_no_override_depends_on_it(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-warn-dbc",
                name="Warn DBC",
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
                database_bindings=[
                    DatabaseBinding(logical_channel=0, path=str(Path(workspace) / "missing.dbc"), format="dbc")
                ],
            )
            preparation = ReplayPreparation(scenario=scenario, frames=[], launch_source=ReplayLaunchSource.SCENARIO_BOUND)

            with patch.object(app, "_build_adapters", return_value={"mock0": object()}), patch.object(
                app,
                "_build_diagnostics",
                return_value={},
            ), patch.object(app.engine, "configure"), patch.object(app.engine, "start"):
                app.start_prepared_replay(preparation)

            _, logs = app.log_snapshot()
            self.assertTrue(any("数据库绑定加载失败" in entry for entry in logs))

    def test_start_prepared_replay_blocks_when_workspace_override_depends_on_failed_database(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-fail-dbc",
                name="Fail DBC",
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
                database_bindings=[
                    DatabaseBinding(logical_channel=0, path=str(Path(workspace) / "missing.dbc"), format="dbc")
                ],
            )
            app.replace_workspace_signal_overrides(
                [
                    SignalOverride(
                        logical_channel=0,
                        message_id_or_pgn=0x123,
                        signal_name="VehicleSpeed",
                        value=50,
                    )
                ]
            )
            preparation = ReplayPreparation(scenario=scenario, frames=[], launch_source=ReplayLaunchSource.SCENARIO_BOUND)

            with patch.object(app, "_build_adapters", return_value={"mock0": object()}), patch.object(
                app,
                "_build_diagnostics",
                return_value={},
            ), patch.object(app.engine, "configure"), patch.object(app.engine, "start"):
                with self.assertRaisesRegex(ValueError, "工作区覆盖"):
                    app.start_prepared_replay(preparation)

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

    def test_runtime_snapshot_finalizes_completed_replay_cleanup_once(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            app.engine = CompletedReplayEngineStub()
            app.frame_enables.set_rule(FrameEnableRule(logical_channel=0, message_id=0x123, enabled=False))
            app._last_runtime_state = ReplayState.RUNNING

            first_snapshot = app.runtime_snapshot()
            second_snapshot = app.runtime_snapshot()

            self.assertEqual(ReplayState.STOPPED, first_snapshot.state)
            self.assertEqual(ReplayState.STOPPED, second_snapshot.state)
            self.assertEqual(1, app.engine.finalize_calls)
            self.assertEqual([], app.frame_enables.list_rules())

    def test_build_adapters_passes_full_binding_to_tongxing_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            primary = DeviceChannelBinding(
                adapter_id="tongxing0",
                driver="tongxing",
                logical_channel=0,
                physical_channel=0,
                bus_type=BusType.CAN,
                device_type="TC1014",
                sdk_root="TSMasterApi",
            )
            seed = DeviceChannelBinding(
                adapter_id="tongxing0",
                driver="tongxing",
                logical_channel=1,
                physical_channel=2,
                bus_type=BusType.CANFD,
                device_type="TC1014",
                sdk_root="TSMasterApi",
                metadata={"ts_application": "BenchApp"},
            )
            scenario = ScenarioSpec(
                scenario_id="scenario-1",
                name="tongxing",
                bindings=[primary, seed],
            )

            with patch("replay_platform.app_controller.TongxingDeviceAdapter") as adapter_cls:
                adapters = app._build_adapters(scenario)

            adapter_cls.assert_called_once_with("tongxing0", seed)
            self.assertIs(adapter_cls.return_value, adapters["tongxing0"])

    def test_load_replay_frames_maps_same_source_channel_from_different_files(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-map-1",
                name="文件映射",
                trace_file_ids=["trace-a", "trace-b"],
                bindings=[
                    DeviceChannelBinding(
                        trace_file_id="trace-a",
                        source_channel=0,
                        source_bus_type=BusType.CANFD,
                        adapter_id="mock0",
                        driver="mock",
                        logical_channel=10,
                        physical_channel=0,
                        bus_type=BusType.CANFD,
                        device_type="MOCK",
                    ),
                    DeviceChannelBinding(
                        trace_file_id="trace-b",
                        source_channel=0,
                        source_bus_type=BusType.CANFD,
                        adapter_id="mock1",
                        driver="mock",
                        logical_channel=11,
                        physical_channel=1,
                        bus_type=BusType.CANFD,
                        device_type="MOCK",
                    ),
                ],
            )
            trace_a_events = [
                FrameEvent(ts_ns=1, bus_type=BusType.CANFD, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
                FrameEvent(ts_ns=2, bus_type=BusType.CANFD, channel=1, message_id=0x101, payload=b"\x02", dlc=1),
            ]
            trace_b_events = [
                FrameEvent(ts_ns=3, bus_type=BusType.CANFD, channel=0, message_id=0x200, payload=b"\x03", dlc=1),
            ]
            with patch.object(
                app.library,
                "get_trace_file",
                side_effect=[
                    TraceFileRecord("trace-a", "a.asc", "C:/a.asc", "C:/lib/a.asc", "asc", "now"),
                    TraceFileRecord("trace-b", "b.asc", "C:/b.asc", "C:/lib/b.asc", "asc", "now"),
                ],
            ), patch.object(app.library, "load_trace_events", side_effect=[trace_a_events, trace_b_events]):
                frames = app._load_replay_frames(scenario)

            self.assertEqual([10, 11], [frame.channel for frame in frames])
            self.assertEqual([0x100, 0x200], [frame.message_id for frame in frames])
            self.assertEqual(["C:/a.asc", "C:/b.asc"], [frame.source_file for frame in frames])

    def test_load_replay_frames_keeps_only_selected_source_from_multi_channel_file(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-map-2",
                name="单文件多通道",
                trace_file_ids=["trace-a"],
                bindings=[
                    DeviceChannelBinding(
                        trace_file_id="trace-a",
                        source_channel=2,
                        source_bus_type=BusType.CAN,
                        adapter_id="mock0",
                        driver="mock",
                        logical_channel=5,
                        physical_channel=0,
                        bus_type=BusType.CAN,
                        device_type="MOCK",
                    )
                ],
            )
            trace_events = [
                FrameEvent(ts_ns=1, bus_type=BusType.CAN, channel=2, message_id=0x300, payload=b"\x01", dlc=1),
                FrameEvent(ts_ns=2, bus_type=BusType.CANFD, channel=2, message_id=0x301, payload=b"\x02", dlc=1),
                FrameEvent(ts_ns=3, bus_type=BusType.CAN, channel=3, message_id=0x302, payload=b"\x03", dlc=1),
            ]
            with patch.object(
                app.library,
                "get_trace_file",
                return_value=TraceFileRecord("trace-a", "a.asc", "C:/a.asc", "C:/lib/a.asc", "asc", "now"),
            ), patch.object(app.library, "load_trace_events", return_value=trace_events) as load_trace_events:
                frames = app._load_replay_frames(scenario)

            load_trace_events.assert_called_once_with(
                "trace-a",
                source_filters={(2, BusType.CAN)},
            )
            self.assertEqual(1, len(frames))
            self.assertEqual(5, frames[0].channel)
            self.assertEqual(0x300, frames[0].message_id)

    def test_load_replay_frames_merges_interleaved_traces_without_global_resort(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-map-3",
                name="鍚堝苟澶氭枃浠?",
                trace_file_ids=["trace-a", "trace-b"],
            )
            trace_a_events = [
                FrameEvent(ts_ns=1, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
                FrameEvent(ts_ns=4, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x02", dlc=1),
            ]
            trace_b_events = [
                FrameEvent(ts_ns=2, bus_type=BusType.CAN, channel=1, message_id=0x200, payload=b"\x03", dlc=1),
                FrameEvent(ts_ns=3, bus_type=BusType.CAN, channel=1, message_id=0x201, payload=b"\x04", dlc=1),
            ]
            with patch.object(
                app.library,
                "get_trace_file",
                side_effect=[
                    TraceFileRecord("trace-a", "a.asc", "C:/a.asc", "C:/lib/a.asc", "asc", "now"),
                    TraceFileRecord("trace-b", "b.asc", "C:/b.asc", "C:/lib/b.asc", "asc", "now"),
                ],
            ), patch.object(app.library, "load_trace_events", side_effect=[trace_a_events, trace_b_events]):
                frames = app._load_replay_frames(scenario)

            self.assertEqual([1, 2, 3, 4], [frame.ts_ns for frame in frames])
            self.assertEqual(["C:/a.asc", "C:/b.asc", "C:/b.asc", "C:/a.asc"], [frame.source_file for frame in frames])

    def test_load_replay_frames_merges_multiple_bindings_from_same_trace_in_timestamp_order(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-map-4",
                name="鍚屾簮鍚堝苟",
                trace_file_ids=["trace-a"],
                bindings=[
                    DeviceChannelBinding(
                        trace_file_id="trace-a",
                        source_channel=0,
                        source_bus_type=BusType.CAN,
                        adapter_id="mock0",
                        driver="mock",
                        logical_channel=10,
                        physical_channel=0,
                        bus_type=BusType.CAN,
                        device_type="MOCK",
                    ),
                    DeviceChannelBinding(
                        trace_file_id="trace-a",
                        source_channel=1,
                        source_bus_type=BusType.CAN,
                        adapter_id="mock1",
                        driver="mock",
                        logical_channel=11,
                        physical_channel=1,
                        bus_type=BusType.CAN,
                        device_type="MOCK",
                    ),
                ],
            )
            trace_events = [
                FrameEvent(ts_ns=1, bus_type=BusType.CAN, channel=0, message_id=0x100, payload=b"\x01", dlc=1),
                FrameEvent(ts_ns=2, bus_type=BusType.CAN, channel=1, message_id=0x200, payload=b"\x02", dlc=1),
                FrameEvent(ts_ns=3, bus_type=BusType.CAN, channel=0, message_id=0x101, payload=b"\x03", dlc=1),
            ]
            with patch.object(
                app.library,
                "get_trace_file",
                return_value=TraceFileRecord("trace-a", "a.asc", "C:/a.asc", "C:/lib/a.asc", "asc", "now"),
            ), patch.object(app.library, "load_trace_events", return_value=trace_events) as load_trace_events:
                frames = app._load_replay_frames(scenario)

            load_trace_events.assert_called_once_with(
                "trace-a",
                source_filters={(0, BusType.CAN), (1, BusType.CAN)},
            )
            self.assertEqual([1, 2, 3], [frame.ts_ns for frame in frames])
            self.assertEqual([10, 11, 10], [frame.channel for frame in frames])
            self.assertEqual([0x100, 0x200, 0x101], [frame.message_id for frame in frames])

    def test_load_replay_frames_reuses_prepared_trace_cache_for_same_signature(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            scenario = ScenarioSpec(
                scenario_id="scenario-map-5",
                name="缂撳瓨鍑嗗甯?",
                trace_file_ids=["trace-a"],
                bindings=[
                    DeviceChannelBinding(
                        trace_file_id="trace-a",
                        source_channel=2,
                        source_bus_type=BusType.CAN,
                        adapter_id="mock0",
                        driver="mock",
                        logical_channel=7,
                        physical_channel=0,
                        bus_type=BusType.CAN,
                        device_type="MOCK",
                    )
                ],
            )
            trace_events = [
                FrameEvent(ts_ns=1, bus_type=BusType.CAN, channel=2, message_id=0x310, payload=b"\x01", dlc=1),
                FrameEvent(ts_ns=2, bus_type=BusType.CAN, channel=2, message_id=0x311, payload=b"\x02", dlc=1),
            ]
            record = TraceFileRecord("trace-a", "a.asc", "C:/a.asc", "C:/lib/a.asc", "asc", "now")
            with patch.object(app.library, "get_trace_file", return_value=record), patch.object(
                app.library,
                "load_trace_events",
                return_value=trace_events,
            ) as load_trace_events:
                first = app._load_replay_frames(scenario)
                second = app._load_replay_frames(scenario)

            load_trace_events.assert_called_once_with(
                "trace-a",
                source_filters={(2, BusType.CAN)},
            )
            self.assertEqual([7, 7], [frame.channel for frame in first])
            self.assertEqual([0x310, 0x311], [frame.message_id for frame in second])
            self.assertEqual(["C:/a.asc", "C:/a.asc"], [frame.source_file for frame in second])


if __name__ == "__main__":
    unittest.main()
