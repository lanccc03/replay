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
from replay_platform.ui.scenario_editor_bindings import ScenarioEditorBindingsMixin
from replay_platform.ui.scenario_editor_ui import ScenarioEditorUiMixin
from replay_platform.ui.scenario_editor_validation import ScenarioEditorValidationMixin


class ScenarioEditorDialog(QDialog, MainWindowMixin, ScenarioEditorUiMixin, ScenarioEditorBindingsMixin, ScenarioEditorValidationMixin):

    def __init__(
        self,
        app_logic: ReplayApplication,
        trace_selection_supplier: Callable[[], list[str]],
        on_payload_changed: Callable[[dict], None],
        on_saved: Callable[[dict], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.app_logic = app_logic
        self._trace_selection_supplier = trace_selection_supplier
        self._on_payload_changed = on_payload_changed
        self._on_saved = on_saved
        self._last_saved_payload: Optional[dict] = None
        self._last_valid_payload: Optional[dict] = None
        self._validation_errors: list[ValidationIssue] = []
        self._validation_warnings: list[ValidationIssue] = []
        self._feedback_message = ""
        self._feedback_tone = "muted"
        self._is_dirty = False
        self._raw_dirty = False
        self._suspend_updates = False
        self._section_boxes: dict[str, QGroupBox] = {}
        self._section_titles: dict[str, str] = {}
        self._field_widgets: dict[str, QWidget] = {}
        self._field_error_labels: dict[str, QLabel] = {}
        self._binding_field_widgets: dict[str, QWidget] = {}
        self._binding_field_error_labels: dict[str, QLabel] = {}
        self._binding_error_counts: dict[int, int] = {}
        self._binding_list_error_messages: dict[int, list[str]] = {}
        self._draft_bindings: list[dict] = []
        self._database_binding_drafts: dict[int, dict] = {}
        self._database_binding_duplicate_counts: dict[int, int] = {}
        self._database_binding_statuses: dict[int, dict[str, Any]] = {}
        self._collection_data = {
            "database_bindings": [],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
        }
        self._collection_sections: dict[str, dict[str, Any]] = {}
        self._trace_records_cache: dict[str, TraceFileRecord] = {}
        self._trace_source_summary_cache: dict[str, list[dict[str, Any]]] = {}
        self._validation_timer = QTimer(self)
        self._validation_timer.setSingleShot(True)
        self._validation_timer.setInterval(150)
        self._validation_timer.timeout.connect(self._run_live_validation)
        self.setObjectName("scenarioEditorDialog")
        self.setWindowTitle("场景编辑器")
        self.resize(1280, 980)
        self._build_ui()
        self._apply_editor_styles()
        self.load_payload(self._default_scenario_payload())

    def current_scenario_id(self) -> str:
        return self.scenario_id_edit.text().strip()

    def closeEvent(self, event) -> None:
        if self._confirm_close_with_unsaved_changes():
            event.accept()
            return
        event.ignore()
