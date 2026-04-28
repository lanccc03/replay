from __future__ import annotations

from replay_platform.app_controller import ReplayApplication
from replay_platform.ui import window_presenters as _window_presenters
from replay_platform.ui.window_presenters import *  # noqa: F401,F403


def build_main_window(app_logic: ReplayApplication):
    from replay_platform.ui.main_window_view import MainWindow

    return MainWindow(app_logic)


def __getattr__(name: str):
    if name == "ScenarioEditorDialog":
        from replay_platform.ui.scenario_editor import ScenarioEditorDialog

        return ScenarioEditorDialog
    if name == "MainWindow":
        from replay_platform.ui.main_window_view import MainWindow

        return MainWindow
    if name == "CollectionItemDialog":
        from replay_platform.ui.collection_dialog import CollectionItemDialog

        return CollectionItemDialog
    if name == "BackgroundTask":
        from replay_platform.ui.qt_workers import BackgroundTask

        return BackgroundTask
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = tuple(_window_presenters.__all__) + (
    "build_main_window",
    "ScenarioEditorDialog",
    "MainWindow",
    "CollectionItemDialog",
    "BackgroundTask",
)
