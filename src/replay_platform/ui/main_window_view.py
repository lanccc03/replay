from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from replay_platform.app_controller import ReplayApplication, ReplayPreparation
from replay_platform.core import (
    FrameEnableRule,
    ReplayLaunchSource,
    ReplayState,
    ScenarioSpec,
    SignalOverride,
    TraceFileRecord,
)
from replay_platform.services.signal_catalog import MessageCatalogEntry, SignalCatalogEntry
from replay_platform.ui.qt_imports import *  # noqa: F403
from replay_platform.ui.window_presenters import *  # noqa: F403
from replay_platform.ui.main_window_actions import MainWindowActionsMixin
from replay_platform.ui.main_window_state import MainWindowStateMixin
from replay_platform.ui.main_window_ui import MainWindowUiMixin
from replay_platform.ui.scenario_editor import ScenarioEditorDialog
from replay_platform.ui.qt_workers import BackgroundTask


class MainWindow(QMainWindow, MainWindowMixin, MainWindowUiMixin, MainWindowStateMixin, MainWindowActionsMixin):

    def __init__(self, app_logic: ReplayApplication) -> None:
        super().__init__()
        self.app_logic = app_logic
        self._log_cursor = 0
        self._scenario_editor: Optional[ScenarioEditorDialog] = None
        self._current_scenario_payload = ScenarioSpec.from_dict(self._default_scenario_payload()).to_dict()
        self._override_catalog_channels: set[int] = set()
        self._override_catalog_statuses: dict[int, dict[str, Any]] = {}
        self._frame_enable_candidate_ids: dict[int, list[int]] = {}
        self._frame_enable_candidate_trace_ids: tuple[str, ...] = ()
        self._frame_enable_candidate_binding_signature: tuple[tuple[str, int, int, str], ...] = ()
        self._all_trace_records: list[TraceFileRecord] = []
        self._trace_lookup: dict[str, TraceFileRecord] = {}
        self._all_scenarios: list[ScenarioSpec] = []
        self._scenario_lookup: dict[str, ScenarioSpec] = {}
        self._trace_import_in_progress = False
        self._trace_import_thread: Optional[QThread] = None
        self._trace_import_worker: Optional[BackgroundTask] = None
        self._replay_prepare_in_progress = False
        self._replay_prepare_thread: Optional[QThread] = None
        self._replay_prepare_worker: Optional[BackgroundTask] = None
        self._replay_prepare_message = ""
        self.setWindowTitle("多总线回放与诊断平台")
        self.resize(1480, 980)
        self._build_ui()
        self._refresh_all()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_runtime_view)
        self._timer.start(250)
