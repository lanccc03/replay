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
from replay_platform.ui.scenario_editor import ScenarioEditorDialog


class MainWindowStateMixin:

    def _set_trace_operation_message(self, message: str, *, tone: Optional[str] = None) -> None:
        self.trace_operation_label.setText(message)
        self.trace_operation_label.setProperty("tone", tone)
        self._refresh_style(self.trace_operation_label)
        self.trace_operation_label.setVisible(bool(message))

    def _set_trace_import_busy(self, busy: bool, *, path: str = "") -> None:
        self._trace_import_in_progress = busy
        if busy:
            filename = Path(path).name if path else ""
            message = "正在导入回放文件，请稍候。"
            if filename:
                message = f"正在导入：{filename}"
            self._set_trace_operation_message(message)
        self._refresh_busy_controls()

    def _clear_trace_import_task(self) -> None:
        self._trace_import_thread = None
        self._trace_import_worker = None
        self._set_trace_import_busy(False)

    def _set_replay_prepare_busy(self, busy: bool, *, trace_count: int = 0) -> None:
        self._replay_prepare_in_progress = busy
        if busy:
            message = "运行状态：正在准备回放，请稍候。"
            if trace_count > 0:
                message = f"运行状态：正在准备 {trace_count} 个回放文件，请稍候。"
            self._replay_prepare_message = message
        else:
            self._replay_prepare_message = ""
        self._refresh_busy_controls()
        self._refresh_runtime_state()

    def _clear_replay_prepare_task(self) -> None:
        self._replay_prepare_thread = None
        self._replay_prepare_worker = None
        self._set_replay_prepare_busy(False)

    def _refresh_busy_controls(self) -> None:
        replay_locked = self._replay_prepare_in_progress
        self.trace_search_edit.setEnabled(not replay_locked)
        self.trace_list.setEnabled(not replay_locked)
        self.scenario_search_edit.setEnabled(not replay_locked)
        self.scenario_list.setEnabled(not replay_locked)
        self.override_channel.setEnabled(not replay_locked)
        self.override_message.setEnabled(not replay_locked)
        self.override_signal.setEnabled(not replay_locked)
        self.override_value.setEnabled(not replay_locked)
        self.override_apply.setEnabled(not replay_locked)
        self.load_scenario_overrides_button.setEnabled(not replay_locked)
        self.write_back_overrides_button.setEnabled(not replay_locked)
        self.override_table.setEnabled(not replay_locked)
        self.delete_override_button.setEnabled(not replay_locked and bool(self.override_table.selectedIndexes()))
        self.clear_overrides_button.setEnabled(not replay_locked and self.override_table.rowCount() > 0)
        self.frame_enable_channel.setEnabled(not replay_locked)
        self.frame_enable_message.setEnabled(not replay_locked)
        self.frame_enable_status.setEnabled(not replay_locked)
        self.frame_enable_apply.setEnabled(not replay_locked and self._current_frame_enable_message_id() is not None)
        self.frame_enable_table.setEnabled(not replay_locked)
        self.delete_frame_enable_button.setEnabled(not replay_locked and bool(self.frame_enable_table.selectedIndexes()))
        self.clear_frame_enable_button.setEnabled(not replay_locked and self.frame_enable_table.rowCount() > 0)
        self.open_editor_button.setEnabled(not replay_locked)
        self.edit_scenario_button.setEnabled(not replay_locked and self._selected_scenario_record() is not None)
        if self._scenario_editor is not None:
            self._scenario_editor.setEnabled(not replay_locked)
        self._update_trace_actions()
        self._update_scenario_actions()

    def _ensure_scenario_editor(self) -> ScenarioEditorDialog:
        if self._scenario_editor is None:
            self._scenario_editor = ScenarioEditorDialog(
                self.app_logic,
                trace_selection_supplier=self._selected_trace_ids,
                on_payload_changed=self._set_current_scenario_payload,
                on_saved=self._handle_saved_scenario,
                parent=self,
            )
        return self._scenario_editor

    def _open_scenario_editor(self, payload: dict) -> None:
        editor = self._ensure_scenario_editor()
        if not editor.load_payload(payload, prompt_on_unsaved=editor.isVisible()):
            return
        editor.show()
        editor.raise_()
        editor.activateWindow()

    def _handle_saved_scenario(self, payload: dict) -> None:
        self._set_current_scenario_payload(payload)
        self._refresh_scenarios()
        self._select_scenario(payload.get("scenario_id", ""))

    def _copy_scenario_id(self) -> None:
        scenario_id = _display_text(self._current_scenario_payload.get("scenario_id", "")).strip()
        if scenario_id:
            QApplication.clipboard().setText(scenario_id)

    def _select_trace(self, trace_id: str) -> None:
        for index in range(self.trace_list.count()):
            item = self.trace_list.item(index)
            if item.data(USER_ROLE) == trace_id:
                self.trace_list.setCurrentItem(item)
                item.setSelected(True)
                return

    def _select_scenario(self, scenario_id: str) -> None:
        for index in range(self.scenario_list.count()):
            item = self.scenario_list.item(index)
            if item.data(USER_ROLE) == scenario_id:
                self.scenario_list.setCurrentItem(item)
                item.setSelected(True)
                return

    def _set_current_scenario_payload(self, payload: dict) -> None:
        previous_scenario_id = _display_text(self._current_scenario_payload.get("scenario_id", "")).strip()
        try:
            normalized = ScenarioSpec.from_dict(payload).to_dict()
        except Exception:
            normalized = _clone_jsonable(payload)
        next_scenario_id = _display_text(normalized.get("scenario_id", "")).strip()
        if previous_scenario_id and next_scenario_id != previous_scenario_id:
            self.app_logic.clear_workspace_signal_overrides()
        self._current_scenario_payload = normalized
        self._sync_override_catalogs()
        self._refresh_overrides()
        self._refresh_frame_enable_candidates()
        self._refresh_current_scenario_summary()
        self._refresh_runtime_state()

    def _current_launch_assessment(self) -> ScenarioLaunchAssessment:
        return _assess_scenario_launch(self._current_scenario_payload, self._selected_trace_ids())

    def _selected_trace_ids(self) -> list[str]:
        return [item.data(USER_ROLE) for item in self.trace_list.selectedItems()]

    def _selected_trace_record(self) -> Optional[TraceFileRecord]:
        item = self.trace_list.currentItem()
        if item is None:
            selected = self.trace_list.selectedItems()
            item = selected[0] if selected else None
        if item is None:
            return None
        return self._trace_lookup.get(item.data(USER_ROLE))

    def _selected_trace_records(self) -> list[TraceFileRecord]:
        return [self._trace_lookup[trace_id] for trace_id in self._selected_trace_ids() if trace_id in self._trace_lookup]

    def _selected_scenario_record(self) -> Optional[ScenarioSpec]:
        selected = self.scenario_list.selectedItems()
        if not selected:
            return None
        return self._scenario_lookup.get(selected[0].data(USER_ROLE))

    def _update_trace_actions(self) -> None:
        busy = self._trace_import_in_progress or self._replay_prepare_in_progress
        self.import_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.delete_trace_button.setEnabled(not busy and self._selected_trace_record() is not None)

    def _update_scenario_actions(self) -> None:
        busy = self._replay_prepare_in_progress
        has_selection = self._selected_scenario_record() is not None
        self.new_scenario_button.setEnabled(not busy)
        self.edit_scenario_button.setEnabled(not busy and has_selection)
        self.delete_scenario_button.setEnabled(not busy and has_selection)

    def _sync_override_catalogs(self) -> None:
        try:
            scenario = ScenarioSpec.from_dict(self._current_scenario_payload)
        except Exception:
            self._override_catalog_channels = set()
            self._override_catalog_statuses = {}
            self._refresh_override_candidates()
            return
        statuses = self.app_logic.rebuild_override_preview(scenario.database_bindings)
        self._override_catalog_statuses = statuses
        self._override_catalog_channels = {
            logical_channel
            for logical_channel, status in statuses.items()
            if status.get("loaded")
        }
        self._refresh_override_candidates()

    def _effective_frame_enable_trace_ids(self) -> tuple[str, ...]:
        trace_ids = [
            _display_text(trace_id).strip()
            for trace_id in self._current_scenario_payload.get("trace_file_ids", [])
            if _display_text(trace_id).strip()
        ]
        if not trace_ids:
            trace_ids = [
                _display_text(trace_id).strip()
                for trace_id in self._selected_trace_ids()
                if _display_text(trace_id).strip()
            ]
        return tuple(sorted(set(trace_ids)))

    def _trace_message_id_summaries(self, trace_id: str) -> list[dict]:
        if not trace_id:
            return []
        try:
            return self.app_logic.get_trace_message_id_summaries(trace_id)
        except Exception:
            return []

    def _frame_enable_binding_signature(self) -> tuple[tuple[str, int, int, str], ...]:
        signature: list[tuple[str, int, int, str]] = []
        for binding in self._current_scenario_payload.get("bindings", []):
            trace_file_id = _display_text(binding.get("trace_file_id", "")).strip()
            source_channel = _parse_optional_int_text(binding.get("source_channel"))
            logical_channel = _parse_optional_int_text(binding.get("logical_channel"))
            source_bus_type = _display_text(binding.get("source_bus_type", "")).strip().upper()
            if not trace_file_id or source_channel is None or logical_channel is None or not source_bus_type:
                continue
            signature.append((trace_file_id, logical_channel, source_channel, source_bus_type))
        return tuple(sorted(signature))

    def _refresh_frame_enable_candidates(self, *, force: bool = False) -> None:
        trace_ids = self._effective_frame_enable_trace_ids()
        binding_signature = self._frame_enable_binding_signature()
        if (
            not force
            and trace_ids == self._frame_enable_candidate_trace_ids
            and binding_signature == self._frame_enable_candidate_binding_signature
        ):
            self._refresh_frame_enable_message_options()
            return
        summary_lookup = {
            trace_id: self._trace_message_id_summaries(trace_id)
            for trace_id in trace_ids
        }
        self._frame_enable_candidate_ids = _build_frame_enable_candidate_ids_from_trace_summaries(
            trace_ids,
            self._current_scenario_payload.get("bindings", []),
            summary_lookup,
        )
        self._frame_enable_candidate_trace_ids = trace_ids
        self._frame_enable_candidate_binding_signature = binding_signature
        self._refresh_frame_enable_message_options()

    def _refresh_current_scenario_summary(self) -> None:
        payload = self._current_scenario_payload
        assessment = self._current_launch_assessment()
        business = _build_scenario_business_summary(
            payload,
            self._trace_lookup,
            self._override_catalog_statuses,
        )
        self.current_scenario_name.setText(payload.get("name", "未命名场景"))
        self.current_scenario_counts.setText(_build_scenario_counts_summary(payload))
        self.current_scenario_trace_text.setText(business.trace_text)
        self.current_scenario_binding_text.setText(business.binding_text)
        self.current_scenario_database_text.setText(business.database_text)
        self.current_scenario_source.setText(assessment.source_text)
        self.current_scenario_id.setText(f"场景 ID：{payload.get('scenario_id', '')}")
        self.copy_scenario_id_button.setEnabled(bool(payload.get("scenario_id")))
        self._set_badge(self.current_scenario_badge, assessment.badge_text, assessment.tone)
        if assessment.issue_text:
            self.current_scenario_issue.setText(assessment.issue_text)
            self._set_tone(self.current_scenario_issue, "error" if assessment.tone == "error" else "warn")
            self.current_scenario_issue.show()
        else:
            self.current_scenario_issue.clear()
            self.current_scenario_issue.hide()

    def _refresh_runtime_view(self) -> None:
        self._refresh_logs()
        self._refresh_runtime_state()
        self._refresh_frame_enables()

    def _refresh_runtime_state(self) -> None:
        assessment = self._current_launch_assessment()
        snapshot = self.app_logic.runtime_snapshot()
        if self._replay_prepare_in_progress:
            self.start_button.setEnabled(False)
            self.pause_button.setEnabled(False)
            self.resume_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.loop_playback_checkbox.setEnabled(False)
            self._set_badge(self.runtime_badge, "准备中", "info")
            self.status_label.setText(self._replay_prepare_message or "运行状态：正在准备回放，请稍候。")
            self.stats_label.setText(_format_replay_stats(self.app_logic.engine.stats, snapshot))
            self.runtime_progress_label.setText("进度：正在准备回放数据，请稍候。")
            self.runtime_source_label.setText(assessment.source_text)
            self.runtime_device_label.setText(assessment.detail_text)
            self.runtime_launch_label.setText("启动动作：准备完成后会自动开始回放。")
            return
        buttons = _playback_button_state(snapshot.state, assessment.ready)
        self.start_button.setEnabled(buttons.start_enabled)
        self.pause_button.setEnabled(buttons.pause_enabled)
        self.resume_button.setEnabled(buttons.resume_enabled)
        self.stop_button.setEnabled(buttons.stop_enabled)
        self.loop_playback_checkbox.setEnabled(snapshot.state == ReplayState.STOPPED)

        if snapshot.state == ReplayState.RUNNING:
            self._set_badge(self.runtime_badge, "运行中", "info")
            self.status_label.setText("运行状态：回放进行中。")
        elif snapshot.state == ReplayState.PAUSED:
            self._set_badge(self.runtime_badge, "已暂停", "warn")
            self.status_label.setText("运行状态：回放已暂停。")
        else:
            self._set_badge(self.runtime_badge, "已停止", "muted")
            self.status_label.setText("运行状态：已停止。")
        self.stats_label.setText(_format_replay_stats(self.app_logic.engine.stats, snapshot))

        summary = _build_runtime_visibility_summary(
            snapshot,
            self._current_scenario_payload.get("bindings", []),
            self._trace_lookup,
        )
        self.runtime_progress_label.setText(summary.progress_text)
        self.runtime_source_label.setText(summary.source_text)
        self.runtime_device_label.setText(summary.device_text)
        self.runtime_launch_label.setText(summary.launch_text)

    def _refresh_all(self) -> None:
        self._refresh_traces()
        self._refresh_scenarios()
        self._sync_override_catalogs()
        self._refresh_overrides()
        self._refresh_frame_enables()
        self._refresh_current_scenario_summary()
        self._refresh_runtime_state()
        self._refresh_logs()

    def _refresh_traces(self) -> None:
        self._all_trace_records = list(self.app_logic.list_traces())
        self._trace_lookup = {record.trace_id: record for record in self._all_trace_records}
        self._render_trace_list()
        self._refresh_frame_enable_candidates(force=True)
        if self._scenario_editor is not None:
            self._scenario_editor.refresh_trace_choices()

    def _render_trace_list(self) -> None:
        selected_trace_ids = set(self._selected_trace_ids())
        filtered_records = _filter_trace_records(self._all_trace_records, self.trace_search_edit.text())
        self.trace_count_label.setText(f"匹配 {len(filtered_records)} / 总 {len(self._all_trace_records)} 个文件")
        self.trace_list.blockSignals(True)
        self.trace_list.clear()
        for record in filtered_records:
            item = QListWidgetItem(f"{record.name} | {record.format.upper()} | {record.event_count} 帧")
            item.setData(USER_ROLE, record.trace_id)
            item.setSelected(record.trace_id in selected_trace_ids)
            self.trace_list.addItem(item)
        self.trace_list.blockSignals(False)
        self.trace_selection_summary.setText(_build_trace_selection_summary(self._selected_trace_records()))
        self._update_trace_actions()
        self._refresh_current_scenario_summary()
        self._refresh_runtime_state()

    def _refresh_scenarios(self) -> None:
        self._all_scenarios = list(self.app_logic.list_scenarios())
        self._scenario_lookup = {scenario.scenario_id: scenario for scenario in self._all_scenarios}
        self._render_scenario_list()

    def _render_scenario_list(self) -> None:
        current_scenario_id = self._current_scenario_payload.get("scenario_id", "")
        filtered_scenarios = _filter_scenarios(self._all_scenarios, self.scenario_search_edit.text())
        self.scenario_count_label.setText(f"匹配 {len(filtered_scenarios)} / 总 {len(self._all_scenarios)} 个场景")
        self.scenario_list.blockSignals(True)
        self.scenario_list.clear()
        for scenario in filtered_scenarios:
            item = QListWidgetItem(scenario.name)
            item.setData(USER_ROLE, scenario.scenario_id)
            item.setSelected(scenario.scenario_id == current_scenario_id)
            self.scenario_list.addItem(item)
        self.scenario_list.blockSignals(False)
        if current_scenario_id:
            self._select_scenario(current_scenario_id)
        self.scenario_selection_summary.setText(_build_scenario_selection_summary(self._selected_scenario_record()))
        self._update_scenario_actions()

    def _refresh_logs(self) -> None:
        base_index, logs = self.app_logic.log_snapshot()
        if not logs:
            self._log_cursor = base_index
            self.log_view.clear()
            self.log_content_stack.setCurrentIndex(0)
            return
        self.log_content_stack.setCurrentIndex(1)
        mode, offset = _plan_log_refresh(self._log_cursor, base_index, len(logs))
        if mode == "reset":
            self.log_view.setPlainText("\n".join(logs))
        else:
            for entry in logs[offset:]:
                self.log_view.appendPlainText(entry)
        self._log_cursor = base_index + len(logs)
        if self.auto_scroll_checkbox.isChecked():
            scrollbar = self.log_view.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _handle_log_level_changed(self, option: str) -> None:
        self.app_logic.apply_log_level_preset(_parse_log_level_option(option))
        self._refresh_log_level_hint()

    def _refresh_log_level_hint(self) -> None:
        try:
            preset = _parse_log_level_option(self.log_level_combo.currentText())
        except ValueError:
            preset = self.app_logic.current_log_level_preset()
        self.log_level_hint.setText(_build_log_level_hint(preset))

    def _refresh_overrides(self) -> None:
        selected_keys = {
            self.override_table.item(index.row(), 0).data(USER_ROLE)
            for index in self.override_table.selectedIndexes()
            if self.override_table.item(index.row(), 0) is not None
        }
        overrides = self.app_logic.list_workspace_signal_overrides()
        self.override_content_stack.setCurrentIndex(1 if overrides else 0)
        self.override_table.setRowCount(len(overrides))
        for row, override in enumerate(overrides):
            key = (override.logical_channel, override.message_id_or_pgn, override.signal_name)
            channel_item = QTableWidgetItem(str(override.logical_channel))
            channel_item.setData(USER_ROLE, key)
            channel_item.setSelected(key in selected_keys)
            self.override_table.setItem(row, 0, channel_item)
            self.override_table.setItem(row, 1, QTableWidgetItem(hex(override.message_id_or_pgn)))
            self.override_table.setItem(row, 2, QTableWidgetItem(override.signal_name))
            self.override_table.setItem(row, 3, QTableWidgetItem(_format_table_value(override.value)))
        self._update_override_actions()

    def _refresh_frame_enables(self) -> None:
        selected_keys = {
            self.frame_enable_table.item(index.row(), 0).data(USER_ROLE)
            for index in self.frame_enable_table.selectedIndexes()
            if self.frame_enable_table.item(index.row(), 0) is not None
        }
        rules = self.app_logic.frame_enables.list_rules()
        self.frame_enable_content_stack.setCurrentIndex(1 if rules else 0)
        self.frame_enable_table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            key = (rule.logical_channel, rule.message_id)
            channel_item = QTableWidgetItem(str(rule.logical_channel))
            channel_item.setData(USER_ROLE, key)
            channel_item.setSelected(key in selected_keys)
            self.frame_enable_table.setItem(row, 0, channel_item)
            self.frame_enable_table.setItem(row, 1, QTableWidgetItem(hex(rule.message_id)))
            self.frame_enable_table.setItem(row, 2, QTableWidgetItem(_frame_enable_status_text(rule.enabled)))
        self._update_frame_enable_actions()

    def _refresh_override_candidates(self) -> None:
        self._refresh_override_message_options()
        self._refresh_override_signal_options()
        self._refresh_override_catalog_status()
        self._refresh_override_signal_hint()
        self._update_override_actions()

    def _refresh_override_message_options(self) -> None:
        current_text = self.override_message.currentText().strip()
        items = [""] + [
            _format_override_message_option(entry)
            for entry in self._available_messages(self.override_channel.value())
        ]
        self.override_message.blockSignals(True)
        self.override_message.clear()
        self.override_message.addItems(items)
        self.override_message.setCurrentText(current_text)
        self.override_message.blockSignals(False)

    def _refresh_frame_enable_message_options(self) -> None:
        current_text = self.frame_enable_message.currentText().strip()
        items = [""] + [hex(message_id) for message_id in self._available_frame_enable_message_ids(self.frame_enable_channel.value())]
        self.frame_enable_message.blockSignals(True)
        self.frame_enable_message.clear()
        self.frame_enable_message.addItems(items)
        self.frame_enable_message.setCurrentText(current_text)
        self.frame_enable_message.blockSignals(False)

    def _refresh_override_signal_options(self) -> None:
        current_text = self.override_signal.currentText().strip()
        message_id = self._current_override_message_id()
        signal_names: list[str] = []
        if message_id is not None and self.override_channel.value() in self._override_catalog_channels:
            signal_names = [
                entry.signal_name
                for entry in self.app_logic.signal_overrides.list_signals(self.override_channel.value(), message_id)
            ]
        self.override_signal.blockSignals(True)
        self.override_signal.clear()
        self.override_signal.addItems([""] + signal_names)
        self.override_signal.setCurrentText(current_text)
        self.override_signal.blockSignals(False)
        self._refresh_override_signal_hint()

    def _refresh_override_catalog_status(self) -> None:
        label_map = {}
        try:
            scenario = ScenarioSpec.from_dict(self._current_scenario_payload)
        except Exception:
            scenario = None
        if scenario is not None:
            label_map = _binding_label_map(scenario.bindings, self._trace_lookup)
        self.override_catalog_status.setText(
            _build_override_catalog_status_text(self._override_catalog_statuses, label_map=label_map)
        )

    def _refresh_override_signal_hint(self) -> None:
        self.override_signal_hint.setText(_build_signal_catalog_hint(self._current_override_signal_entry()))

    def _available_messages(self, logical_channel: int) -> list[MessageCatalogEntry]:
        if logical_channel not in self._override_catalog_channels:
            return []
        return self.app_logic.signal_overrides.list_messages(logical_channel)

    def _available_frame_enable_message_ids(self, logical_channel: int) -> list[int]:
        return self._frame_enable_candidate_ids.get(logical_channel, [])

    def _current_override_message_id(self) -> Optional[int]:
        return _parse_message_combo_text(self.override_message.currentText())

    def _current_override_signal_entry(self) -> Optional[SignalCatalogEntry]:
        message_id = self._current_override_message_id()
        if message_id is None or self.override_channel.value() not in self._override_catalog_channels:
            return None
        signal_name = self.override_signal.currentText().strip()
        for entry in self.app_logic.signal_overrides.list_signals(self.override_channel.value(), message_id):
            if entry.signal_name == signal_name:
                return entry
        return None

    def _current_frame_enable_message_id(self) -> Optional[int]:
        text = self.frame_enable_message.currentText().strip()
        if not text:
            return None
        try:
            return int(text, 0)
        except ValueError:
            return None

    def _update_override_actions(self) -> None:
        self._refresh_override_signal_hint()
        if self._replay_prepare_in_progress:
            self.delete_override_button.setEnabled(False)
            self.clear_overrides_button.setEnabled(False)
            self.override_apply.setEnabled(False)
            self.load_scenario_overrides_button.setEnabled(False)
            self.write_back_overrides_button.setEnabled(False)
            return
        has_selection = bool(self.override_table.selectedIndexes())
        has_rows = self.override_table.rowCount() > 0
        message_id = self._current_override_message_id()
        signal_name = self.override_signal.currentText().strip()
        value_text = self.override_value.text().strip()
        self.delete_override_button.setEnabled(has_selection)
        self.clear_overrides_button.setEnabled(has_rows)
        self.override_apply.setEnabled(message_id is not None and bool(signal_name) and bool(value_text))
        self.load_scenario_overrides_button.setEnabled(True)
        self.write_back_overrides_button.setEnabled(has_rows)

    def _update_frame_enable_actions(self) -> None:
        if self._replay_prepare_in_progress:
            self.delete_frame_enable_button.setEnabled(False)
            self.clear_frame_enable_button.setEnabled(False)
            self.frame_enable_apply.setEnabled(False)
            return
        has_selection = bool(self.frame_enable_table.selectedIndexes())
        has_rows = self.frame_enable_table.rowCount() > 0
        message_id = self._current_frame_enable_message_id()
        self.delete_frame_enable_button.setEnabled(has_selection)
        self.clear_frame_enable_button.setEnabled(has_rows)
        self.frame_enable_apply.setEnabled(message_id is not None)
