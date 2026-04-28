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


class ScenarioEditorBindingsMixin:

    def refresh_trace_choices(self) -> None:
        self._refresh_trace_library_cache()
        self._suspend_updates = True
        self._populate_trace_choices(set(self._checked_trace_ids()))
        self._suspend_updates = False
        self._handle_trace_selection_changed()
        self._run_live_validation()

    def _refresh_trace_library_cache(self) -> None:
        self._trace_records_cache = {record.trace_id: record for record in self.app_logic.list_traces()}
        self._trace_source_summary_cache = {
            trace_id: summaries
            for trace_id, summaries in self._trace_source_summary_cache.items()
            if trace_id in self._trace_records_cache
        }

    def _populate_trace_choices(self, checked_trace_ids: set[str]) -> None:
        existing = {record.trace_id: record for record in self.app_logic.list_traces()}
        self.scenario_trace_list.clear()
        for record in existing.values():
            item = QListWidgetItem(f"{record.name} | {record.format.upper()} | {record.event_count} 帧")
            item.setData(USER_ROLE, record.trace_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(
                Qt.CheckState.Checked if record.trace_id in checked_trace_ids else Qt.CheckState.Unchecked
            )
            self.scenario_trace_list.addItem(item)
        missing_ids = sorted(trace_id for trace_id in checked_trace_ids if trace_id not in existing)
        for trace_id in missing_ids:
            item = QListWidgetItem(f"缺失文件 | {trace_id}")
            item.setData(USER_ROLE, trace_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked)
            item.setForeground(QColor("#b45309"))
            self.scenario_trace_list.addItem(item)
        self._refresh_trace_choice_labels()
        self._sync_list_height(self.scenario_trace_list, min_rows=3)

    def _refresh_trace_choice_labels(self) -> None:
        existing = {record.trace_id: record for record in self.app_logic.list_traces()}
        for index in range(self.scenario_trace_list.count()):
            item = self.scenario_trace_list.item(index)
            trace_id = _display_text(item.data(USER_ROLE)).strip()
            mapping_text = _trace_mapping_completion_text(trace_id, self._draft_bindings)
            record = existing.get(trace_id)
            if record is None:
                item.setText(f"缺失文件 | {trace_id} | {mapping_text}")
                continue
            item.setText(f"{record.name} | {record.format.upper()} | {record.event_count} 帧 | {mapping_text}")

    def _checked_trace_ids(self) -> list[str]:
        trace_ids = []
        for index in range(self.scenario_trace_list.count()):
            item = self.scenario_trace_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                trace_ids.append(item.data(USER_ROLE))
        return trace_ids

    def _binding_trace_lookup(self) -> dict[str, TraceFileRecord]:
        return dict(self._trace_records_cache)

    def _binding_trace_source_summaries(self, trace_id: str) -> list[dict]:
        if not trace_id:
            return []
        cached = self._trace_source_summary_cache.get(trace_id)
        if cached is not None:
            return [dict(item) for item in cached]
        try:
            summaries = [dict(item) for item in self.app_logic.get_trace_source_summaries(trace_id)]
        except Exception:
            return []
        self._trace_source_summary_cache[trace_id] = summaries
        return [dict(item) for item in summaries]

    def _database_binding_items(self) -> list[dict]:
        return _database_binding_items_from_map(self._database_binding_drafts)

    def _database_binding_for_channel(self, logical_channel: Optional[int]) -> Optional[dict]:
        if logical_channel is None:
            return None
        binding = self._database_binding_drafts.get(int(logical_channel))
        return dict(binding) if binding is not None else None

    def _set_database_binding_for_channel(self, logical_channel: Optional[int], path: str) -> None:
        if logical_channel is None:
            return
        normalized_path = _display_text(path).strip()
        if not normalized_path:
            self._database_binding_drafts.pop(int(logical_channel), None)
            self._database_binding_statuses.pop(int(logical_channel), None)
            return
        self._database_binding_drafts[int(logical_channel)] = {
            "logical_channel": int(logical_channel),
            "path": normalized_path,
            "format": "dbc",
        }

    def _database_binding_channel_usage_count(self, logical_channel: Optional[int]) -> int:
        if logical_channel is None:
            return 0
        return sum(
            1
            for item in self._draft_bindings
            if _parse_optional_int_text(item.get("logical_channel")) == int(logical_channel)
        )

    def _prune_database_binding_for_channel_if_unused(self, logical_channel: Optional[int]) -> None:
        if logical_channel is None:
            return
        if self._database_binding_channel_usage_count(logical_channel) > 0:
            return
        self._database_binding_drafts.pop(int(logical_channel), None)
        self._database_binding_statuses.pop(int(logical_channel), None)

    def _refresh_database_binding_statuses(self) -> None:
        items = self._database_binding_items()
        if not items:
            self._database_binding_statuses = {}
            return
        statuses = self.app_logic.rebuild_override_preview(
            [DatabaseBinding(**item) for item in items]
        )
        self._database_binding_statuses = {int(key): dict(value) for key, value in statuses.items()}

    def _current_binding_logical_channel(self) -> Optional[int]:
        index = self.binding_list.currentRow()
        if index < 0 or index >= len(self._draft_bindings):
            return None
        return _parse_optional_int_text(self._draft_bindings[index].get("logical_channel"))

    def _refresh_orphan_database_bindings(self) -> None:
        orphan_items = _database_binding_orphan_items(self._database_binding_drafts, self._draft_bindings)
        if not orphan_items:
            self.orphan_database_label.clear()
            self.orphan_database_label.hide()
            self.orphan_database_list.clear()
            self.orphan_database_list.hide()
            self.remove_orphan_database_button.hide()
            return
        label_map = self._collection_label_map()
        self.orphan_database_label.setText(_build_orphan_database_binding_text(orphan_items, label_map))
        self.orphan_database_label.setProperty("tone", "warn")
        self._refresh_style(self.orphan_database_label)
        self.orphan_database_label.show()
        self.orphan_database_list.clear()
        for item in orphan_items:
            summary = _database_binding_summary(item, label_map)
            orphan_item = QListWidgetItem(summary)
            orphan_item.setData(USER_ROLE, int(item["logical_channel"]))
            self.orphan_database_list.addItem(orphan_item)
        self._sync_list_height(self.orphan_database_list, min_rows=1)
        self.orphan_database_list.show()
        self.remove_orphan_database_button.show()
        self._update_orphan_database_buttons()

    def _update_orphan_database_buttons(self) -> None:
        if not hasattr(self, "remove_orphan_database_button"):
            return
        self.remove_orphan_database_button.setEnabled(self.orphan_database_list.currentRow() >= 0)

    def _remove_selected_orphan_database_binding(self) -> None:
        index = self.orphan_database_list.currentRow()
        if index < 0:
            return
        item = self.orphan_database_list.item(index)
        logical_channel = _parse_optional_int_text(item.data(USER_ROLE))
        if logical_channel is None:
            return
        self._database_binding_drafts.pop(logical_channel, None)
        self._database_binding_statuses.pop(logical_channel, None)
        self._refresh_binding_list(select_index=self.binding_list.currentRow(), reload_editor=False)
        self._refresh_orphan_database_bindings()
        self._mark_dirty_and_schedule_validation(immediate=True)

    def _refresh_binding_database_editor(self, logical_channel: Optional[int]) -> None:
        if logical_channel is None:
            self.binding_database_channel_edit.clear()
            self.binding_database_path_edit.clear()
            self.binding_database_status_label.setText("状态：当前逻辑通道未绑定DBC。")
            self.binding_database_scope_label.setText("当前DBC作用于逻辑通道，共享同通道映射。")
            self.binding_database_clear_button.setEnabled(False)
            self.binding_database_browse_button.setEnabled(False)
            self.binding_database_path_edit.setEnabled(False)
            return

        binding = self._database_binding_for_channel(logical_channel)
        status = self._database_binding_statuses.get(int(logical_channel))
        usage_count = self._database_binding_channel_usage_count(logical_channel)
        self.binding_database_channel_edit.setText(str(logical_channel))
        self.binding_database_path_edit.setEnabled(True)
        self.binding_database_browse_button.setEnabled(True)
        self.binding_database_path_edit.setText(_display_text((binding or {}).get("path", "")))
        self.binding_database_status_label.setText(_database_binding_status_detail(binding, status))
        if usage_count > 1:
            self.binding_database_scope_label.setText(
                f"当前DBC作用于LC{logical_channel}，已有 {usage_count} 条文件映射共享该通道。"
            )
        else:
            self.binding_database_scope_label.setText(f"当前DBC作用于LC{logical_channel}。")
        self.binding_database_clear_button.setEnabled(bool(binding))

    def _apply_current_database_binding_path(self, *, refresh_status: bool) -> None:
        logical_channel = self._current_binding_logical_channel()
        self._set_database_binding_for_channel(logical_channel, self.binding_database_path_edit.text())
        if refresh_status:
            self._refresh_database_binding_statuses()
        self._refresh_binding_list(select_index=self.binding_list.currentRow(), reload_editor=False)
        self._refresh_orphan_database_bindings()
        self._refresh_binding_database_editor(logical_channel)

    def _handle_binding_database_path_text_changed(self, *_args) -> None:
        if self._suspend_updates:
            return
        self._apply_current_database_binding_path(refresh_status=False)
        self._mark_dirty_and_schedule_validation()

    def _handle_binding_database_path_editing_finished(self) -> None:
        if self._suspend_updates:
            return
        self._apply_current_database_binding_path(refresh_status=True)
        self._mark_dirty_and_schedule_validation(immediate=True)

    def _browse_binding_database_path(self) -> None:
        logical_channel = self._current_binding_logical_channel()
        if logical_channel is None:
            return
        initial_dir = _display_text(self.binding_database_path_edit.text()).strip()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择DBC文件",
            initial_dir,
            "DBC文件 (*.dbc);;所有文件 (*.*)",
        )
        if not path:
            return
        self.binding_database_path_edit.setText(path)
        self._apply_current_database_binding_path(refresh_status=True)
        self._mark_dirty_and_schedule_validation(immediate=True)

    def _clear_binding_database_path(self) -> None:
        logical_channel = self._current_binding_logical_channel()
        if logical_channel is None:
            return
        self.binding_database_path_edit.clear()
        self._database_binding_drafts.pop(logical_channel, None)
        self._database_binding_statuses.pop(logical_channel, None)
        self._refresh_binding_list(select_index=self.binding_list.currentRow(), reload_editor=False)
        self._refresh_orphan_database_bindings()
        self._refresh_binding_database_editor(logical_channel)
        self._mark_dirty_and_schedule_validation(immediate=True)

    def _next_binding_logical_channel(self) -> int:
        existing_channels = {
            _parse_optional_int_text(binding.get("logical_channel"))
            for binding in self._draft_bindings
        }
        candidate = 0
        while candidate in existing_channels:
            candidate += 1
        return candidate

    def _next_unmapped_trace_id(self) -> str:
        mapped_trace_ids = {
            _display_text(binding.get("trace_file_id", "")).strip()
            for binding in self._draft_bindings
            if _display_text(binding.get("trace_file_id", "")).strip()
        }
        for trace_id in self._checked_trace_ids():
            if trace_id not in mapped_trace_ids:
                return trace_id
        return ""

    def _current_trace_file_id(self) -> str:
        value = self.binding_trace_file_combo.currentData(USER_ROLE)
        if value is None:
            value = self.binding_trace_file_combo.currentText()
        return _display_text(value).strip()

    def _current_source_summary(self) -> Optional[dict]:
        value = self.binding_source_combo.currentData(USER_ROLE)
        if isinstance(value, dict):
            return dict(value)
        return None

    def _coerce_binding_draft(self, payload: dict) -> dict:
        draft = dict(payload)
        trace_file_id = _display_text(draft.get("trace_file_id", "")).strip()
        draft["trace_file_id"] = trace_file_id
        source_channel = _display_text(draft.get("source_channel", "")).strip()
        source_bus_type = _display_text(draft.get("source_bus_type", "")).strip().upper()
        if not trace_file_id:
            draft["source_channel"] = ""
            draft["source_bus_type"] = ""
            return draft
        summaries = self._binding_trace_source_summaries(trace_file_id)
        selected_summary = next(
            (
                summary
                for summary in summaries
                if str(summary.get("source_channel")) == source_channel
                and _display_text(summary.get("bus_type", "")).strip().upper() == source_bus_type
            ),
            None,
        )
        if selected_summary is None and summaries:
            selected_summary = summaries[0]
        if selected_summary is not None:
            draft["source_channel"] = str(selected_summary["source_channel"])
            draft["source_bus_type"] = _display_text(selected_summary["bus_type"]).strip().upper()
            draft["bus_type"] = draft["source_bus_type"]
        elif source_bus_type:
            draft["bus_type"] = source_bus_type
        return draft

    def _populate_binding_trace_file_options(self, selected_trace_id: str) -> None:
        trace_lookup = self._binding_trace_lookup()
        selected_ids = self._checked_trace_ids()
        if selected_trace_id and selected_trace_id not in selected_ids:
            selected_ids.append(selected_trace_id)
        self.binding_trace_file_combo.blockSignals(True)
        self.binding_trace_file_combo.clear()
        for trace_id in selected_ids:
            label = _trace_record_name(trace_id, trace_lookup)
            self.binding_trace_file_combo.addItem(label)
            self.binding_trace_file_combo.setItemData(self.binding_trace_file_combo.count() - 1, trace_id, USER_ROLE)
        if self.binding_trace_file_combo.count() == 0:
            self.binding_trace_file_combo.addItem("请先勾选场景文件")
            self.binding_trace_file_combo.setItemData(0, "", USER_ROLE)
        if selected_trace_id:
            index = self.binding_trace_file_combo.findData(selected_trace_id, USER_ROLE)
            if index >= 0:
                self.binding_trace_file_combo.setCurrentIndex(index)
        self.binding_trace_file_combo.blockSignals(False)

    def _populate_binding_source_options(
        self,
        trace_file_id: str,
        source_channel: str,
        source_bus_type: str,
    ) -> None:
        summaries = self._binding_trace_source_summaries(trace_file_id)
        self.binding_source_combo.blockSignals(True)
        self.binding_source_combo.clear()
        selected_index = -1
        for summary in summaries:
            self.binding_source_combo.addItem(_display_text(summary.get("label", "")))
            self.binding_source_combo.setItemData(self.binding_source_combo.count() - 1, summary, USER_ROLE)
            if str(summary.get("source_channel")) == source_channel and _display_text(summary.get("bus_type", "")).strip().upper() == source_bus_type:
                selected_index = self.binding_source_combo.count() - 1
        if selected_index < 0 and trace_file_id and source_channel and source_bus_type:
            legacy_summary = {
                "source_channel": source_channel,
                "bus_type": source_bus_type,
                "frame_count": 0,
                "label": f"CH{source_channel} | {source_bus_type} | 旧映射/缺失",
            }
            self.binding_source_combo.addItem(legacy_summary["label"])
            self.binding_source_combo.setItemData(self.binding_source_combo.count() - 1, legacy_summary, USER_ROLE)
            selected_index = self.binding_source_combo.count() - 1
        if self.binding_source_combo.count() == 0:
            self.binding_source_combo.addItem("当前文件未识别到可映射源项")
            self.binding_source_combo.setItemData(0, None, USER_ROLE)
        if selected_index >= 0:
            self.binding_source_combo.setCurrentIndex(selected_index)
        elif self.binding_source_combo.count() > 0:
            self.binding_source_combo.setCurrentIndex(0)
        self.binding_source_combo.blockSignals(False)

    def _populate_binding_driver_options(self, selected_driver: str) -> None:
        self.binding_driver_combo.blockSignals(True)
        self.binding_driver_combo.clear()
        self.binding_driver_combo.addItems(list(DRIVER_OPTIONS))
        normalized_driver = _normalize_driver_name(selected_driver)
        index = self.binding_driver_combo.findText(normalized_driver)
        if index >= 0:
            self.binding_driver_combo.setCurrentIndex(index)
        self.binding_driver_combo.blockSignals(False)

    def _populate_binding_device_type_options(self, driver: Any, current_value: str) -> None:
        normalized_driver = _normalize_driver_name(driver)
        self.binding_device_type_combo.blockSignals(True)
        self.binding_device_type_combo.clear()
        for value in _binding_device_type_options(normalized_driver):
            self.binding_device_type_combo.addItem(value)
        self.binding_device_type_combo.setEditText(_display_text(current_value).strip())
        line_edit = self.binding_device_type_combo.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText(_binding_device_type_placeholder(normalized_driver))
        self.binding_device_type_combo.setToolTip(_binding_device_type_placeholder(normalized_driver))
        self.binding_device_type_combo.blockSignals(False)

    def _connect_binding_widget(self, widget: QWidget) -> None:
        if widget is self.binding_trace_file_combo:
            self.binding_trace_file_combo.currentIndexChanged.connect(self._handle_binding_trace_file_changed)
            return
        if widget is self.binding_source_combo:
            self.binding_source_combo.currentIndexChanged.connect(self._handle_binding_source_changed)
            return
        if getattr(self, "binding_driver_combo", None) is widget:
            self.binding_driver_combo.currentTextChanged.connect(self._handle_binding_driver_changed)
            return
        if isinstance(widget, QLineEdit):
            widget.textChanged.connect(self._binding_input_changed)
            return
        if isinstance(widget, QComboBox):
            widget.currentTextChanged.connect(self._binding_input_changed)
            return
        if isinstance(widget, QCheckBox):
            widget.toggled.connect(self._binding_input_changed)
            return
        if isinstance(widget, QPlainTextEdit):
            widget.textChanged.connect(self._binding_text_changed)

    def _binding_text_changed(self) -> None:
        self._sync_text_edit_height(self.binding_network_editor, min_lines=4)
        self._sync_text_edit_height(self.binding_metadata_editor, min_lines=4)
        self._binding_input_changed()

    def _binding_input_changed(self, *_args) -> None:
        if self._suspend_updates:
            return
        index = self._sync_selected_binding_draft()
        if index is None:
            return
        self._refresh_binding_list(select_index=index)
        self._refresh_trace_choice_labels()
        self._refresh_orphan_database_bindings()
        self._refresh_all_collection_lists()
        self._mark_dirty_and_schedule_validation()

    def _sync_selected_binding_draft(self) -> Optional[int]:
        index = self.binding_list.currentRow()
        if index < 0 or index >= len(self._draft_bindings):
            return None
        self._draft_bindings[index] = self._coerce_binding_draft(self._selected_binding_payload_from_inputs())
        return index

    def _handle_binding_trace_file_changed(self, *_args) -> None:
        if self._suspend_updates:
            return
        index = self.binding_list.currentRow()
        if index < 0 or index >= len(self._draft_bindings):
            return
        payload = dict(self._draft_bindings[index])
        payload["trace_file_id"] = self._current_trace_file_id()
        payload["source_channel"] = ""
        payload["source_bus_type"] = ""
        self._draft_bindings[index] = self._coerce_binding_draft(payload)
        self._refresh_binding_list(select_index=index)
        self._refresh_trace_choice_labels()
        self._refresh_orphan_database_bindings()
        self._refresh_all_collection_lists()
        self._mark_dirty_and_schedule_validation()

    def _handle_binding_source_changed(self, *_args) -> None:
        if self._suspend_updates:
            return
        index = self.binding_list.currentRow()
        if index < 0 or index >= len(self._draft_bindings):
            return
        payload = dict(self._draft_bindings[index])
        source_summary = self._current_source_summary() or {}
        payload["trace_file_id"] = self._current_trace_file_id()
        payload["source_channel"] = _display_text(source_summary.get("source_channel", ""))
        payload["source_bus_type"] = _display_text(source_summary.get("bus_type", "")).strip().upper()
        self._draft_bindings[index] = self._coerce_binding_draft(payload)
        self._refresh_binding_list(select_index=index)
        self._refresh_trace_choice_labels()
        self._refresh_orphan_database_bindings()
        self._refresh_all_collection_lists()
        self._mark_dirty_and_schedule_validation()

    def _handle_binding_driver_changed(self, *_args) -> None:
        if self._suspend_updates:
            return
        index = self.binding_list.currentRow()
        if index < 0 or index >= len(self._draft_bindings):
            return
        payload = dict(self._draft_bindings[index])
        previous_driver = _normalize_driver_name(payload.get("driver", "zlg"))
        new_driver = _normalize_driver_name(self.binding_driver_combo.currentText())
        payload["driver"] = new_driver
        current_sdk_root = _display_text(self.binding_sdk_root_edit.text()).strip()
        if not current_sdk_root or current_sdk_root == _default_sdk_root_for_driver(previous_driver):
            payload["sdk_root"] = _default_sdk_root_for_driver(new_driver)
        current_device_type = _display_text(self.binding_device_type_combo.currentText()).strip()
        payload["device_type"] = current_device_type
        self._draft_bindings[index] = self._coerce_binding_draft(payload)
        self._suspend_updates = True
        self._populate_binding_device_type_options(new_driver, current_device_type)
        self.binding_sdk_root_edit.setText(_display_text(payload.get("sdk_root", "")))
        self._suspend_updates = False
        self._refresh_binding_list(select_index=index)
        self._refresh_trace_choice_labels()
        self._refresh_orphan_database_bindings()
        self._refresh_all_collection_lists()
        self._mark_dirty_and_schedule_validation()

    def _selected_binding_payload_from_inputs(self) -> dict:
        source_summary = self._current_source_summary() or {}
        return {
            "trace_file_id": self._current_trace_file_id(),
            "source_channel": _display_text(source_summary.get("source_channel", "")),
            "source_bus_type": _display_text(source_summary.get("bus_type", "")).strip().upper(),
            "adapter_id": self.binding_adapter_id_edit.text(),
            "driver": self.binding_driver_combo.currentText(),
            "logical_channel": self.binding_logical_channel_edit.text(),
            "physical_channel": self.binding_physical_channel_edit.text(),
            "bus_type": self.binding_bus_type_edit.text(),
            "device_type": self.binding_device_type_combo.currentText(),
            "device_index": self.binding_device_index_edit.text(),
            "sdk_root": self.binding_sdk_root_edit.text(),
            "nominal_baud": self.binding_nominal_baud_edit.text(),
            "data_baud": self.binding_data_baud_edit.text(),
            "resistance_enabled": self.binding_resistance_checkbox.isChecked(),
            "listen_only": self.binding_listen_only_checkbox.isChecked(),
            "tx_echo": self.binding_tx_echo_checkbox.isChecked(),
            "merge_receive": self.binding_merge_receive_checkbox.isChecked(),
            "network": self.binding_network_editor.toPlainText(),
            "metadata": self.binding_metadata_editor.toPlainText(),
        }

    def _handle_binding_selection_changed(self) -> None:
        if self._suspend_updates:
            return
        index = self.binding_list.currentRow()
        self.remove_binding_button.setEnabled(index >= 0)
        self._refresh_database_binding_statuses()
        self._load_selected_binding_into_editor(index)
        self._apply_validation_visuals()

    def _load_selected_binding_into_editor(self, index: int) -> None:
        self._suspend_updates = True
        enabled = 0 <= index < len(self._draft_bindings)
        self._set_binding_editor_enabled(enabled)
        if not enabled:
            self.binding_editor_hint.show()
            for widget in self._binding_field_widgets.values():
                if isinstance(widget, QPlainTextEdit):
                    widget.setPlainText("")
                elif isinstance(widget, QComboBox):
                    widget.clear()
                elif isinstance(widget, QCheckBox):
                    widget.setChecked(False)
                else:
                    widget.clear()
            self._refresh_binding_database_editor(None)
            self._suspend_updates = False
            return

        self.binding_editor_hint.hide()
        payload = self._coerce_binding_draft(self._draft_bindings[index])
        self._draft_bindings[index] = payload
        self._populate_binding_trace_file_options(_display_text(payload.get("trace_file_id", "")).strip())
        self._populate_binding_source_options(
            _display_text(payload.get("trace_file_id", "")).strip(),
            _display_text(payload.get("source_channel", "")),
            _display_text(payload.get("source_bus_type", "")).strip().upper(),
        )
        self.binding_adapter_id_edit.setText(_display_text(payload.get("adapter_id", "")))
        normalized_driver = _normalize_driver_name(payload.get("driver", "zlg"))
        self._populate_binding_driver_options(normalized_driver)
        self.binding_logical_channel_edit.setText(_display_text(payload.get("logical_channel", "")))
        self.binding_bus_type_edit.setText(_display_text(payload.get("bus_type", "CANFD")).upper() or "CANFD")
        self._populate_binding_device_type_options(normalized_driver, _display_text(payload.get("device_type", "")))
        self.binding_device_index_edit.setText(_display_text(payload.get("device_index", "")))
        self.binding_sdk_root_edit.setText(_display_text(payload.get("sdk_root", "")))
        self.binding_nominal_baud_edit.setText(_display_text(payload.get("nominal_baud", "")))
        self.binding_data_baud_edit.setText(_display_text(payload.get("data_baud", "")))
        self.binding_physical_channel_edit.setText(_display_text(payload.get("physical_channel", "")))
        self.binding_resistance_checkbox.setChecked(bool(payload.get("resistance_enabled", False)))
        self.binding_listen_only_checkbox.setChecked(bool(payload.get("listen_only", False)))
        self.binding_tx_echo_checkbox.setChecked(bool(payload.get("tx_echo", False)))
        self.binding_merge_receive_checkbox.setChecked(bool(payload.get("merge_receive", False)))
        self.binding_network_editor.setPlainText(_display_text(payload.get("network", "{}")))
        self.binding_metadata_editor.setPlainText(_display_text(payload.get("metadata", "{}")))
        self._sync_text_edit_height(self.binding_network_editor, min_lines=4)
        self._sync_text_edit_height(self.binding_metadata_editor, min_lines=4)
        self._refresh_binding_database_editor(_parse_optional_int_text(payload.get("logical_channel")))
        self._suspend_updates = False

    def _set_binding_editor_enabled(self, enabled: bool) -> None:
        for widget in self._binding_field_widgets.values():
            widget.setEnabled(enabled)
        self.binding_bus_type_edit.setEnabled(False)
        self.binding_database_channel_edit.setEnabled(False)
        self.binding_database_format_edit.setEnabled(False)
        if not enabled:
            self.binding_database_path_edit.setEnabled(False)
            self.binding_database_browse_button.setEnabled(False)
            self.binding_database_clear_button.setEnabled(False)

    def _add_binding(self) -> None:
        trace_id = self._next_unmapped_trace_id()
        if not trace_id:
            QMessageBox.information(self, "新增文件映射", "当前没有可新增的场景文件映射，请先勾选文件或清理已有映射。")
            return
        draft = _new_binding_draft(self._next_binding_logical_channel())
        draft["trace_file_id"] = trace_id
        draft = self._coerce_binding_draft(draft)
        self._draft_bindings.append(draft)
        self._refresh_database_binding_statuses()
        self._refresh_binding_list(select_index=len(self._draft_bindings) - 1)
        self._refresh_trace_choice_labels()
        self._refresh_orphan_database_bindings()
        self._refresh_all_collection_lists()
        self._mark_dirty_and_schedule_validation(immediate=True)

    def _remove_selected_binding(self) -> None:
        index = self.binding_list.currentRow()
        if index < 0:
            return
        logical_channel = _parse_optional_int_text(self._draft_bindings[index].get("logical_channel"))
        del self._draft_bindings[index]
        self._prune_database_binding_for_channel_if_unused(logical_channel)
        self._refresh_database_binding_statuses()
        next_index = min(index, len(self._draft_bindings) - 1)
        self._refresh_binding_list(select_index=next_index if next_index >= 0 else None)
        self._refresh_trace_choice_labels()
        self._refresh_orphan_database_bindings()
        self._refresh_all_collection_lists()
        self._mark_dirty_and_schedule_validation(immediate=True)

    def _refresh_binding_list(
        self,
        *,
        select_index: Optional[int] = None,
        reload_editor: bool = True,
    ) -> None:
        previous_index = self.binding_list.currentRow()
        target_index = previous_index if select_index is None else select_index
        self._suspend_updates = True
        self.binding_list.clear()
        trace_lookup = self._binding_trace_lookup()
        for index, payload in enumerate(self._draft_bindings):
            logical_channel = _parse_optional_int_text(payload.get("logical_channel"))
            summary = _resource_mapping_summary(
                payload,
                trace_lookup,
                database_binding=self._database_binding_for_channel(logical_channel),
                database_status=self._database_binding_statuses.get(int(logical_channel)) if logical_channel is not None else None,
            )
            error_count = self._binding_error_counts.get(index, 0)
            if error_count:
                summary = f"{summary} • {error_count} 个错误"
            item = QListWidgetItem(summary)
            if error_count:
                item.setForeground(QColor("#b42318"))
                item.setToolTip("\n".join(self._binding_list_error_messages.get(index, [])))
            self.binding_list.addItem(item)
        self._sync_list_height(self.binding_list, min_rows=2)
        if 0 <= target_index < self.binding_list.count():
            self.binding_list.setCurrentRow(target_index)
        self._suspend_updates = False
        self.remove_binding_button.setEnabled(self.binding_list.currentRow() >= 0)
        self.add_binding_button.setEnabled(bool(self._next_unmapped_trace_id()))
        if reload_editor:
            self._load_selected_binding_into_editor(self.binding_list.currentRow())

    def _collection_label_map(self) -> dict[int, str]:
        return _binding_label_map(self._draft_bindings, self._binding_trace_lookup())

    def _logical_channel_options(self, *, allow_empty: bool = False) -> tuple[Any, ...]:
        options: list[Any] = []
        if allow_empty:
            options.append(("留空（作用于整个适配器）", ""))
        label_map = self._collection_label_map()
        seen_channels: set[int] = set()
        sorted_bindings = sorted(
            self._draft_bindings,
            key=lambda item: (
                _parse_optional_int_text(item.get("logical_channel"))
                if _parse_optional_int_text(item.get("logical_channel")) is not None
                else -1,
                _display_text(item.get("adapter_id", "")),
            ),
        )
        for binding in sorted_bindings:
            logical_channel = _parse_optional_int_text(binding.get("logical_channel"))
            if logical_channel is None or logical_channel in seen_channels:
                continue
            seen_channels.add(logical_channel)
            options.append((_logical_channel_label(logical_channel, label_map), logical_channel))
        return tuple(options)
