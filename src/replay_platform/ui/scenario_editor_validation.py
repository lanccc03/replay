from __future__ import annotations

import uuid
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
from replay_platform.ui.collection_dialog import CollectionItemDialog


class ScenarioEditorValidationMixin:

    def load_payload(self, payload: dict, *, prompt_on_unsaved: bool = False) -> bool:
        normalized = _normalize_scenario_payload(payload)
        current_id = self.current_scenario_id()
        incoming_id = _display_text(normalized.get("scenario_id", "")).strip()
        if prompt_on_unsaved and self.isVisible() and current_id and incoming_id == current_id:
            self.show()
            self.raise_()
            self.activateWindow()
            return True
        if prompt_on_unsaved and not self._confirm_close_with_unsaved_changes():
            return False

        self._refresh_trace_library_cache()
        self._suspend_updates = True
        self._validation_timer.stop()
        self._draft_bindings = [_binding_draft_from_item(item) for item in normalized.get("bindings", [])]
        self._database_binding_drafts, self._database_binding_duplicate_counts = _database_binding_map_from_items(
            _clone_jsonable(normalized.get("database_bindings", []))
        )
        self._collection_data["database_bindings"] = []
        self._collection_data["signal_overrides"] = _clone_jsonable(normalized.get("signal_overrides", []))
        self._collection_data["diagnostic_targets"] = _clone_jsonable(normalized.get("diagnostic_targets", []))
        self._collection_data["diagnostic_actions"] = _clone_jsonable(normalized.get("diagnostic_actions", []))
        self._collection_data["link_actions"] = _clone_jsonable(normalized.get("link_actions", []))

        self.scenario_id_edit.setText(_display_text(normalized.get("scenario_id", "")))
        self.scenario_name_edit.setText(_display_text(normalized.get("name", "新场景")))
        self.metadata_editor.setPlainText(_format_json_text(normalized.get("metadata", {})))
        self._sync_text_edit_height(self.metadata_editor, min_lines=4)
        self._populate_trace_choices(set(normalized.get("trace_file_ids", [])))
        self._refresh_database_binding_statuses()
        self._refresh_all_collection_lists()
        self._refresh_binding_list(select_index=0 if self._draft_bindings else None)
        self._refresh_orphan_database_bindings()
        self._last_saved_payload = _clone_jsonable(normalized)
        self._last_valid_payload = _clone_jsonable(normalized)
        self._validation_errors = []
        self._validation_warnings = []
        self._feedback_message = "已加载场景。"
        self._feedback_tone = "muted"
        self._is_dirty = False
        self._raw_dirty = False
        self._suspend_updates = False
        result = self._validate_current_draft()
        self._validation_errors = result.errors
        self._validation_warnings = result.warnings
        if result.warnings:
            self._feedback_message = f"已加载场景；仍有 {len(result.warnings)} 个提示需要关注。"
            self._feedback_tone = "warn"
        self._on_payload_changed(_clone_jsonable(normalized))
        self._refresh_json_preview()
        self._apply_validation_visuals()
        return True

    def export_scenario(self, use_selected_trace_fallback: bool = False) -> ScenarioSpec:
        result = self._validate_current_draft()
        self._validation_errors = result.errors
        self._validation_warnings = result.warnings
        self._apply_validation_visuals()
        if result.errors:
            self._focus_issue(result.errors[0])
            raise ValueError(result.errors[0].message)
        payload = _clone_jsonable(result.normalized_payload or {})
        if use_selected_trace_fallback and not payload.get("trace_file_ids"):
            payload["trace_file_ids"] = self._trace_selection_supplier()
        return ScenarioSpec.from_dict(payload)

    def _handle_tab_changed(self, index: int) -> None:
        if index == 1:
            self._refresh_json_preview()

    def _handle_user_edit(self, *_args) -> None:
        if self._suspend_updates:
            return
        if self.sender() is self.scenario_trace_list:
            self._handle_trace_selection_changed()
        self._mark_dirty_and_schedule_validation()

    def _handle_metadata_changed(self) -> None:
        self._sync_text_edit_height(self.metadata_editor, min_lines=4)
        self._handle_user_edit()

    def _handle_trace_selection_changed(self) -> None:
        index = self.binding_list.currentRow()
        if 0 <= index < len(self._draft_bindings):
            self._draft_bindings[index] = self._coerce_binding_draft(self._draft_bindings[index])
        self._refresh_binding_list(select_index=index if index >= 0 else None)
        self._refresh_all_collection_lists()

    def _collection_fields(self, key: str) -> list[EditorFieldSpec]:
        fields = list(self._collection_sections[key]["fields"])
        if key not in {"database_bindings", "signal_overrides", "diagnostic_targets", "link_actions"}:
            return fields
        options = self._logical_channel_options(allow_empty=key == "link_actions")
        resolved_fields: list[EditorFieldSpec] = []
        for field in fields:
            if field.key == "logical_channel":
                resolved_fields.append(EditorFieldSpec(field.key, field.label, "combo", options))
            else:
                resolved_fields.append(field)
        return resolved_fields

    def _update_collection_buttons(self, key: str) -> None:
        section = self._collection_sections[key]
        has_selection = section["list"].currentRow() >= 0
        section["edit_button"].setEnabled(has_selection)
        section["remove_button"].setEnabled(has_selection)

    def _refresh_collection_list(self, key: str) -> None:
        section = self._collection_sections[key]
        list_widget = section["list"]
        list_widget.clear()
        label_map = self._collection_label_map()
        for item in self._collection_data[key]:
            if key == "diagnostic_actions":
                summary = section["summary"](item)
            else:
                summary = section["summary"](item, label_map)
            list_widget.addItem(QListWidgetItem(summary))
        self._sync_list_height(list_widget, min_rows=1)
        self._update_collection_buttons(key)

    def _refresh_all_collection_lists(self) -> None:
        for key in self._collection_sections:
            self._refresh_collection_list(key)

    def replace_signal_overrides(self, overrides: Sequence[dict]) -> None:
        self._collection_data["signal_overrides"] = [_clone_jsonable(item) for item in overrides]
        self._refresh_collection_list("signal_overrides")
        self._mark_dirty_and_schedule_validation(immediate=True)

    def _edit_selected_collection_item(self, key: str) -> None:
        index = self._collection_sections[key]["list"].currentRow()
        if index < 0:
            return
        self._edit_collection_item(key, index)

    def _edit_collection_item(self, key: str, index: Optional[int]) -> None:
        section = self._collection_sections[key]
        initial_value = (
            _clone_jsonable(self._collection_data[key][index])
            if index is not None
            else _clone_jsonable(section["default_item"]())
        )
        if index is None and key in {"database_bindings", "signal_overrides", "diagnostic_targets"}:
            options = self._logical_channel_options()
            if options:
                initial_value["logical_channel"] = options[0][1]
        dialog = CollectionItemDialog(
            section["title"],
            self._collection_fields(key),
            section["normalize"],
            initial_value=initial_value,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        if index is None:
            self._collection_data[key].append(dialog.value())
            selected_index = len(self._collection_data[key]) - 1
        else:
            self._collection_data[key][index] = dialog.value()
            selected_index = index
        self._refresh_collection_list(key)
        self._collection_sections[key]["list"].setCurrentRow(selected_index)
        self._mark_dirty_and_schedule_validation(immediate=True)

    def _remove_selected_collection_item(self, key: str) -> None:
        section = self._collection_sections[key]
        index = section["list"].currentRow()
        if index < 0:
            return
        del self._collection_data[key][index]
        self._refresh_collection_list(key)
        next_index = min(index, len(self._collection_data[key]) - 1)
        if next_index >= 0:
            section["list"].setCurrentRow(next_index)
        self._mark_dirty_and_schedule_validation(immediate=True)

    def _mark_dirty_and_schedule_validation(self, *, immediate: bool = False) -> None:
        self._raw_dirty = True
        self._feedback_message = ""
        self._feedback_tone = "muted"
        if immediate:
            self._validation_timer.stop()
            self._run_live_validation()
            return
        self._validation_timer.start()
        self._apply_validation_visuals()

    def _validate_current_draft(self) -> DraftValidationResult:
        issues: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        normalized_bindings: list[tuple[int, dict]] = []
        normalized_database_bindings: list[dict] = []
        for index, item in enumerate(self._draft_bindings):
            normalized_item, item_issues = _validate_binding_draft(item, index)
            if item_issues:
                issues.extend(item_issues)
                continue
            normalized_bindings.append((index, normalized_item or {}))

        normalized_collections: dict[str, list[dict]] = {
            "database_bindings": [],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
        }
        collection_normalizers = {
            "database_bindings": _normalize_database_binding_item,
            "signal_overrides": _normalize_signal_override_item,
            "diagnostic_targets": _normalize_diagnostic_target_item,
            "diagnostic_actions": _normalize_diagnostic_action_item,
            "link_actions": _normalize_link_action_item,
        }
        for key, items in self._collection_data.items():
            for index, item in enumerate(items):
                try:
                    normalized_collections[key].append(collection_normalizers[key](item, path_prefix=f"{key}[{index}]"))
                except FieldValidationError as exc:
                    issues.append(ValidationIssue(key, exc.path, str(exc)))

        for index, item in enumerate(self._database_binding_items()):
            try:
                normalized_database_bindings.append(
                    _normalize_database_binding_item(item, path_prefix=f"database_bindings[{index}]")
                )
            except FieldValidationError as exc:
                issues.append(ValidationIssue("bindings", exc.path, str(exc)))

        try:
            metadata = _parse_json_object_text(self.metadata_editor.toPlainText(), "场景元数据")
        except ValueError as exc:
            issues.append(ValidationIssue("metadata", "metadata", str(exc)))
            metadata = {}

        trace_ids = self._checked_trace_ids()
        existing_trace_ids = set(self._binding_trace_lookup())
        missing_trace_ids = [trace_id for trace_id in trace_ids if trace_id not in existing_trace_ids]
        if missing_trace_ids:
            warnings.append(
                ValidationIssue(
                    "traces",
                    "trace_file_ids",
                    f"当前场景仍引用 {len(missing_trace_ids)} 个缺失文件。",
                )
            )

        file_mapped_trace_ids: set[str] = set()
        used_physical_channels: dict[tuple[str, int], int] = {}
        has_file_mapping = False
        for binding_index, binding in normalized_bindings:
            if not _binding_uses_trace_source(binding):
                continue
            has_file_mapping = True
            trace_file_id = _display_text(binding.get("trace_file_id", "")).strip()
            if trace_file_id in file_mapped_trace_ids:
                issues.append(
                    ValidationIssue(
                        "bindings",
                        f"bindings[{binding_index}].trace_file_id",
                        "同一个场景文件只能保留一条文件映射。",
                    )
                )
            else:
                file_mapped_trace_ids.add(trace_file_id)
            if trace_file_id and trace_file_id not in trace_ids:
                issues.append(
                    ValidationIssue(
                        "bindings",
                        f"bindings[{binding_index}].trace_file_id",
                        "文件映射必须引用当前已勾选的场景文件。",
                    )
                )
            if trace_file_id in existing_trace_ids:
                summaries = self._binding_trace_source_summaries(trace_file_id)
                source_channel = binding.get("source_channel")
                source_bus_type = _display_text(binding.get("source_bus_type", "")).strip().upper()
                matched_summary = any(
                    summary.get("source_channel") == source_channel
                    and _display_text(summary.get("bus_type", "")).strip().upper() == source_bus_type
                    for summary in summaries
                )
                if summaries and not matched_summary:
                    issues.append(
                        ValidationIssue(
                            "bindings",
                            f"bindings[{binding_index}].source_selector",
                            "所选源项不属于当前文件，请重新选择。",
                        )
                    )
            channel_key = (str(binding.get("adapter_id", "")), int(binding.get("physical_channel", 0)))
            existing_binding_index = used_physical_channels.get(channel_key)
            if existing_binding_index is not None:
                issues.append(
                    ValidationIssue(
                        "bindings",
                        f"bindings[{binding_index}].physical_channel",
                        "同一个物理通道同一时刻只能映射一个文件。",
                    )
                )
            else:
                used_physical_channels[channel_key] = binding_index

        for binding_index, binding in normalized_bindings:
            warning = _binding_device_type_warning(binding, binding_index)
            if warning is not None:
                warnings.append(warning)

        for logical_channel, duplicate_count in sorted(self._database_binding_duplicate_counts.items()):
            warnings.append(
                ValidationIssue(
                    "bindings",
                    f"database_bindings[{logical_channel}]",
                    f"LC{logical_channel} 存在 {duplicate_count} 条DBC绑定，编辑器已按最后一条展示，保存时会去重。",
                )
            )

        orphan_database_bindings = _database_binding_orphan_items(
            self._database_binding_drafts,
            [binding for _, binding in normalized_bindings],
        )
        if orphan_database_bindings:
            warnings.append(
                ValidationIssue(
                    "bindings",
                    "database_bindings",
                    f"当前存在 {len(orphan_database_bindings)} 条孤立DBC绑定，默认保留，可在资源映射区单独移除。",
                )
            )

        if has_file_mapping:
            missing_mapped_trace_ids = sorted(set(trace_ids) - file_mapped_trace_ids)
            if missing_mapped_trace_ids:
                issues.append(
                    ValidationIssue("traces", "trace_file_ids", "已勾选的场景文件必须全部完成文件映射。")
                )

        if issues:
            return DraftValidationResult(None, issues, warnings)

        bindings = [binding for _, binding in normalized_bindings]
        payload = {
            "scenario_id": self.scenario_id_edit.text().strip() or uuid.uuid4().hex,
            "name": self.scenario_name_edit.text().strip() or "新场景",
            "trace_file_ids": trace_ids,
            "bindings": bindings,
            "database_bindings": normalized_database_bindings,
            "signal_overrides": normalized_collections["signal_overrides"],
            "diagnostic_targets": normalized_collections["diagnostic_targets"],
            "diagnostic_actions": normalized_collections["diagnostic_actions"],
            "link_actions": normalized_collections["link_actions"],
            "metadata": metadata,
        }
        try:
            normalized_payload = _normalize_scenario_payload(payload)
        except Exception as exc:
            issues.append(ValidationIssue("basic", "scenario", str(exc)))
            return DraftValidationResult(None, issues, warnings)
        return DraftValidationResult(normalized_payload, issues, warnings)

    def _run_live_validation(
        self,
        *,
        focus_first_error: bool = False,
        success_message: str = "",
        failure_message: str = "",
    ) -> DraftValidationResult:
        result = self._validate_current_draft()
        self._validation_errors = result.errors
        self._validation_warnings = result.warnings
        if result.normalized_payload is not None:
            self._last_valid_payload = _clone_jsonable(result.normalized_payload)
            self._is_dirty = _scenario_payload_is_dirty(result.normalized_payload, self._last_saved_payload)
            self._raw_dirty = self._is_dirty
            self._on_payload_changed(_clone_jsonable(result.normalized_payload))
            if success_message:
                self._feedback_message = success_message
                self._feedback_tone = "good"
        else:
            self._is_dirty = self._raw_dirty
            if failure_message:
                self._feedback_message = failure_message
                self._feedback_tone = "error"
        self._refresh_json_preview()
        self._apply_validation_visuals()
        if result.errors and focus_first_error:
            self._focus_issue(result.errors[0])
        return result

    def _validate_scenario(self) -> None:
        self._refresh_database_binding_statuses()
        self._refresh_binding_list(select_index=self.binding_list.currentRow(), reload_editor=False)
        self._refresh_orphan_database_bindings()
        result = self._run_live_validation(
            focus_first_error=True,
            failure_message="校验失败，已定位到第一个错误。",
        )
        if result.errors:
            return
        if result.warnings:
            self._feedback_message = f"校验通过，但仍有 {len(result.warnings)} 个提示需要关注。"
            self._feedback_tone = "warn"
        else:
            self._feedback_message = "校验通过，可保存。"
            self._feedback_tone = "good"
        self._apply_validation_visuals()

    def _save_scenario(self) -> None:
        self._refresh_database_binding_statuses()
        self._refresh_binding_list(select_index=self.binding_list.currentRow(), reload_editor=False)
        self._refresh_orphan_database_bindings()
        result = self._run_live_validation(
            focus_first_error=True,
            failure_message="保存前校验失败，请先修正错误。",
        )
        if result.errors or result.normalized_payload is None:
            return
        self._save_normalized_payload(result.normalized_payload)

    def _save_normalized_payload(self, normalized_payload: dict) -> bool:
        try:
            scenario = ScenarioSpec.from_dict(normalized_payload)
            self.app_logic.save_scenario(scenario)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return False

        saved_payload = scenario.to_dict()
        self._last_saved_payload = _clone_jsonable(saved_payload)
        self._last_valid_payload = _clone_jsonable(saved_payload)
        self._validation_errors = []
        self._feedback_message = "场景已保存。"
        self._feedback_tone = "good"
        self._is_dirty = False
        self._raw_dirty = False
        self._on_saved(_clone_jsonable(saved_payload))
        self.load_payload(saved_payload)
        if self._validation_warnings:
            self._feedback_message = f"场景已保存；仍有 {len(self._validation_warnings)} 个提示需要关注。"
            self._feedback_tone = "warn"
        else:
            self._feedback_message = "场景已保存。"
            self._feedback_tone = "good"
        self._apply_validation_visuals()
        return True

    def _confirm_close_with_unsaved_changes(self) -> bool:
        if not (self._is_dirty or self._raw_dirty):
            return True
        box = QMessageBox(self)
        box.setWindowTitle("未保存修改")
        box.setText("当前场景存在未保存修改。")
        box.setInformativeText("要先保存再继续吗？")
        box.setStandardButtons(QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Save)
        decision = box.exec()
        if decision == QMessageBox.Cancel:
            return False
        if decision == QMessageBox.Discard:
            return True
        result = self._run_live_validation(
            focus_first_error=True,
            failure_message="保存前校验失败，请先修正错误。",
        )
        if result.errors or result.normalized_payload is None:
            return False
        return self._save_normalized_payload(result.normalized_payload)

    def _apply_validation_visuals(self) -> None:
        self._clear_field_errors()
        self._binding_error_counts = {}
        self._binding_list_error_messages = {}
        section_error_counts = {key: 0 for key in self._section_boxes}
        for issue in self._validation_errors:
            section_key = "resources" if issue.section in {"traces", "bindings"} else issue.section
            section_error_counts[section_key] = section_error_counts.get(section_key, 0) + 1
            if issue.path in self._field_widgets:
                self._set_field_error(issue.path, issue.message)
                continue
            if issue.section == "bindings" and issue.path.startswith("bindings["):
                prefix, _, suffix = issue.path.partition("].")
                try:
                    index = int(prefix[len("bindings[") :])
                except ValueError:
                    continue
                self._binding_error_counts[index] = self._binding_error_counts.get(index, 0) + 1
                self._binding_list_error_messages.setdefault(index, []).append(issue.message)
                if index == self.binding_list.currentRow() and suffix in self._binding_field_widgets:
                    self._set_binding_field_error(suffix, issue.message)

        warning_counts = {key: 0 for key in self._section_boxes}
        trace_warning_messages = [warning.message for warning in self._validation_warnings if warning.section == "traces"]
        binding_warning_messages = [warning.message for warning in self._validation_warnings if warning.section == "bindings"]
        for warning in self._validation_warnings:
            section_key = "resources" if warning.section in {"traces", "bindings"} else warning.section
            warning_counts[section_key] = warning_counts.get(section_key, 0) + 1
        if trace_warning_messages:
            self.trace_warning_label.setText("\n".join(trace_warning_messages))
            self.trace_warning_label.setProperty("tone", "warn")
            self.trace_warning_label.show()
            self._refresh_style(self.trace_warning_label)
        else:
            self.trace_warning_label.clear()
            self.trace_warning_label.hide()
        if binding_warning_messages:
            self.binding_warning_label.setText("\n".join(binding_warning_messages))
            self.binding_warning_label.setProperty("tone", "warn")
            self.binding_warning_label.show()
            self._refresh_style(self.binding_warning_label)
        else:
            self.binding_warning_label.clear()
            self.binding_warning_label.hide()

        self._refresh_binding_list(
            select_index=self.binding_list.currentRow(),
            reload_editor=False,
        )
        self._refresh_trace_choice_labels()
        self._refresh_orphan_database_bindings()
        self._update_section_titles(section_error_counts, warning_counts)
        self._update_status_labels()

    def _clear_field_errors(self) -> None:
        for path in self._field_error_labels:
            self._field_error_labels[path].clear()
            self._set_widget_error(self._field_widgets[path], False)
        for key in self._binding_field_error_labels:
            self._binding_field_error_labels[key].clear()
            self._set_widget_error(self._binding_field_widgets[key], False)

    def _set_field_error(self, path: str, message: str) -> None:
        self._field_error_labels[path].setText(message)
        self._set_widget_error(self._field_widgets[path], True)

    def _set_binding_field_error(self, key: str, message: str) -> None:
        self._binding_field_error_labels[key].setText(message)
        self._set_widget_error(self._binding_field_widgets[key], True)

    def _set_widget_error(self, widget: QWidget, has_error: bool) -> None:
        widget.setProperty("errorState", has_error)
        self._refresh_style(widget)

    def _update_section_titles(self, error_counts: dict[str, int], warning_counts: dict[str, int]) -> None:
        for key, box in self._section_boxes.items():
            title = self._section_titles[key]
            error_count = error_counts.get(key, 0)
            warning_count = warning_counts.get(key, 0)
            suffixes = []
            if error_count:
                suffixes.append(f"{error_count} 个错误")
            if warning_count:
                suffixes.append(f"{warning_count} 个警告")
            if suffixes:
                box.setTitle(f"{title} • {' / '.join(suffixes)}")
            else:
                box.setTitle(title)

    def _update_status_labels(self) -> None:
        error_count = len(self._validation_errors)
        warning_count = len(self._validation_warnings)
        if error_count:
            text = f"未保存 • {error_count} 个错误"
            if warning_count:
                text += f" • {warning_count} 个警告"
            tone = "error"
        elif self._is_dirty or self._raw_dirty:
            text = "未保存"
            if warning_count:
                text += f" • {warning_count} 个警告"
            tone = "warn"
        else:
            text = "已保存"
            if warning_count:
                text += f" • {warning_count} 个警告"
                tone = "warn"
            else:
                tone = "good"
        self.status_badge_label.setText(text)
        self.status_badge_label.setProperty("tone", tone)
        self._refresh_style(self.status_badge_label)

        if self._feedback_message:
            detail = self._feedback_message
            detail_tone = self._feedback_tone
        elif error_count:
            detail = self._validation_errors[0].message
            detail_tone = "error"
        elif warning_count:
            detail = self._validation_warnings[0].message
            detail_tone = "warn"
        elif self._is_dirty or self._raw_dirty:
            detail = "当前草稿已变更，最近一次有效草稿已同步到主窗口摘要。"
            detail_tone = "muted"
        else:
            detail = "当前草稿与已保存版本一致。"
            detail_tone = "muted"
        self.status_detail_label.setText(detail)
        self.status_detail_label.setProperty("tone", detail_tone)
        self._refresh_style(self.status_detail_label)

    def _focus_issue(self, issue: ValidationIssue) -> None:
        self.editor_tabs.setCurrentIndex(0)
        if issue.path == "trace_file_ids":
            self.scenario_trace_list.setFocus()
            self.form_scroll.ensureWidgetVisible(self.scenario_trace_list, 24, 24)
            return
        if issue.path in self._field_widgets:
            widget = self._field_widgets[issue.path]
            widget.setFocus()
            self.form_scroll.ensureWidgetVisible(widget, 24, 24)
            return
        if issue.section == "bindings" and issue.path.startswith("bindings["):
            prefix, _, suffix = issue.path.partition("].")
            try:
                index = int(prefix[len("bindings[") :])
            except ValueError:
                return
            self.binding_list.setCurrentRow(index)
            widget = self._binding_field_widgets.get(suffix)
            if widget is not None:
                widget.setFocus()
                self.form_scroll.ensureWidgetVisible(widget, 24, 24)
            else:
                self.form_scroll.ensureWidgetVisible(self.binding_list, 24, 24)
            return
        if issue.section in self._collection_sections and issue.path.startswith(f"{issue.section}["):
            prefix = issue.path.split("]", 1)[0]
            try:
                index = int(prefix[len(issue.section) + 1 :])
            except ValueError:
                return
            list_widget = self._collection_sections[issue.section]["list"]
            list_widget.setCurrentRow(index)
            list_widget.setFocus()
            self.form_scroll.ensureWidgetVisible(list_widget, 24, 24)

    def _refresh_json_preview(self) -> None:
        note, text = _build_json_preview(self._last_valid_payload, len(self._validation_errors))
        self.json_preview_note.setText(note)
        self.json_preview_note.setProperty("tone", "warn" if self._validation_errors else "muted")
        self._refresh_style(self.json_preview_note)
        if self.scenario_editor.toPlainText() != text:
            self.scenario_editor.setPlainText(text)
