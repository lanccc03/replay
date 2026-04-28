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
from replay_platform.ui.qt_workers import BackgroundTask


class MainWindowActionsMixin:

    def _start_background_task(
        self,
        task: Callable[[], Any],
        *,
        on_success: Callable[[Any], None],
        on_failure: Callable[[str], None],
        on_cleanup: Callable[[], None],
    ) -> tuple[QThread, BackgroundTask]:
        thread = QThread(self)
        worker = BackgroundTask(task)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(on_success)
        worker.failed.connect(on_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(on_cleanup)
        thread.start()
        return thread, worker

    def _handle_trace_import_succeeded(self, result: Any) -> None:
        self._refresh_traces()
        if isinstance(result, TraceFileRecord):
            self._select_trace(result.trace_id)
            self._set_trace_operation_message(f"导入完成：{result.name}", tone="good")
            return
        self._set_trace_operation_message("导入完成。", tone="good")

    def _handle_trace_import_failed(self, message: str) -> None:
        self._set_trace_operation_message("导入失败，请检查文件后重试。", tone="error")
        QMessageBox.critical(self, "导入失败", message)

    def _begin_trace_import(self) -> None:
        if self._trace_import_in_progress or self._replay_prepare_in_progress:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入回放文件",
            str(Path.cwd()),
            "Trace 文件 (*.asc *.blf)",
        )
        if not path:
            return
        self._set_trace_import_busy(True, path=path)
        self._trace_import_thread, self._trace_import_worker = self._start_background_task(
            lambda: self.app_logic.import_trace(path),
            on_success=self._handle_trace_import_succeeded,
            on_failure=self._handle_trace_import_failed,
            on_cleanup=self._clear_trace_import_task,
        )

    def _import_trace(self) -> None:
        self._begin_trace_import()

    def _delete_selected_trace(self) -> None:
        record = self._selected_trace_record()
        if record is None:
            return
        referencing_scenarios = self.app_logic.find_scenarios_referencing_trace(record.trace_id)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("删除回放文件")
        box.setText("确认删除当前选中的回放文件吗？")
        box.setInformativeText(_build_trace_delete_summary(record, referencing_scenarios))
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() != QMessageBox.Yes:
            return
        try:
            self.app_logic.delete_trace(record.trace_id)
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        self._refresh_traces()

    def _new_scenario(self) -> None:
        payload = self._default_scenario_payload()
        self._set_current_scenario_payload(payload)
        self._open_scenario_editor(payload)

    def _load_selected_scenario(self) -> None:
        scenario = self._selected_scenario_record()
        self.scenario_selection_summary.setText(_build_scenario_selection_summary(scenario))
        self._update_scenario_actions()
        if scenario is None:
            return
        self._set_current_scenario_payload(scenario.to_dict())

    def _edit_current_scenario(self, *_args) -> None:
        selected = self.scenario_list.selectedItems()
        if selected:
            scenario_id = selected[0].data(USER_ROLE)
            scenario = self.app_logic.library.load_scenario(scenario_id)
            payload = scenario.to_dict()
        else:
            payload = self._current_scenario_payload
        self._set_current_scenario_payload(payload)
        self._open_scenario_editor(payload)

    def _handle_trace_selection_changed(self) -> None:
        self.trace_selection_summary.setText(_build_trace_selection_summary(self._selected_trace_records()))
        self._update_trace_actions()
        self._refresh_frame_enable_candidates()
        self._refresh_current_scenario_summary()
        self._refresh_runtime_state()

    def _delete_selected_scenario(self) -> None:
        scenario = self._selected_scenario_record()
        if scenario is None:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("删除场景")
        box.setText("确认删除当前选中的场景吗？")
        box.setInformativeText(_build_scenario_delete_summary(scenario))
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() != QMessageBox.Yes:
            return
        try:
            self.app_logic.delete_scenario(scenario.scenario_id)
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))
            return
        if _should_reset_current_scenario_after_delete(self._current_scenario_payload, scenario.scenario_id):
            fallback_payload = self._default_scenario_payload()
            self._set_current_scenario_payload(fallback_payload)
            if self._scenario_editor is not None and self._scenario_editor.current_scenario_id() == scenario.scenario_id:
                self._scenario_editor.hide()
                self._scenario_editor.load_payload(fallback_payload)
        self._refresh_scenarios()

    def _handle_override_channel_changed(self) -> None:
        self._refresh_override_candidates()

    def _handle_override_message_changed(self) -> None:
        self._refresh_override_signal_options()
        self._update_override_actions()

    def _handle_frame_enable_channel_changed(self) -> None:
        self._refresh_frame_enable_message_options()
        self._update_frame_enable_actions()

    def _handle_frame_enable_message_changed(self) -> None:
        self._update_frame_enable_actions()

    def _scenario_from_current_source(self, use_selected_trace_fallback: bool) -> tuple[ScenarioSpec, ReplayLaunchSource]:
        if self._scenario_editor is not None and self._scenario_editor.isVisible():
            scenario = self._scenario_editor.export_scenario(use_selected_trace_fallback=False)
            payload = scenario.to_dict()
        else:
            scenario = ScenarioSpec.from_dict(dict(self._current_scenario_payload))
            payload = scenario.to_dict()
        launch_source = ReplayLaunchSource.SCENARIO_BOUND
        if use_selected_trace_fallback and not payload.get("trace_file_ids") and self._selected_trace_ids():
            payload["trace_file_ids"] = self._selected_trace_ids()
            launch_source = ReplayLaunchSource.SELECTED_FALLBACK
        scenario = ScenarioSpec.from_dict(payload)
        self._set_current_scenario_payload(scenario.to_dict())
        return scenario, launch_source

    def _handle_replay_prepare_succeeded(self, result: Any) -> None:
        try:
            if not isinstance(result, ReplayPreparation):
                raise TypeError("回放准备结果无效。")
            self.app_logic.start_prepared_replay(result)
        except Exception as exc:
            QMessageBox.critical(self, "回放失败", str(exc))
            self._refresh_runtime_state()
            return
        self._refresh_overrides()
        self._refresh_frame_enables()
        self._refresh_runtime_state()
        self._refresh_logs()

    def _handle_replay_prepare_failed(self, message: str) -> None:
        QMessageBox.critical(self, "回放失败", message)

    def _begin_start_replay(self) -> None:
        if self._replay_prepare_in_progress:
            return
        try:
            scenario, launch_source = self._scenario_from_current_source(use_selected_trace_fallback=True)
            loop_enabled = self.loop_playback_checkbox.isChecked()
        except Exception as exc:
            QMessageBox.critical(self, "回放失败", str(exc))
            return
        self._set_replay_prepare_busy(True, trace_count=len(scenario.trace_file_ids))
        self._replay_prepare_thread, self._replay_prepare_worker = self._start_background_task(
            lambda: self.app_logic.prepare_replay(
                scenario,
                launch_source=launch_source,
                loop_enabled=loop_enabled,
            ),
            on_success=self._handle_replay_prepare_succeeded,
            on_failure=self._handle_replay_prepare_failed,
            on_cleanup=self._clear_replay_prepare_task,
        )

    def _start_replay(self) -> None:
        self._begin_start_replay()

    def _pause_replay(self) -> None:
        self.app_logic.pause_replay()
        self._refresh_runtime_state()

    def _resume_replay(self) -> None:
        self.app_logic.resume_replay()
        self._refresh_runtime_state()

    def _stop_replay(self) -> None:
        self.app_logic.stop_replay()
        self._refresh_overrides()
        self._refresh_frame_enables()
        self._refresh_runtime_state()

    def _apply_override(self) -> None:
        try:
            message_id = self._current_override_message_id()
            if message_id is None:
                raise ValueError("报文 ID 必须是十进制或十六进制整数。")
            signal_name = self.override_signal.currentText().strip()
            if not signal_name:
                raise ValueError("信号名不能为空。")
            value = _parse_scalar_text(self.override_value.text().strip())
            if value == "":
                raise ValueError("覆盖值不能为空。")
            override = SignalOverride(
                logical_channel=self.override_channel.value(),
                message_id_or_pgn=message_id,
                signal_name=signal_name,
                value=value,
            )
            self.app_logic.set_workspace_signal_override(override, sync_runtime=True)
        except Exception as exc:
            QMessageBox.critical(self, "信号覆盖失败", str(exc))
            return
        self._refresh_overrides()
        self._refresh_override_signal_hint()

    def _load_scenario_signal_overrides(self) -> None:
        try:
            scenario = ScenarioSpec.from_dict(dict(self._current_scenario_payload))
        except Exception as exc:
            QMessageBox.critical(self, "载入失败", str(exc))
            return
        self.app_logic.replace_workspace_signal_overrides(scenario.signal_overrides, sync_runtime=True)
        self._refresh_overrides()

    def _write_workspace_overrides_to_scenario(self) -> None:
        try:
            scenario = ScenarioSpec.from_dict(dict(self._current_scenario_payload))
            self.app_logic.validate_workspace_signal_overrides(scenario.database_bindings)
        except Exception as exc:
            QMessageBox.critical(self, "写回失败", str(exc))
            return
        payload = _clone_jsonable(self._current_scenario_payload)
        payload["signal_overrides"] = _signal_override_payload_items(self.app_logic.list_workspace_signal_overrides())
        self._set_current_scenario_payload(payload)
        if self._scenario_editor is not None and self._scenario_editor.isVisible():
            self._scenario_editor.replace_signal_overrides(payload["signal_overrides"])
        QMessageBox.information(self, "写回完成", "当前工作区覆盖已写回到当前场景草稿。")

    def _apply_frame_enable(self) -> None:
        try:
            message_id = self._current_frame_enable_message_id()
            if message_id is None:
                raise ValueError("报文 ID 必须是十进制或十六进制整数。")
            logical_channel = self.frame_enable_channel.value()
            enabled = self.frame_enable_status.currentText().strip() == FRAME_ENABLE_STATUS_OPTIONS[0]
            self.app_logic.frame_enables.set_enabled(logical_channel, message_id, enabled)
            self.app_logic.log_info(
                f"帧使能：LC{logical_channel} ID=0x{message_id:X} 已{_frame_enable_status_text(enabled)}。"
            )
        except Exception as exc:
            QMessageBox.critical(self, "帧使能设置失败", str(exc))
            return
        self._refresh_frame_enables()
        self._refresh_logs()

    def _delete_selected_overrides(self) -> None:
        rows = sorted({index.row() for index in self.override_table.selectedIndexes()})
        if not rows:
            return
        for row in rows:
            item = self.override_table.item(row, 0)
            if item is None:
                continue
            key = item.data(USER_ROLE)
            if not key:
                continue
            self.app_logic.clear_workspace_signal_override(*key, sync_runtime=True)
        self._refresh_overrides()

    def _delete_selected_frame_enables(self) -> None:
        rows = sorted({index.row() for index in self.frame_enable_table.selectedIndexes()})
        if not rows:
            return
        cleared = 0
        for row in rows:
            item = self.frame_enable_table.item(row, 0)
            if item is None:
                continue
            key = item.data(USER_ROLE)
            if not key:
                continue
            self.app_logic.frame_enables.clear_rule(*key)
            cleared += 1
        if cleared:
            self.app_logic.log_info(f"帧使能：已恢复 {cleared} 条报文为默认启用。")
        self._refresh_frame_enables()
        self._refresh_logs()

    def _clear_all_overrides(self) -> None:
        self.app_logic.clear_workspace_signal_overrides(sync_runtime=True)
        self._refresh_overrides()

    def _clear_all_frame_enables(self) -> None:
        rules = self.app_logic.frame_enables.list_rules()
        if not rules:
            return
        self.app_logic.frame_enables.clear_all()
        self.app_logic.log_info(f"帧使能：已清空 {len(rules)} 条禁用规则，恢复默认启用。")
        self._refresh_frame_enables()
        self._refresh_logs()

    def _clear_logs(self) -> None:
        self.app_logic.clear_logs()
        self._log_cursor = 0
        self.log_view.clear()
        self.log_content_stack.setCurrentIndex(0)
