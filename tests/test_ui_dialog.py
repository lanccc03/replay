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
    from replay_platform.ui.main_window import ScenarioEditorDialog, _new_binding_draft
except ModuleNotFoundError:  # pragma: no cover - optional UI dependency in test env
    QApplication = None
    ReplayApplication = None
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


if __name__ == "__main__":
    unittest.main()
