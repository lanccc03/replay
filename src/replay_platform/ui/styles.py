from __future__ import annotations

from replay_platform.ui.qss_loader import load_qss
from replay_platform.ui.qt_imports import QLabel, QPushButton, QWidget


SCENARIO_EDITOR_STYLESHEET = load_qss("scenario_editor.qss")
MAIN_WINDOW_STYLESHEET = load_qss("main_window.qss")


def refresh_widget_style(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def set_button_variant(button: QPushButton, variant: str) -> None:
    button.setProperty("variant", variant)
    refresh_widget_style(button)


def set_badge(label: QLabel, text: str, tone: str) -> None:
    label.setText(text)
    label.setProperty("tone", tone)
    refresh_widget_style(label)


def set_tone(label: QLabel, tone: str) -> None:
    label.setProperty("tone", tone)
    refresh_widget_style(label)
