from __future__ import annotations

import sys

from replay_platform.app_controller import ReplayApplication
from replay_platform.ui.main_window import build_main_window


def run_qt_app(app_logic: ReplayApplication) -> None:
    from PySide6.QtWidgets import QApplication

    qt_app = QApplication(sys.argv)
    window = build_main_window(app_logic)
    window.show()
    raise SystemExit(qt_app.exec())

