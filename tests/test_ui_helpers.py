from __future__ import annotations

import unittest

import tests.bootstrap  # noqa: F401

from replay_platform.ui.main_window import (
    _assess_scenario_launch,
    _binding_device_type_options,
    _binding_device_type_warning,
    _build_override_catalog_status_text,
    _build_log_level_hint,
    _format_replay_stats,
    _binding_draft_from_item,
    _build_frame_enable_candidate_ids_from_trace_summaries,
    _build_signal_catalog_hint,
    _binding_summary,
    _frame_enable_rule_summary,
    _format_override_message_option,
    _build_scenario_delete_summary,
    _build_runtime_visibility_summary,
    _build_scenario_business_summary,
    _build_json_preview,
    _build_scenario_counts_summary,
    _build_scenario_selection_summary,
    _build_trace_delete_summary,
    _build_trace_selection_summary,
    _filter_scenarios,
    _filter_trace_records,
    _parse_bool_text,
    _parse_hex_bytes_text,
    _parse_int_text,
    _parse_json_object_text,
    _parse_scalar_text,
    _signal_override_payload_items,
    _playback_button_state,
    _plan_log_refresh,
    _log_level_option,
    _parse_message_combo_text,
    _parse_log_level_option,
    _normalize_binding_item,
    _new_binding_draft,
    _scenario_payload_is_dirty,
    _should_reset_current_scenario_after_delete,
    _validate_binding_draft,
)
from replay_platform.app_controller import LOG_LEVEL_PRESET_DEBUG_ALL, LOG_LEVEL_PRESET_DEBUG_SAMPLED
from replay_platform.core import (
    AdapterHealth,
    FrameEnableRule,
    ReplayStats,
    ReplayLaunchSource,
    ReplayRuntimeSnapshot,
    ReplayState,
    ScenarioSpec,
    SignalOverride,
    TimelineKind,
    TraceFileRecord,
)
from replay_platform.services.signal_catalog import MessageCatalogEntry, SignalCatalogEntry


class MainWindowHelperTests(unittest.TestCase):
    def test_scenario_payload_dirty_ignores_metadata_key_order(self) -> None:
        saved_payload = {
            "scenario_id": "scenario-1",
            "name": "示例场景",
            "trace_file_ids": [],
            "bindings": [],
            "database_bindings": [],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
            "metadata": {"a": 1, "b": 2},
        }
        current_payload = {
            "scenario_id": "scenario-1",
            "name": "示例场景",
            "trace_file_ids": [],
            "bindings": [],
            "database_bindings": [],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
            "metadata": {"b": 2, "a": 1},
        }

        self.assertFalse(_scenario_payload_is_dirty(current_payload, saved_payload))
        changed_payload = dict(current_payload)
        changed_payload["name"] = "已修改"
        self.assertTrue(_scenario_payload_is_dirty(changed_payload, saved_payload))

    def test_parse_int_supports_hex(self) -> None:
        self.assertEqual(_parse_int_text("0x7E0", "tx_id"), 0x7E0)
        self.assertEqual(_parse_int_text("42", "port"), 42)

    def test_parse_bool_supports_common_values(self) -> None:
        self.assertTrue(_parse_bool_text("true", "enabled"))
        self.assertTrue(_parse_bool_text("是", "enabled"))
        self.assertFalse(_parse_bool_text("0", "enabled"))
        self.assertFalse(_parse_bool_text("", "enabled"))

    def test_parse_json_object_defaults_to_empty_dict(self) -> None:
        self.assertEqual(_parse_json_object_text("", "metadata"), {})
        self.assertEqual(_parse_json_object_text('{"ip":"192.168.0.10"}', "network"), {"ip": "192.168.0.10"})

    def test_parse_scalar_keeps_plain_text_and_numbers(self) -> None:
        self.assertEqual(_parse_scalar_text("12"), 12)
        self.assertEqual(_parse_scalar_text("12.5"), 12.5)
        self.assertEqual(_parse_scalar_text("0x123"), 0x123)
        self.assertEqual(_parse_scalar_text("vehicle_speed"), "vehicle_speed")

    def test_parse_hex_bytes_normalizes_spacing(self) -> None:
        self.assertEqual(_parse_hex_bytes_text("10 03", "payload"), "1003")
        self.assertEqual(_parse_hex_bytes_text("", "payload"), "")

    def test_parse_message_combo_text_supports_named_options(self) -> None:
        self.assertEqual(0x123, _parse_message_combo_text("0x123 | VehicleStatus"))
        self.assertEqual(0x123, _parse_message_combo_text("0x123"))
        self.assertIsNone(_parse_message_combo_text("VehicleStatus"))

    def test_format_override_message_option_uses_message_name(self) -> None:
        self.assertEqual(
            "0x123 | VehicleStatus",
            _format_override_message_option(MessageCatalogEntry(message_id=0x123, message_name="VehicleStatus")),
        )

    def test_plan_log_refresh_requests_reset_when_cursor_falls_behind_buffer(self) -> None:
        self.assertEqual(("reset", 0), _plan_log_refresh(4, 5, 2000))

    def test_plan_log_refresh_appends_from_cursor_offset(self) -> None:
        self.assertEqual(("append", 3), _plan_log_refresh(8, 5, 10))

    def test_log_level_options_distinguish_sampled_and_all_debug(self) -> None:
        self.assertEqual("调试（帧采样）", _log_level_option(LOG_LEVEL_PRESET_DEBUG_SAMPLED))
        self.assertEqual("调试（逐帧）", _log_level_option(LOG_LEVEL_PRESET_DEBUG_ALL))
        self.assertEqual(LOG_LEVEL_PRESET_DEBUG_SAMPLED, _parse_log_level_option("调试（帧采样）"))
        self.assertEqual(LOG_LEVEL_PRESET_DEBUG_ALL, _parse_log_level_option("调试（逐帧）"))

    def test_log_level_hint_describes_sampled_and_all_debug(self) -> None:
        self.assertIn("采样", _build_log_level_hint(LOG_LEVEL_PRESET_DEBUG_SAMPLED))
        self.assertIn("逐帧", _build_log_level_hint(LOG_LEVEL_PRESET_DEBUG_ALL))

    def test_new_binding_draft_leaves_zlg_device_type_empty(self) -> None:
        draft = _new_binding_draft(3)

        self.assertEqual("zlg", draft["driver"])
        self.assertEqual("", draft["device_type"])
        self.assertEqual("3", draft["physical_channel"])

    def test_binding_device_type_options_follow_driver(self) -> None:
        self.assertIn("USBCANFD_200U", _binding_device_type_options("zlg"))
        self.assertEqual(("MOCK",), _binding_device_type_options("mock"))
        self.assertEqual(("TC1014",), _binding_device_type_options("tongxing"))

    def test_validate_binding_draft_reports_required_bool_and_json_errors(self) -> None:
        binding_payload = {
            "adapter_id": "",
            "driver": "zlg",
            "logical_channel": "0",
            "physical_channel": "0",
            "bus_type": "CANFD",
            "device_type": "USBCANFD",
            "device_index": "0",
            "sdk_root": "zlgcan_python_251211",
            "nominal_baud": "500000",
            "data_baud": "2000000",
            "resistance_enabled": "maybe",
            "listen_only": False,
            "tx_echo": False,
            "merge_receive": False,
            "network": "[]",
            "metadata": "{}",
        }

        normalized, issues = _validate_binding_draft(binding_payload, 0)

        self.assertIsNone(normalized)
        self.assertEqual(
            ["bindings[0].adapter_id", "bindings[0].resistance_enabled", "bindings[0].network"],
            [issue.path for issue in issues],
        )

    def test_validate_binding_draft_allows_empty_zlg_device_type(self) -> None:
        normalized, issues = _validate_binding_draft(
            {
                "adapter_id": "zlg0",
                "driver": "zlg",
                "logical_channel": "0",
                "physical_channel": "0",
                "bus_type": "CANFD",
                "device_type": "",
                "device_index": "0",
                "sdk_root": "zlgcan_python_251211",
                "nominal_baud": "500000",
                "data_baud": "2000000",
                "resistance_enabled": True,
                "listen_only": False,
                "tx_echo": False,
                "merge_receive": False,
                "network": "{}",
                "metadata": "{}",
            },
            0,
        )

        self.assertEqual([], issues)
        self.assertEqual("", normalized["device_type"])

    def test_normalize_binding_item_allows_empty_zlg_device_type(self) -> None:
        normalized = _normalize_binding_item(
            {
                "adapter_id": "zlg0",
                "driver": "zlg",
                "logical_channel": "0",
                "physical_channel": "0",
                "bus_type": "CANFD",
                "device_type": "",
                "device_index": "0",
                "sdk_root": "zlgcan_python_251211",
                "nominal_baud": "500000",
                "data_baud": "2000000",
                "resistance_enabled": True,
                "listen_only": False,
                "tx_echo": False,
                "merge_receive": False,
                "network": "{}",
                "metadata": "{}",
            }
        )

        self.assertEqual("", normalized["device_type"])

    def test_binding_draft_from_item_uses_tongxing_sdk_default(self) -> None:
        draft = _binding_draft_from_item(
            {
                "adapter_id": "tongxing0",
                "driver": "tongxing",
                "logical_channel": 0,
                "physical_channel": 1,
                "bus_type": "CANFD",
                "device_type": "TC1014",
            }
        )

        self.assertEqual("TSMasterApi", draft["sdk_root"])

    def test_normalize_binding_item_uses_tongxing_sdk_default(self) -> None:
        normalized = _normalize_binding_item(
            {
                "adapter_id": "tongxing0",
                "driver": "tongxing",
                "logical_channel": "0",
                "physical_channel": "1",
                "bus_type": "CANFD",
                "device_type": "TC1014",
                "device_index": "0",
                "sdk_root": "",
                "nominal_baud": "500000",
                "data_baud": "2000000",
                "resistance_enabled": True,
                "listen_only": False,
                "tx_echo": False,
                "merge_receive": False,
                "network": "{}",
                "metadata": "{}",
            }
        )

        self.assertEqual("TSMasterApi", normalized["sdk_root"])

    def test_validate_binding_draft_uses_tongxing_sdk_default(self) -> None:
        normalized, issues = _validate_binding_draft(
            {
                "adapter_id": "tongxing0",
                "driver": "tongxing",
                "logical_channel": "0",
                "physical_channel": "1",
                "bus_type": "CANFD",
                "device_type": "TC1014",
                "device_index": "0",
                "sdk_root": "",
                "nominal_baud": "500000",
                "data_baud": "2000000",
                "resistance_enabled": True,
                "listen_only": False,
                "tx_echo": False,
                "merge_receive": False,
                "network": "{}",
                "metadata": "{}",
            },
            0,
        )

        self.assertEqual([], issues)
        self.assertEqual("TSMasterApi", normalized["sdk_root"])

    def test_binding_device_type_warning_reports_empty_zlg_as_warning(self) -> None:
        warning = _binding_device_type_warning(
            {
                "adapter_id": "zlg0",
                "driver": "zlg",
                "logical_channel": 0,
                "device_type": "",
            },
            0,
        )

        self.assertIsNotNone(warning)
        self.assertEqual("bindings[0].device_type", warning.path)
        self.assertIn("尚未选择具体 ZLG 设备类型", warning.message)

    def test_binding_device_type_warning_reports_legacy_zlg_alias(self) -> None:
        warning = _binding_device_type_warning(
            {
                "adapter_id": "zlg0",
                "driver": "zlg",
                "logical_channel": 0,
                "device_type": "USBCANFD",
            },
            0,
        )

        self.assertIsNotNone(warning)
        self.assertIn("旧写法 USBCANFD", warning.message)

    def _obsolete_assess_binding_probe_requires_specific_zlg_device_type(self) -> None:
        empty_assessment = _assess_binding_probe("zlg", "CANFD", "")
        legacy_assessment = _assess_binding_probe("zlg", "CANFD", "USBCANFD")
        ready_assessment = _assess_binding_probe("zlg", "CANFD", "USBCANFD_200U")

        self.assertFalse(empty_assessment.ready)
        self.assertIn("请先选择具体设备类型", empty_assessment.message)
        self.assertFalse(empty_assessment.include_current_value)
        self.assertFalse(legacy_assessment.ready)
        self.assertIn("旧写法", legacy_assessment.message)
        self.assertFalse(legacy_assessment.include_current_value)
        self.assertTrue(ready_assessment.ready)

    def _obsolete_physical_channel_option_values_can_hide_current_placeholder(self) -> None:
        self.assertEqual([], _physical_channel_option_values([], "0", include_current_value=False))
        self.assertEqual(["0", "1"], _physical_channel_option_values(["0", "1"], "0", include_current_value=False))
        self.assertEqual(["1", "2"], _physical_channel_option_values(["1"], "2", include_current_value=True))

    def test_build_json_preview_uses_last_valid_payload_when_errors_exist(self) -> None:
        last_valid_payload = {
            "scenario_id": "scenario-1",
            "name": "示例场景",
            "trace_file_ids": [],
            "bindings": [],
            "database_bindings": [],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
            "metadata": {"mode": "preview"},
        }

        note, preview = _build_json_preview(last_valid_payload, 2)

        self.assertEqual("当前表单存在 2 个错误，预览未更新。", note)
        self.assertIn('"mode": "preview"', preview)

    def test_binding_summary_formats_compact_label(self) -> None:
        binding_payload = {
            "adapter_id": "zlg0",
            "driver": "zlg",
            "logical_channel": "0",
            "physical_channel": "1",
            "bus_type": "CANFD",
            "device_type": "USBCANFD",
        }

        self.assertEqual("zlg0 | zlg | LC0->PC1 | CANFD/USBCANFD", _binding_summary(binding_payload))

    def test_binding_summary_formats_file_mapping_label(self) -> None:
        binding_payload = {
            "trace_file_id": "trace-a",
            "source_channel": 0,
            "source_bus_type": "CANFD",
            "adapter_id": "zlg0",
            "driver": "zlg",
            "logical_channel": "0",
            "physical_channel": "1",
            "bus_type": "CANFD",
            "device_type": "USBCANFD",
        }
        trace_lookup = {
            "trace-a": TraceFileRecord(
                trace_id="trace-a",
                name="can.asc",
                original_path="/tmp/can.asc",
                library_path="/tmp/cache.asc",
                format="asc",
                imported_at="now",
            )
        }

        self.assertEqual(
            "can.asc | CH0 | CANFD -> zlg0/PC1 | zlg | CANFD/USBCANFD",
            _binding_summary(binding_payload, trace_lookup),
        )

    def test_frame_enable_rule_summary_formats_compact_label(self) -> None:
        rule = FrameEnableRule(logical_channel=1, message_id=0x123, enabled=False)

        self.assertEqual("LC1 | 0x123 | 禁用", _frame_enable_rule_summary(rule))

    def test_build_frame_enable_candidates_uses_source_channels_without_file_mapping(self) -> None:
        candidates = _build_frame_enable_candidate_ids_from_trace_summaries(
            ["trace-a"],
            [],
            {
                "trace-a": [
                    {"source_channel": 0, "bus_type": "CANFD", "message_ids": [0x100, 0x101]},
                    {"source_channel": 1, "bus_type": "CAN", "message_ids": [0x200]},
                ]
            },
        )

        self.assertEqual({0: [0x100, 0x101], 1: [0x200]}, candidates)

    def test_build_frame_enable_candidates_maps_trace_sources_to_logical_channels(self) -> None:
        candidates = _build_frame_enable_candidate_ids_from_trace_summaries(
            ["trace-a", "trace-b"],
            [
                {
                    "trace_file_id": "trace-a",
                    "source_channel": 0,
                    "source_bus_type": "CANFD",
                    "logical_channel": 7,
                },
                {
                    "trace_file_id": "trace-b",
                    "source_channel": 2,
                    "source_bus_type": "CAN",
                    "logical_channel": 3,
                },
            ],
            {
                "trace-a": [
                    {"source_channel": 0, "bus_type": "CANFD", "message_ids": [0x100, 0x101]},
                    {"source_channel": 1, "bus_type": "CANFD", "message_ids": [0x999]},
                ],
                "trace-b": [
                    {"source_channel": 2, "bus_type": "CAN", "message_ids": [0x200, 0x201]},
                ],
            },
        )

        self.assertEqual({7: [0x100, 0x101], 3: [0x200, 0x201]}, candidates)

    def test_format_replay_stats_includes_loop_progress_when_enabled(self) -> None:
        stats = ReplayStats(sent_frames=12, skipped_frames=2, diagnostic_actions=1, link_actions=3)
        snapshot = ReplayRuntimeSnapshot(
            state=ReplayState.RUNNING,
            loop_enabled=True,
            completed_loops=2,
        )

        summary = _format_replay_stats(stats, snapshot)

        self.assertIn("循环回放：当前第 3 圈 / 已完成 2 圈", summary)
        self.assertIn("已发帧 12", summary)
        self.assertIn("错误 0", summary)

    def test_format_replay_stats_reports_completed_loops_after_stop(self) -> None:
        stats = ReplayStats(sent_frames=4, skipped_frames=1)
        snapshot = ReplayRuntimeSnapshot(
            state=ReplayState.STOPPED,
            loop_enabled=True,
            completed_loops=1,
        )

        summary = _format_replay_stats(stats, snapshot)

        self.assertIn("循环回放：已完成 1 圈", summary)
        self.assertNotIn("当前第", summary)

    def test_assess_scenario_launch_prefers_bound_trace_files(self) -> None:
        payload = {
            "scenario_id": "scenario-1",
            "name": "示例场景",
            "trace_file_ids": ["trace-a", "trace-b"],
            "bindings": [
                {
                    "adapter_id": "mock0",
                    "driver": "mock",
                    "logical_channel": 0,
                    "physical_channel": 0,
                    "bus_type": "CAN",
                    "device_type": "MOCK",
                }
            ],
            "database_bindings": [],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
            "metadata": {},
        }

        result = _assess_scenario_launch(payload, ["fallback-trace"])

        self.assertTrue(result.ready)
        self.assertEqual("已就绪", result.badge_text)
        self.assertEqual("启动来源：将使用场景内已绑定文件启动。", result.source_text)
        self.assertEqual("场景已绑定回放文件。", result.detail_text)

    def test_assess_scenario_launch_uses_selected_trace_fallback(self) -> None:
        payload = {
            "scenario_id": "scenario-1",
            "name": "示例场景",
            "trace_file_ids": [],
            "bindings": [
                {
                    "adapter_id": "mock0",
                    "driver": "mock",
                    "logical_channel": 0,
                    "physical_channel": 0,
                    "bus_type": "CAN",
                    "device_type": "MOCK",
                }
            ],
            "database_bindings": [],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
            "metadata": {},
        }

        result = _assess_scenario_launch(payload, ["fallback-trace"])

        self.assertTrue(result.ready)
        self.assertEqual("启动来源：将使用主窗口当前选中的文件启动。", result.source_text)
        self.assertEqual("将回退到主窗口当前选中的文件。", result.detail_text)

    def test_assess_scenario_launch_requires_trace_and_bindings(self) -> None:
        payload = {
            "scenario_id": "scenario-1",
            "name": "示例场景",
            "trace_file_ids": [],
            "bindings": [],
            "database_bindings": [],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
            "metadata": {},
        }

        result = _assess_scenario_launch(payload, [])

        self.assertFalse(result.ready)
        self.assertEqual("未就绪", result.badge_text)
        self.assertIn("缺少可回放文件。", result.issue_text)
        self.assertIn("场景未配置任何通道绑定。", result.issue_text)

    def test_playback_button_state_matches_state_matrix(self) -> None:
        stopped_ready = _playback_button_state(ReplayState.STOPPED, True)
        stopped_unready = _playback_button_state(ReplayState.STOPPED, False)
        running = _playback_button_state(ReplayState.RUNNING, True)
        paused = _playback_button_state(ReplayState.PAUSED, True)

        self.assertEqual((True, False, False, False), tuple(stopped_ready.__dict__.values()))
        self.assertEqual((False, False, False, False), tuple(stopped_unready.__dict__.values()))
        self.assertEqual((False, True, False, True), tuple(running.__dict__.values()))
        self.assertEqual((False, False, True, True), tuple(paused.__dict__.values()))

    def test_build_scenario_counts_summary_uses_payload_lengths(self) -> None:
        payload = {
            "trace_file_ids": ["trace-a", "trace-b"],
            "bindings": [{"adapter_id": "mock0"}],
            "database_bindings": [],
            "signal_overrides": [],
            "diagnostic_targets": [{}, {}],
            "diagnostic_actions": [{}],
            "link_actions": [{}, {}, {}],
        }

        summary = _build_scenario_counts_summary(payload)

        self.assertEqual("文件 2 个 | 绑定 1 条 | 诊断目标 2 个 | 诊断动作 1 条 | 链路动作 3 条", summary)

    def test_build_scenario_business_summary_uses_file_names_and_binding_info(self) -> None:
        payload = {
            "scenario_id": "scenario-1",
            "name": "示例场景",
            "trace_file_ids": ["trace-a", "trace-missing"],
            "bindings": [
                {
                    "adapter_id": "mock0",
                    "driver": "mock",
                    "logical_channel": 0,
                    "physical_channel": 1,
                    "bus_type": "CANFD",
                    "device_type": "MOCK",
                }
            ],
            "database_bindings": [{"logical_channel": 0, "path": "/tmp/vehicle.dbc", "format": "dbc"}],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
            "metadata": {},
        }
        trace_lookup = {
            "trace-a": TraceFileRecord(
                trace_id="trace-a",
                name="can.asc",
                original_path="/tmp/can.asc",
                library_path="/tmp/cache.asc",
                format="asc",
                imported_at="now",
            )
        }

        summary = _build_scenario_business_summary(payload, trace_lookup)

        self.assertEqual("回放文件：can.asc，缺失文件（trace-missing）", summary.trace_text)
        self.assertEqual("通道绑定：LC0（旧映射） -> mock0/PC1 CANFD", summary.binding_text)
        self.assertEqual("数据库绑定：LC0（旧映射） -> vehicle.dbc", summary.database_text)

    def test_build_scenario_business_summary_includes_database_load_status(self) -> None:
        payload = {
            "scenario_id": "scenario-1",
            "name": "示例场景",
            "trace_file_ids": [],
            "bindings": [
                {
                    "adapter_id": "mock0",
                    "driver": "mock",
                    "logical_channel": 0,
                    "physical_channel": 1,
                    "bus_type": "CANFD",
                    "device_type": "MOCK",
                }
            ],
            "database_bindings": [{"logical_channel": 0, "path": "/tmp/vehicle.dbc", "format": "dbc"}],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
            "metadata": {},
        }

        summary = _build_scenario_business_summary(
            payload,
            {},
            {0: {"loaded": False, "error": "cantools missing", "message_count": 0}},
        )

        self.assertEqual("数据库绑定：LC0（旧映射） -> vehicle.dbc（加载失败：cantools missing）", summary.database_text)

    def test_build_override_catalog_status_text_lists_loaded_and_failed_channels(self) -> None:
        text = _build_override_catalog_status_text(
            {
                0: {"loaded": True, "message_count": 2},
                1: {"loaded": False, "error": "missing.dbc"},
            }
        )

        self.assertIn("已加载 2 个报文", text)
        self.assertIn("加载失败：missing.dbc", text)

    def test_build_signal_catalog_hint_includes_metadata(self) -> None:
        text = _build_signal_catalog_hint(
            SignalCatalogEntry(
                message_id=0x200,
                signal_name="LightState",
                unit="",
                minimum=0,
                maximum=3,
                choices={0: "Off", 1: "LowBeam"},
            )
        )

        self.assertIn("LightState", text)
        self.assertIn("范围 0 ~ 3", text)
        self.assertIn("枚举 0=Off, 1=LowBeam", text)

    def test_signal_override_payload_items_round_trip_with_scenario_spec(self) -> None:
        payload_items = _signal_override_payload_items(
            [
                SignalOverride(
                    logical_channel=0,
                    message_id_or_pgn=0x123,
                    signal_name="VehicleSpeed",
                    value=42,
                )
            ]
        )

        scenario = ScenarioSpec.from_dict(
            {
                "scenario_id": "scenario-1",
                "name": "写回验证",
                "trace_file_ids": [],
                "bindings": [],
                "database_bindings": [],
                "signal_overrides": payload_items,
                "diagnostic_targets": [],
                "diagnostic_actions": [],
                "link_actions": [],
                "metadata": {},
            }
        )

        self.assertEqual(1, len(scenario.signal_overrides))
        self.assertEqual("VehicleSpeed", scenario.signal_overrides[0].signal_name)

    def test_filter_helpers_use_case_insensitive_contains(self) -> None:
        traces = [
            TraceFileRecord("t1", "Body.asc", "", "", "asc", "now"),
            TraceFileRecord("t2", "Powertrain.asc", "", "", "asc", "now"),
        ]
        scenario_records = [
            type("ScenarioLike", (), {"name": "Body Replay"})(),
            type("ScenarioLike", (), {"name": "Diag Case"})(),
        ]

        self.assertEqual(["Body.asc"], [item.name for item in _filter_trace_records(traces, "body")])
        self.assertEqual(["Body Replay"], [item.name for item in _filter_scenarios(scenario_records, "body")])

    def test_build_trace_selection_summary_reports_counts_and_span(self) -> None:
        records = [
            TraceFileRecord("t1", "a.asc", "", "", "asc", "now", event_count=10, start_ns=0, end_ns=5_000_000),
            TraceFileRecord("t2", "b.asc", "", "", "asc", "now", event_count=5, start_ns=1_000_000, end_ns=9_000_000),
        ]

        summary = _build_trace_selection_summary(records)

        self.assertIn("已选 2 个文件", summary)
        self.assertIn("累计 15 帧", summary)
        self.assertIn("9.000 ms", summary)

    def test_build_trace_delete_summary_reports_references(self) -> None:
        record = TraceFileRecord(
            "t1",
            "body.asc",
            "",
            "",
            "asc",
            "now",
            event_count=18,
            start_ns=0,
            end_ns=20_000_000,
        )
        scenarios = [
            ScenarioSpec(scenario_id="scenario-1", name="Body Replay", trace_file_ids=["t1"]),
            ScenarioSpec(scenario_id="scenario-2", name="Diag Replay", trace_file_ids=["t1"]),
        ]

        summary = _build_trace_delete_summary(record, scenarios)

        self.assertIn("文件：body.asc", summary)
        self.assertIn("帧数：18", summary)
        self.assertIn("20.000 ms", summary)
        self.assertIn("仍被 2 个场景引用", summary)
        self.assertIn("Body Replay", summary)

    def test_build_scenario_selection_summary_reports_selected_counts(self) -> None:
        scenario = type(
            "ScenarioLike",
            (),
            {
                "name": "Body Replay",
                "trace_file_ids": ["t1"],
                "bindings": [object(), object()],
                "database_bindings": [object()],
                "diagnostic_actions": [object(), object(), object()],
            },
        )()

        summary = _build_scenario_selection_summary(scenario)

        self.assertEqual("Body Replay | 文件 1 | 绑定 2 | 数据库 1 | 诊断动作 3", summary)

    def test_build_scenario_delete_summary_uses_business_counts(self) -> None:
        scenario = ScenarioSpec(
            scenario_id="scenario-1",
            name="Body Replay",
            trace_file_ids=["t1"],
            bindings=[object()],  # type: ignore[list-item]
            diagnostic_actions=[object(), object()],  # type: ignore[list-item]
        )

        summary = _build_scenario_delete_summary(scenario)

        self.assertIn("场景：Body Replay", summary)
        self.assertIn("文件 1", summary)
        self.assertIn("绑定 1", summary)
        self.assertIn("诊断动作 2", summary)

    def test_should_reset_current_scenario_after_delete_matches_ids(self) -> None:
        current_payload = {"scenario_id": "scenario-1", "name": "Body Replay"}

        self.assertTrue(_should_reset_current_scenario_after_delete(current_payload, "scenario-1"))
        self.assertFalse(_should_reset_current_scenario_after_delete(current_payload, "scenario-2"))

    def test_build_runtime_visibility_summary_formats_snapshot_details(self) -> None:
        snapshot = ReplayRuntimeSnapshot(
            state=ReplayState.RUNNING,
            current_ts_ns=5_000_000,
            total_ts_ns=10_000_000,
            timeline_index=1,
            timeline_size=4,
            current_item_kind=TimelineKind.FRAME,
            current_source_file="body.asc",
            launch_source=ReplayLaunchSource.SELECTED_FALLBACK,
        )
        snapshot.adapter_health["mock0"] = AdapterHealth(online=True, detail="ok", per_channel={0: True})
        bindings = [{"adapter_id": "mock0", "logical_channel": 0, "physical_channel": 0}]

        summary = _build_runtime_visibility_summary(snapshot, bindings)

        self.assertEqual("进度 50.0% | 当前时间 5.000 ms / 10.000 ms", summary.progress_text)
        self.assertEqual("当前来源：body.asc", summary.source_text)
        self.assertIn("mock0 在线", summary.device_text)
        self.assertEqual("启动来源：主窗口选中文件回退", summary.launch_text)

    def test_assess_scenario_launch_requires_all_checked_files_mapped_for_file_mapping(self) -> None:
        payload = {
            "scenario_id": "scenario-1",
            "name": "文件映射场景",
            "trace_file_ids": ["trace-a", "trace-b"],
            "bindings": [
                {
                    "trace_file_id": "trace-a",
                    "source_channel": 0,
                    "source_bus_type": "CANFD",
                    "adapter_id": "mock0",
                    "driver": "mock",
                    "logical_channel": 0,
                    "physical_channel": 0,
                    "bus_type": "CANFD",
                    "device_type": "MOCK",
                }
            ],
            "database_bindings": [],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
            "metadata": {},
        }

        result = _assess_scenario_launch(payload, ["fallback-trace"])

        self.assertFalse(result.ready)
        self.assertIn("映射", result.issue_text)

    def test_build_runtime_visibility_summary_uses_file_mapping_labels(self) -> None:
        snapshot = ReplayRuntimeSnapshot(
            state=ReplayState.RUNNING,
            current_ts_ns=1_000_000,
            total_ts_ns=2_000_000,
            launch_source=ReplayLaunchSource.SCENARIO_BOUND,
        )
        snapshot.adapter_health["mock0"] = AdapterHealth(online=True, detail="ok", per_channel={0: True})
        bindings = [
            {
                "trace_file_id": "trace-a",
                "source_channel": 0,
                "source_bus_type": "CANFD",
                "adapter_id": "mock0",
                "logical_channel": 0,
                "physical_channel": 0,
                "bus_type": "CANFD",
            }
        ]
        trace_lookup = {
            "trace-a": TraceFileRecord(
                trace_id="trace-a",
                name="can.asc",
                original_path="/tmp/can.asc",
                library_path="/tmp/cache.asc",
                format="asc",
                imported_at="now",
            )
        }

        summary = _build_runtime_visibility_summary(snapshot, bindings, trace_lookup)

        self.assertIn("can.asc | CH0 | CANFD", summary.device_text)


if __name__ == "__main__":
    unittest.main()
