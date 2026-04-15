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
except ModuleNotFoundError:  # pragma: no cover - optional UI dependency in test env
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


if __name__ == "__main__":
    unittest.main()
