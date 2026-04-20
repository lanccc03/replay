from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tests.bootstrap  # noqa: F401

try:
    from PySide6.QtWidgets import QApplication
    from replay_platform.app_controller import ReplayApplication
    from replay_platform.core import TraceFileRecord
    from replay_platform.ui.main_window import ScenarioEditorDialog, _new_binding_draft
except (ModuleNotFoundError, ImportError):  # pragma: no cover - optional UI dependency in test env
    QApplication = None
    ReplayApplication = None
    TraceFileRecord = None
    ScenarioEditorDialog = None
    _new_binding_draft = None


@unittest.skipIf(QApplication is None, "PySide6 未安装，跳过 Qt 对话框回归测试")
class ScenarioEditorDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._qt_app = QApplication.instance() or QApplication([])

    def test_apply_validation_visuals_does_not_reload_binding_editor(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dialog = ScenarioEditorDialog(
                ReplayApplication(Path(workspace)),
                trace_selection_supplier=lambda: [],
                on_payload_changed=lambda _payload: None,
                on_saved=lambda _payload: None,
            )
            try:
                dialog._draft_bindings = [_new_binding_draft(0)]
                dialog._refresh_binding_list(select_index=0)
                dialog._load_selected_binding_into_editor = Mock()  # type: ignore[method-assign]

                dialog._apply_validation_visuals()

                dialog._load_selected_binding_into_editor.assert_not_called()
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_binding_editor_shows_physical_channel_input(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dialog = ScenarioEditorDialog(
                ReplayApplication(Path(workspace)),
                trace_selection_supplier=lambda: [],
                on_payload_changed=lambda _payload: None,
                on_saved=lambda _payload: None,
            )
            try:
                dialog._draft_bindings = [_new_binding_draft(0)]
                dialog._refresh_binding_list(select_index=0)
                dialog.show()
                self._qt_app.processEvents()

                self.assertFalse(dialog.binding_physical_channel_edit.isHidden())
                self.assertTrue(dialog.binding_physical_channel_edit.isEnabled())
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_loading_binding_restores_physical_channel_text(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dialog = ScenarioEditorDialog(
                ReplayApplication(Path(workspace)),
                trace_selection_supplier=lambda: [],
                on_payload_changed=lambda _payload: None,
                on_saved=lambda _payload: None,
            )
            try:
                draft = _new_binding_draft(0)
                draft["physical_channel"] = "7"
                dialog._draft_bindings = [draft]
                dialog._refresh_binding_list(select_index=0)

                self.assertEqual("7", dialog.binding_physical_channel_edit.text())
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_binding_trace_metadata_uses_dialog_cache(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dialog = ScenarioEditorDialog(
                ReplayApplication(Path(workspace)),
                trace_selection_supplier=lambda: [],
                on_payload_changed=lambda _payload: None,
                on_saved=lambda _payload: None,
            )
            try:
                trace_record = TraceFileRecord(
                    trace_id="trace-a",
                    name="body.asc",
                    original_path=str(Path(workspace) / "body.asc"),
                    library_path=str(Path(workspace) / ".replay_platform" / "traces" / "body.asc"),
                    format="asc",
                    imported_at="now",
                )
                dialog.app_logic.list_traces = Mock(return_value=[trace_record])  # type: ignore[method-assign]
                dialog.app_logic.get_trace_source_summaries = Mock(  # type: ignore[method-assign]
                    return_value=[{"source_channel": 0, "bus_type": "CANFD", "frame_count": 2, "label": "CH0 | CANFD | 2帧"}]
                )

                dialog.load_payload(
                    {
                        "scenario_id": "scenario-1",
                        "name": "缓存验证",
                        "trace_file_ids": ["trace-a"],
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
                )

                dialog.app_logic.list_traces.reset_mock()
                dialog.app_logic.get_trace_source_summaries.reset_mock()
                dialog._trace_source_summary_cache.clear()

                first_lookup = dialog._binding_trace_lookup()
                second_lookup = dialog._binding_trace_lookup()
                first_summary = dialog._binding_trace_source_summaries("trace-a")
                second_summary = dialog._binding_trace_source_summaries("trace-a")

                self.assertIn("trace-a", first_lookup)
                self.assertEqual(first_lookup, second_lookup)
                self.assertEqual(first_summary, second_summary)
                dialog.app_logic.list_traces.assert_not_called()
                dialog.app_logic.get_trace_source_summaries.assert_called_once_with("trace-a")
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_replace_signal_overrides_updates_editor_collection(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dialog = ScenarioEditorDialog(
                ReplayApplication(Path(workspace)),
                trace_selection_supplier=lambda: [],
                on_payload_changed=lambda _payload: None,
                on_saved=lambda _payload: None,
            )
            try:
                dialog.load_payload(
                    {
                        "scenario_id": "scenario-1",
                        "name": "覆盖回填",
                        "trace_file_ids": [],
                        "bindings": [],
                        "database_bindings": [],
                        "signal_overrides": [],
                        "diagnostic_targets": [],
                        "diagnostic_actions": [],
                        "link_actions": [],
                        "metadata": {},
                    }
                )

                dialog.replace_signal_overrides(
                    [
                        {
                            "logical_channel": 0,
                            "message_id_or_pgn": 0x123,
                            "signal_name": "VehicleSpeed",
                            "value": 66,
                        }
                    ]
                )

                self.assertEqual(1, len(dialog._collection_data["signal_overrides"]))
                self.assertEqual("VehicleSpeed", dialog._collection_data["signal_overrides"][0]["signal_name"])
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_inline_database_binding_loads_for_selected_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dialog = ScenarioEditorDialog(
                ReplayApplication(Path(workspace)),
                trace_selection_supplier=lambda: [],
                on_payload_changed=lambda _payload: None,
                on_saved=lambda _payload: None,
            )
            try:
                dialog.app_logic.rebuild_override_preview = Mock(  # type: ignore[method-assign]
                    return_value={0: {"loaded": False, "error": "missing dbc"}}
                )
                dbc_path = str(Path(workspace) / "vehicle.dbc")

                dialog.load_payload(
                    {
                        "scenario_id": "scenario-1",
                        "name": "数据库内联",
                        "trace_file_ids": [],
                        "bindings": [
                            {
                                "adapter_id": "mock0",
                                "driver": "mock",
                                "logical_channel": 0,
                                "physical_channel": 0,
                                "bus_type": "CANFD",
                                "device_type": "MOCK",
                            }
                        ],
                        "database_bindings": [
                            {"logical_channel": 0, "path": dbc_path, "format": "dbc"},
                        ],
                        "signal_overrides": [],
                        "diagnostic_targets": [],
                        "diagnostic_actions": [],
                        "link_actions": [],
                        "metadata": {},
                    }
                )

                self.assertEqual("0", dialog.binding_database_channel_edit.text())
                self.assertEqual(dbc_path, dialog.binding_database_path_edit.text())
                self.assertIn("vehicle.dbc", dialog.binding_database_status_label.text())
                self.assertIn("vehicle.dbc", dialog.binding_list.item(0).text())
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_inline_database_binding_exports_compatible_payload(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dialog = ScenarioEditorDialog(
                ReplayApplication(Path(workspace)),
                trace_selection_supplier=lambda: [],
                on_payload_changed=lambda _payload: None,
                on_saved=lambda _payload: None,
            )
            try:
                dialog._draft_bindings = [_new_binding_draft(0)]
                dialog._refresh_binding_list(select_index=0)
                dbc_path = str(Path(workspace) / "inline.dbc")

                dialog.binding_database_path_edit.setText(dbc_path)
                dialog._apply_current_database_binding_path(refresh_status=False)

                result = dialog._validate_current_draft()

                self.assertEqual([], result.errors)
                self.assertIsNotNone(result.normalized_payload)
                self.assertEqual(
                    [{"logical_channel": 0, "path": dbc_path, "format": "dbc"}],
                    result.normalized_payload["database_bindings"],
                )
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_logical_channel_change_switches_inline_database_binding_without_moving_old_channel(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dialog = ScenarioEditorDialog(
                ReplayApplication(Path(workspace)),
                trace_selection_supplier=lambda: [],
                on_payload_changed=lambda _payload: None,
                on_saved=lambda _payload: None,
            )
            try:
                draft = _new_binding_draft(0)
                dialog._draft_bindings = [draft]
                dialog._database_binding_drafts = {
                    0: {"logical_channel": 0, "path": str(Path(workspace) / "lc0.dbc"), "format": "dbc"},
                    1: {"logical_channel": 1, "path": str(Path(workspace) / "lc1.dbc"), "format": "dbc"},
                }
                dialog._refresh_binding_list(select_index=0)

                self.assertTrue(dialog.binding_database_path_edit.text().endswith("lc0.dbc"))

                dialog.binding_logical_channel_edit.setText("1")
                dialog._binding_input_changed()

                self.assertTrue(dialog.binding_database_path_edit.text().endswith("lc1.dbc"))
                self.assertTrue(dialog._database_binding_drafts[0]["path"].endswith("lc0.dbc"))
                self.assertEqual("1", dialog._draft_bindings[0]["logical_channel"])
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_removing_last_binding_prunes_inline_database_binding(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dialog = ScenarioEditorDialog(
                ReplayApplication(Path(workspace)),
                trace_selection_supplier=lambda: [],
                on_payload_changed=lambda _payload: None,
                on_saved=lambda _payload: None,
            )
            try:
                dialog._draft_bindings = [_new_binding_draft(0)]
                dialog._database_binding_drafts = {
                    0: {"logical_channel": 0, "path": str(Path(workspace) / "lc0.dbc"), "format": "dbc"}
                }
                dialog._refresh_binding_list(select_index=0)

                dialog._remove_selected_binding()

                self.assertEqual([], dialog._draft_bindings)
                self.assertNotIn(0, dialog._database_binding_drafts)
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_duplicate_database_bindings_warn_and_save_last_value(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dialog = ScenarioEditorDialog(
                ReplayApplication(Path(workspace)),
                trace_selection_supplier=lambda: [],
                on_payload_changed=lambda _payload: None,
                on_saved=lambda _payload: None,
            )
            try:
                dialog.app_logic.rebuild_override_preview = Mock(return_value={0: {"loaded": False, "error": "missing"}})  # type: ignore[method-assign]
                first_path = str(Path(workspace) / "first.dbc")
                last_path = str(Path(workspace) / "last.dbc")

                dialog.load_payload(
                    {
                        "scenario_id": "scenario-1",
                        "name": "重复数据库绑定",
                        "trace_file_ids": [],
                        "bindings": [
                            {
                                "adapter_id": "mock0",
                                "driver": "mock",
                                "logical_channel": 0,
                                "physical_channel": 0,
                                "bus_type": "CANFD",
                                "device_type": "MOCK",
                            }
                        ],
                        "database_bindings": [
                            {"logical_channel": 0, "path": first_path, "format": "dbc"},
                            {"logical_channel": 0, "path": last_path, "format": "dbc"},
                        ],
                        "signal_overrides": [],
                        "diagnostic_targets": [],
                        "diagnostic_actions": [],
                        "link_actions": [],
                        "metadata": {},
                    }
                )

                result = dialog._validate_current_draft()

                self.assertEqual([], result.errors)
                self.assertIsNotNone(result.normalized_payload)
                self.assertEqual(
                    [{"logical_channel": 0, "path": last_path, "format": "dbc"}],
                    result.normalized_payload["database_bindings"],
                )
                self.assertTrue(any("LC0" in warning.message for warning in result.warnings))
            finally:
                dialog.close()
                dialog.deleteLater()

    def test_orphan_database_bindings_are_listed_and_removable(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            dialog = ScenarioEditorDialog(
                ReplayApplication(Path(workspace)),
                trace_selection_supplier=lambda: [],
                on_payload_changed=lambda _payload: None,
                on_saved=lambda _payload: None,
            )
            try:
                dialog.app_logic.rebuild_override_preview = Mock(return_value={4: {"loaded": False, "error": "missing"}})  # type: ignore[method-assign]
                orphan_path = str(Path(workspace) / "orphan.dbc")

                dialog.load_payload(
                    {
                        "scenario_id": "scenario-1",
                        "name": "孤立数据库绑定",
                        "trace_file_ids": [],
                        "bindings": [],
                        "database_bindings": [
                            {"logical_channel": 4, "path": orphan_path, "format": "dbc"},
                        ],
                        "signal_overrides": [],
                        "diagnostic_targets": [],
                        "diagnostic_actions": [],
                        "link_actions": [],
                        "metadata": {},
                    }
                )

                self.assertEqual(1, dialog.orphan_database_list.count())
                self.assertIn("orphan.dbc", dialog.orphan_database_label.text())

                dialog.orphan_database_list.setCurrentRow(0)
                dialog._update_orphan_database_buttons()
                self.assertTrue(dialog.remove_orphan_database_button.isEnabled())

                dialog._remove_selected_orphan_database_binding()

                self.assertEqual(0, dialog.orphan_database_list.count())
                self.assertNotIn(4, dialog._database_binding_drafts)
            finally:
                dialog.close()
                dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
