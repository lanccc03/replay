from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

from replay_platform.app_controller import ReplayApplication
from replay_platform.core import ScenarioSpec, SignalOverride


class MainWindowMixin:
    """Small helper mixin so the UI module stays compact."""

    def _default_scenario_payload(self) -> dict:
        return {
            "scenario_id": uuid.uuid4().hex,
            "name": "新场景",
            "trace_file_ids": [],
            "bindings": [],
            "database_bindings": [],
            "signal_overrides": [],
            "diagnostic_targets": [],
            "diagnostic_actions": [],
            "link_actions": [],
            "metadata": {},
        }


def build_main_window(app_logic: ReplayApplication):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QFileDialog,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QSpinBox,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    class MainWindow(QMainWindow, MainWindowMixin):
        def __init__(self) -> None:
            super().__init__()
            self.app_logic = app_logic
            self._log_cursor = 0
            self.setWindowTitle("多总线回放与诊断平台")
            self.resize(1480, 920)
            self._build_ui()
            self._refresh_all()
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._refresh_logs)
            self._timer.start(250)

        def _build_ui(self) -> None:
            root = QWidget()
            self.setCentralWidget(root)
            self.setStyleSheet(
                """
                QMainWindow, QWidget { background: #f4efe8; color: #1e1a16; }
                QGroupBox {
                    border: 2px solid #d2b48c;
                    border-radius: 12px;
                    margin-top: 12px;
                    font-weight: 700;
                    background: #fffaf4;
                }
                QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }
                QPushButton {
                    background: #c84b31;
                    color: white;
                    border: none;
                    border-radius: 10px;
                    padding: 8px 14px;
                    font-weight: 700;
                }
                QPushButton:hover { background: #a53e28; }
                QPlainTextEdit, QListWidget, QTableWidget, QLineEdit, QSpinBox {
                    background: #fff;
                    border: 1px solid #cfbca3;
                    border-radius: 10px;
                    padding: 6px;
                }
                """
            )

            layout = QHBoxLayout(root)
            splitter = QSplitter()
            layout.addWidget(splitter)

            left = QWidget()
            left_layout = QVBoxLayout(left)
            right = QWidget()
            right_layout = QVBoxLayout(right)
            splitter.addWidget(left)
            splitter.addWidget(right)
            splitter.setSizes([440, 1040])

            files_box = QGroupBox("文件库")
            files_layout = QVBoxLayout(files_box)
            self.trace_list = QListWidget()
            files_layout.addWidget(self.trace_list)
            files_buttons = QHBoxLayout()
            self.import_button = QPushButton("导入回放文件")
            self.import_button.clicked.connect(self._import_trace)
            self.refresh_button = QPushButton("刷新")
            self.refresh_button.clicked.connect(self._refresh_all)
            files_buttons.addWidget(self.import_button)
            files_buttons.addWidget(self.refresh_button)
            files_layout.addLayout(files_buttons)
            left_layout.addWidget(files_box)

            scenarios_box = QGroupBox("场景")
            scenarios_layout = QVBoxLayout(scenarios_box)
            self.scenario_list = QListWidget()
            self.scenario_list.itemSelectionChanged.connect(self._load_selected_scenario)
            scenarios_layout.addWidget(self.scenario_list)
            scenario_buttons = QHBoxLayout()
            self.new_scenario_button = QPushButton("新建场景")
            self.new_scenario_button.clicked.connect(self._new_scenario)
            self.save_scenario_button = QPushButton("保存场景")
            self.save_scenario_button.clicked.connect(self._save_scenario)
            scenario_buttons.addWidget(self.new_scenario_button)
            scenario_buttons.addWidget(self.save_scenario_button)
            scenarios_layout.addLayout(scenario_buttons)
            left_layout.addWidget(scenarios_box)

            editor_box = QGroupBox("场景 JSON")
            editor_layout = QVBoxLayout(editor_box)
            self.scenario_editor = QPlainTextEdit()
            editor_layout.addWidget(self.scenario_editor)
            right_layout.addWidget(editor_box, stretch=4)

            controls_box = QGroupBox("回放控制")
            controls_layout = QVBoxLayout(controls_box)
            controls_buttons = QHBoxLayout()
            self.start_button = QPushButton("开始")
            self.start_button.clicked.connect(self._start_replay)
            self.pause_button = QPushButton("暂停")
            self.pause_button.clicked.connect(self.app_logic.pause_replay)
            self.resume_button = QPushButton("继续")
            self.resume_button.clicked.connect(self.app_logic.resume_replay)
            self.stop_button = QPushButton("停止")
            self.stop_button.clicked.connect(self.app_logic.stop_replay)
            for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button):
                controls_buttons.addWidget(button)
            controls_layout.addLayout(controls_buttons)
            self.status_label = QLabel("状态：已停止")
            controls_layout.addWidget(self.status_label)
            right_layout.addWidget(controls_box)

            override_box = QGroupBox("手动信号覆盖")
            override_layout = QVBoxLayout(override_box)
            form = QHBoxLayout()
            self.override_channel = QSpinBox()
            self.override_channel.setRange(0, 255)
            self.override_message = QLineEdit("0x123")
            self.override_signal = QLineEdit("vehicle_speed")
            self.override_value = QLineEdit("10")
            self.override_apply = QPushButton("应用覆盖")
            self.override_apply.clicked.connect(self._apply_override)
            form.addWidget(QLabel("通道"))
            form.addWidget(self.override_channel)
            form.addWidget(QLabel("报文"))
            form.addWidget(self.override_message)
            form.addWidget(QLabel("信号"))
            form.addWidget(self.override_signal)
            form.addWidget(QLabel("值"))
            form.addWidget(self.override_value)
            form.addWidget(self.override_apply)
            override_layout.addLayout(form)
            self.override_table = QTableWidget(0, 4)
            self.override_table.setHorizontalHeaderLabels(["通道", "报文", "信号", "值"])
            override_layout.addWidget(self.override_table)
            right_layout.addWidget(override_box, stretch=1)

            log_box = QGroupBox("运行日志")
            log_layout = QVBoxLayout(log_box)
            self.log_view = QPlainTextEdit()
            self.log_view.setReadOnly(True)
            log_layout.addWidget(self.log_view)
            right_layout.addWidget(log_box, stretch=2)

        def _refresh_all(self) -> None:
            self._refresh_traces()
            self._refresh_scenarios()
            self._refresh_overrides()
            self._refresh_status()

        def _refresh_traces(self) -> None:
            self.trace_list.clear()
            for record in self.app_logic.list_traces():
                item = QListWidgetItem(f"{record.name} | {record.format.upper()} | {record.event_count} 帧")
                item.setData(32, record.trace_id)
                self.trace_list.addItem(item)

        def _refresh_scenarios(self) -> None:
            self.scenario_list.clear()
            for scenario in self.app_logic.list_scenarios():
                item = QListWidgetItem(scenario.name)
                item.setData(32, scenario.scenario_id)
                self.scenario_list.addItem(item)

        def _refresh_logs(self) -> None:
            while self._log_cursor < len(self.app_logic.logs):
                self.log_view.appendPlainText(self.app_logic.logs[self._log_cursor])
                self._log_cursor += 1
            self._refresh_status()

        def _refresh_status(self) -> None:
            state_labels = {
                "STOPPED": "已停止",
                "RUNNING": "运行中",
                "PAUSED": "已暂停",
            }
            state_text = state_labels.get(self.app_logic.engine.state.value, self.app_logic.engine.state.value)
            self.status_label.setText(f"状态：{state_text}")

        def _refresh_overrides(self) -> None:
            overrides = self.app_logic.signal_overrides.list_overrides()
            self.override_table.setRowCount(len(overrides))
            for row, override in enumerate(overrides):
                self.override_table.setItem(row, 0, QTableWidgetItem(str(override.logical_channel)))
                self.override_table.setItem(row, 1, QTableWidgetItem(hex(override.message_id_or_pgn)))
                self.override_table.setItem(row, 2, QTableWidgetItem(override.signal_name))
                self.override_table.setItem(row, 3, QTableWidgetItem(str(override.value)))

        def _import_trace(self) -> None:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "导入回放文件",
                str(Path.cwd()),
                "Trace 文件 (*.asc *.blf)",
            )
            if not path:
                return
            try:
                self.app_logic.import_trace(path)
            except Exception as exc:
                QMessageBox.critical(self, "导入失败", str(exc))
                return
            self._refresh_traces()

        def _new_scenario(self) -> None:
            self.scenario_editor.setPlainText(
                json.dumps(self._default_scenario_payload(), indent=2, ensure_ascii=True)
            )

        def _load_selected_scenario(self) -> None:
            selected = self.scenario_list.selectedItems()
            if not selected:
                return
            scenario_id = selected[0].data(32)
            scenario = self.app_logic.library.load_scenario(scenario_id)
            self.scenario_editor.setPlainText(
                json.dumps(scenario.to_dict(), indent=2, ensure_ascii=True)
            )

        def _save_scenario(self) -> None:
            try:
                payload = json.loads(self.scenario_editor.toPlainText())
                scenario = ScenarioSpec.from_dict(payload)
                self.app_logic.save_scenario(scenario)
            except Exception as exc:
                QMessageBox.critical(self, "保存失败", str(exc))
                return
            self._refresh_scenarios()

        def _selected_trace_ids(self):
            return [item.data(32) for item in self.trace_list.selectedItems()]

        def _start_replay(self) -> None:
            try:
                payload = json.loads(self.scenario_editor.toPlainText())
                if not payload.get("trace_file_ids"):
                    payload["trace_file_ids"] = self._selected_trace_ids()
                scenario = ScenarioSpec.from_dict(payload)
                self.app_logic.start_replay(scenario)
            except Exception as exc:
                QMessageBox.critical(self, "回放失败", str(exc))
                return
            self._refresh_status()

        def _apply_override(self) -> None:
            try:
                message_id = int(self.override_message.text(), 0)
                value_text = self.override_value.text().strip()
                value = float(value_text) if "." in value_text else int(value_text, 0)
                override = SignalOverride(
                    logical_channel=self.override_channel.value(),
                    message_id_or_pgn=message_id,
                    signal_name=self.override_signal.text().strip(),
                    value=value,
                )
                self.app_logic.signal_overrides.set_override(override)
            except Exception as exc:
                QMessageBox.critical(self, "信号覆盖失败", str(exc))
                return
            self._refresh_overrides()

    return MainWindow()
