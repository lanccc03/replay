from __future__ import annotations

import tests.bootstrap  # noqa: F401

import unittest

from replay_platform.ui.qss_loader import load_qss


class UiStylesTests(unittest.TestCase):
    def test_main_window_qss_loads_without_qt_dependency(self) -> None:
        qss = load_qss("main_window.qss")

        self.assertIn("QMainWindow", qss)
        self.assertIn('QPushButton[variant="primary"]', qss)

    def test_scenario_editor_qss_loads_without_qt_dependency(self) -> None:
        qss = load_qss("scenario_editor.qss")

        self.assertIn("QDialog#scenarioEditorDialog", qss)
        self.assertIn('QLineEdit[errorState="true"]', qss)


if __name__ == "__main__":
    unittest.main()
