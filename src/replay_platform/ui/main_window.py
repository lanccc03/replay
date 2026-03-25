from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from replay_platform.app_controller import ReplayApplication
from replay_platform.core import ScenarioSpec, SignalOverride


USER_ROLE = 32


def _format_table_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True)
    return str(value)


def _parse_int_text(
    raw: str,
    field_name: str,
    *,
    allow_empty: bool = False,
    default: Optional[int] = None,
) -> Optional[int]:
    text = raw.strip()
    if not text:
        if allow_empty:
            return default
        raise ValueError(f"{field_name} 不能为空。")
    try:
        return int(text, 0)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是十进制或十六进制整数：{raw}") from exc


def _parse_bool_text(raw: str, field_name: str) -> bool:
    text = raw.strip().lower()
    if text in {"", "0", "false", "no", "n", "off", "否"}:
        return False
    if text in {"1", "true", "yes", "y", "on", "是"}:
        return True
    raise ValueError(f"{field_name} 必须是 true/false 或 1/0：{raw}")


def _parse_json_object_text(raw: str, field_name: str) -> dict:
    text = raw.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} 必须是 JSON 对象：{raw}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 JSON 对象。")
    return value


def _require_text(raw: str, field_name: str) -> str:
    text = raw.strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空。")
    return text


def _parse_scalar_text(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    if text.lower().startswith("0x"):
        try:
            return int(text, 0)
        except ValueError:
            return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _parse_hex_bytes_text(raw: str, field_name: str) -> str:
    text = raw.strip().replace(" ", "")
    if not text:
        return ""
    try:
        return bytes.fromhex(text).hex()
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是十六进制字节串：{raw}") from exc


def _plan_log_refresh(cursor: int, base_index: int, entry_count: int) -> tuple[str, int]:
    if cursor < base_index:
        return "reset", 0
    offset = min(max(cursor - base_index, 0), entry_count)
    return "append", offset


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
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QDialog,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QScrollArea,
        QSpinBox,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    class ScenarioEditorDialog(QDialog, MainWindowMixin):
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
            self.setWindowTitle("场景编辑器")
            self.resize(1240, 920)
            self._build_ui()
            self.load_payload(self._default_scenario_payload())

        def _build_ui(self) -> None:
            layout = QVBoxLayout(self)

            actions = QHBoxLayout()
            self.form_to_json_button = QPushButton("从表单刷新 JSON")
            self.form_to_json_button.clicked.connect(self._sync_json_from_form)
            self.json_to_form_button = QPushButton("从 JSON 回填表单")
            self.json_to_form_button.clicked.connect(self._sync_form_from_json)
            self.save_button = QPushButton("保存场景")
            self.save_button.clicked.connect(self._save_scenario)
            self.close_button = QPushButton("关闭")
            self.close_button.clicked.connect(self.close)
            for button in (
                self.form_to_json_button,
                self.json_to_form_button,
                self.save_button,
                self.close_button,
            ):
                actions.addWidget(button)
            layout.addLayout(actions)

            self.editor_tabs = QTabWidget()
            layout.addWidget(self.editor_tabs)
            self._build_form_tab()
            self._build_json_tab()

        def _build_form_tab(self) -> None:
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            tab_layout.addWidget(scroll)
            scroll_body = QWidget()
            scroll.setWidget(scroll_body)
            body_layout = QVBoxLayout(scroll_body)

            basic_box = QGroupBox("基础信息")
            basic_layout = QFormLayout(basic_box)
            self.scenario_id_edit = QLineEdit()
            self.scenario_id_edit.setReadOnly(True)
            self.scenario_name_edit = QLineEdit()
            basic_layout.addRow("场景 ID", self.scenario_id_edit)
            basic_layout.addRow("场景名称", self.scenario_name_edit)
            body_layout.addWidget(basic_box)

            traces_box = QGroupBox("场景文件")
            traces_layout = QVBoxLayout(traces_box)
            traces_layout.addWidget(QLabel("勾选当前场景要回放的导入文件。"))
            self.scenario_trace_list = QListWidget()
            self.scenario_trace_list.setMinimumHeight(140)
            traces_layout.addWidget(self.scenario_trace_list)
            body_layout.addWidget(traces_box)

            self.binding_table = self._create_table_section(
                body_layout,
                "通道绑定",
                [
                    "适配器ID",
                    "驱动",
                    "逻辑通道",
                    "物理通道",
                    "总线类型",
                    "设备类型",
                    "设备索引",
                    "SDK路径",
                    "仲裁波特率",
                    "数据波特率",
                    "终端电阻",
                    "只听",
                    "回显",
                    "合并接收",
                    "网络参数(JSON)",
                    "元数据(JSON)",
                ],
                self._add_binding_row,
            )
            self.database_table = self._create_table_section(
                body_layout,
                "数据库绑定",
                ["逻辑通道", "文件路径", "格式"],
                self._add_database_row,
            )
            self.scenario_override_table = self._create_table_section(
                body_layout,
                "场景初始信号覆盖",
                ["逻辑通道", "报文ID/PGN", "信号名", "值"],
                self._add_scenario_override_row,
            )
            self.diagnostic_target_table = self._create_table_section(
                body_layout,
                "诊断目标",
                [
                    "名称",
                    "传输",
                    "适配器ID",
                    "逻辑通道",
                    "TX ID",
                    "RX ID",
                    "主机",
                    "端口",
                    "源地址",
                    "目标地址",
                    "激活类型",
                    "超时ms",
                    "元数据(JSON)",
                ],
                self._add_diagnostic_target_row,
            )
            self.diagnostic_action_table = self._create_table_section(
                body_layout,
                "诊断动作",
                ["时间戳ns", "目标名称", "SID", "Payload(hex)", "传输", "超时ms", "说明", "元数据(JSON)"],
                self._add_diagnostic_action_row,
            )
            self.link_action_table = self._create_table_section(
                body_layout,
                "链路动作",
                ["时间戳ns", "适配器ID", "动作", "逻辑通道", "说明", "元数据(JSON)"],
                self._add_link_action_row,
            )

            metadata_box = QGroupBox("场景元数据")
            metadata_layout = QVBoxLayout(metadata_box)
            metadata_layout.addWidget(QLabel("填写 JSON 对象；不需要时保持 `{}`。"))
            self.metadata_editor = QPlainTextEdit("{}")
            self.metadata_editor.setFixedHeight(90)
            metadata_layout.addWidget(self.metadata_editor)
            body_layout.addWidget(metadata_box)
            body_layout.addStretch(1)

            self.editor_tabs.addTab(tab, "表单编辑")

        def _build_json_tab(self) -> None:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(QLabel("高级场景配置仍可直接编辑 JSON，保存前建议回填到表单页检查。"))
            self.scenario_editor = QPlainTextEdit()
            layout.addWidget(self.scenario_editor)
            self.editor_tabs.addTab(tab, "JSON")

        def _create_table_section(
            self,
            parent_layout,
            title: str,
            headers: list[str],
            add_callback,
        ) -> QTableWidget:
            box = QGroupBox(title)
            layout = QVBoxLayout(box)
            buttons = QHBoxLayout()
            add_button = QPushButton("新增")
            add_button.clicked.connect(add_callback)
            remove_button = QPushButton("删除选中")
            table = QTableWidget(0, len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            table.setAlternatingRowColors(True)
            table.verticalHeader().setVisible(False)
            header = table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.ResizeToContents)
            header.setStretchLastSection(True)
            remove_button.clicked.connect(lambda: self._remove_selected_rows(table))
            buttons.addWidget(add_button)
            buttons.addWidget(remove_button)
            layout.addLayout(buttons)
            layout.addWidget(table)
            parent_layout.addWidget(box)
            return table

        def load_payload(self, payload: dict) -> None:
            scenario = ScenarioSpec.from_dict(payload)
            jsonable = scenario.to_dict()
            self._apply_scenario_to_form(jsonable)
            self._set_scenario_json(jsonable)
            self._on_payload_changed(jsonable)

        def refresh_trace_choices(self) -> None:
            self._populate_trace_choices(set(self._checked_trace_ids()))

        def export_scenario(self, use_selected_trace_fallback: bool = False) -> ScenarioSpec:
            if self.editor_tabs.currentIndex() == 1:
                payload = json.loads(self.scenario_editor.toPlainText())
                if use_selected_trace_fallback and not payload.get("trace_file_ids"):
                    payload["trace_file_ids"] = self._trace_selection_supplier()
            else:
                payload = self._scenario_payload_from_form()
                if use_selected_trace_fallback and not payload["trace_file_ids"]:
                    payload["trace_file_ids"] = self._trace_selection_supplier()
            scenario = ScenarioSpec.from_dict(payload)
            jsonable = scenario.to_dict()
            self._apply_scenario_to_form(jsonable)
            self._set_scenario_json(jsonable)
            self._on_payload_changed(jsonable)
            return scenario

        def _save_scenario(self) -> None:
            try:
                scenario = self.export_scenario(use_selected_trace_fallback=False)
                self.app_logic.save_scenario(scenario)
            except Exception as exc:
                QMessageBox.critical(self, "保存失败", str(exc))
                return
            self._on_saved(scenario.to_dict())

        def _sync_json_from_form(self) -> None:
            try:
                scenario = ScenarioSpec.from_dict(self._scenario_payload_from_form())
            except Exception as exc:
                QMessageBox.critical(self, "同步失败", str(exc))
                return
            jsonable = scenario.to_dict()
            self._set_scenario_json(jsonable)
            self._on_payload_changed(jsonable)
            self.editor_tabs.setCurrentIndex(1)

        def _sync_form_from_json(self) -> None:
            try:
                scenario = ScenarioSpec.from_dict(json.loads(self.scenario_editor.toPlainText()))
            except Exception as exc:
                QMessageBox.critical(self, "同步失败", str(exc))
                return
            jsonable = scenario.to_dict()
            self._apply_scenario_to_form(jsonable)
            self._set_scenario_json(jsonable)
            self._on_payload_changed(jsonable)
            self.editor_tabs.setCurrentIndex(0)

        def _scenario_payload_from_form(self) -> dict:
            scenario_id = self.scenario_id_edit.text().strip() or uuid.uuid4().hex
            return {
                "scenario_id": scenario_id,
                "name": self.scenario_name_edit.text().strip() or "新场景",
                "trace_file_ids": self._checked_trace_ids(),
                "bindings": self._bindings_from_table(),
                "database_bindings": self._database_bindings_from_table(),
                "signal_overrides": self._scenario_overrides_from_table(),
                "diagnostic_targets": self._diagnostic_targets_from_table(),
                "diagnostic_actions": self._diagnostic_actions_from_table(),
                "link_actions": self._link_actions_from_table(),
                "metadata": _parse_json_object_text(self.metadata_editor.toPlainText(), "场景元数据"),
            }

        def _apply_scenario_to_form(self, payload: dict) -> None:
            self.scenario_id_edit.setText(str(payload.get("scenario_id", "")))
            self.scenario_name_edit.setText(str(payload.get("name", "新场景")))
            self.metadata_editor.setPlainText(
                json.dumps(payload.get("metadata", {}), indent=2, ensure_ascii=True)
            )
            self._populate_trace_choices(set(payload.get("trace_file_ids", [])))
            self._populate_binding_table(payload.get("bindings", []))
            self._populate_database_table(payload.get("database_bindings", []))
            self._populate_scenario_override_table(payload.get("signal_overrides", []))
            self._populate_diagnostic_target_table(payload.get("diagnostic_targets", []))
            self._populate_diagnostic_action_table(payload.get("diagnostic_actions", []))
            self._populate_link_action_table(payload.get("link_actions", []))

        def _set_scenario_json(self, payload: dict) -> None:
            self.scenario_editor.setPlainText(json.dumps(payload, indent=2, ensure_ascii=True))

        def _populate_trace_choices(self, checked_trace_ids: set[str]) -> None:
            existing = {record.trace_id: record for record in self.app_logic.list_traces()}
            self.scenario_trace_list.clear()
            for record in existing.values():
                item = QListWidgetItem(f"{record.name} | {record.format.upper()} | {record.event_count} 帧")
                item.setData(USER_ROLE, record.trace_id)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setCheckState(
                    Qt.CheckState.Checked if record.trace_id in checked_trace_ids else Qt.CheckState.Unchecked
                )
                self.scenario_trace_list.addItem(item)
            missing_ids = sorted(trace_id for trace_id in checked_trace_ids if trace_id not in existing)
            for trace_id in missing_ids:
                item = QListWidgetItem(f"缺失文件 | {trace_id}")
                item.setData(USER_ROLE, trace_id)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setCheckState(Qt.CheckState.Checked)
                self.scenario_trace_list.addItem(item)

        def _checked_trace_ids(self) -> list[str]:
            trace_ids = []
            for index in range(self.scenario_trace_list.count()):
                item = self.scenario_trace_list.item(index)
                if item.checkState() == Qt.CheckState.Checked:
                    trace_ids.append(item.data(USER_ROLE))
            return trace_ids

        def _remove_selected_rows(self, table: QTableWidget) -> None:
            rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
            for row in rows:
                table.removeRow(row)

        def _append_table_row(self, table: QTableWidget, values: list[Any]) -> None:
            row = table.rowCount()
            table.insertRow(row)
            for column in range(table.columnCount()):
                value = values[column] if column < len(values) else ""
                table.setItem(row, column, QTableWidgetItem(_format_table_value(value)))
            table.resizeColumnsToContents()

        def _set_table_rows(self, table: QTableWidget, rows: list[list[Any]]) -> None:
            table.setRowCount(0)
            for values in rows:
                self._append_table_row(table, values)

        def _cell_text(self, table: QTableWidget, row: int, column: int) -> str:
            item = table.item(row, column)
            return item.text().strip() if item else ""

        def _row_is_blank(self, table: QTableWidget, row: int) -> bool:
            for column in range(table.columnCount()):
                if self._cell_text(table, row, column):
                    return False
            return True

        def _add_binding_row(self) -> None:
            self._append_table_row(
                self.binding_table,
                [
                    "zlg0",
                    "zlg",
                    "0",
                    "0",
                    "CANFD",
                    "USBCANFD",
                    "0",
                    "zlgcan_python_251211",
                    "500000",
                    "2000000",
                    "true",
                    "false",
                    "false",
                    "false",
                    "{}",
                    "{}",
                ],
            )

        def _add_database_row(self) -> None:
            self._append_table_row(self.database_table, ["0", "", "dbc"])

        def _add_scenario_override_row(self) -> None:
            self._append_table_row(self.scenario_override_table, ["0", "0x123", "vehicle_speed", "0"])

        def _add_diagnostic_target_row(self) -> None:
            self._append_table_row(
                self.diagnostic_target_table,
                ["diag0", "CAN", "", "0", "0x7E0", "0x7E8", "", "13400", "0x0E00", "0x0001", "0x00", "1000", "{}"],
            )

        def _add_diagnostic_action_row(self) -> None:
            self._append_table_row(self.diagnostic_action_table, ["0", "diag0", "0x10", "", "CAN", "1000", "", "{}"])

        def _add_link_action_row(self) -> None:
            self._append_table_row(self.link_action_table, ["0", "zlg0", "DISCONNECT", "", "", "{}"])

        def _populate_binding_table(self, bindings: list[dict]) -> None:
            rows = [
                [
                    item.get("adapter_id", ""),
                    item.get("driver", ""),
                    item.get("logical_channel", 0),
                    item.get("physical_channel", 0),
                    item.get("bus_type", "CANFD"),
                    item.get("device_type", ""),
                    item.get("device_index", 0),
                    item.get("sdk_root", "zlgcan_python_251211"),
                    item.get("nominal_baud", 500000),
                    item.get("data_baud", 2000000),
                    item.get("resistance_enabled", True),
                    item.get("listen_only", False),
                    item.get("tx_echo", False),
                    item.get("merge_receive", False),
                    item.get("network", {}),
                    item.get("metadata", {}),
                ]
                for item in bindings
            ]
            self._set_table_rows(self.binding_table, rows)

        def _populate_database_table(self, bindings: list[dict]) -> None:
            rows = [[item.get("logical_channel", 0), item.get("path", ""), item.get("format", "dbc")] for item in bindings]
            self._set_table_rows(self.database_table, rows)

        def _populate_scenario_override_table(self, overrides: list[dict]) -> None:
            rows = [
                [
                    item.get("logical_channel", 0),
                    item.get("message_id_or_pgn", 0),
                    item.get("signal_name", ""),
                    item.get("value", ""),
                ]
                for item in overrides
            ]
            self._set_table_rows(self.scenario_override_table, rows)

        def _populate_diagnostic_target_table(self, targets: list[dict]) -> None:
            rows = [
                [
                    item.get("name", ""),
                    item.get("transport", "CAN"),
                    item.get("adapter_id", ""),
                    item.get("logical_channel", 0),
                    item.get("tx_id", 0x7E0),
                    item.get("rx_id", 0x7E8),
                    item.get("host", ""),
                    item.get("port", 13400),
                    item.get("source_address", 0x0E00),
                    item.get("target_address", 0x0001),
                    item.get("activation_type", 0x00),
                    item.get("timeout_ms", 1000),
                    item.get("metadata", {}),
                ]
                for item in targets
            ]
            self._set_table_rows(self.diagnostic_target_table, rows)

        def _populate_diagnostic_action_table(self, actions: list[dict]) -> None:
            rows = [
                [
                    item.get("ts_ns", 0),
                    item.get("target", ""),
                    item.get("service_id", 0),
                    item.get("payload", ""),
                    item.get("transport", "CAN"),
                    item.get("timeout_ms", 1000),
                    item.get("description", ""),
                    item.get("metadata", {}),
                ]
                for item in actions
            ]
            self._set_table_rows(self.diagnostic_action_table, rows)

        def _populate_link_action_table(self, actions: list[dict]) -> None:
            rows = [
                [
                    item.get("ts_ns", 0),
                    item.get("adapter_id", ""),
                    item.get("action", "DISCONNECT"),
                    item.get("logical_channel", ""),
                    item.get("description", ""),
                    item.get("metadata", {}),
                ]
                for item in actions
            ]
            self._set_table_rows(self.link_action_table, rows)

        def _bindings_from_table(self) -> list[dict]:
            bindings = []
            for row in range(self.binding_table.rowCount()):
                if self._row_is_blank(self.binding_table, row):
                    continue
                prefix = f"通道绑定第 {row + 1} 行"
                bindings.append(
                    {
                        "adapter_id": _require_text(self._cell_text(self.binding_table, row, 0), f"{prefix} 适配器ID"),
                        "driver": self._cell_text(self.binding_table, row, 1) or "zlg",
                        "logical_channel": _parse_int_text(self._cell_text(self.binding_table, row, 2), f"{prefix} 逻辑通道"),
                        "physical_channel": _parse_int_text(self._cell_text(self.binding_table, row, 3), f"{prefix} 物理通道"),
                        "bus_type": (self._cell_text(self.binding_table, row, 4) or "CANFD").upper(),
                        "device_type": _require_text(self._cell_text(self.binding_table, row, 5), f"{prefix} 设备类型"),
                        "device_index": _parse_int_text(
                            self._cell_text(self.binding_table, row, 6),
                            f"{prefix} 设备索引",
                            allow_empty=True,
                            default=0,
                        ),
                        "sdk_root": self._cell_text(self.binding_table, row, 7) or "zlgcan_python_251211",
                        "nominal_baud": _parse_int_text(
                            self._cell_text(self.binding_table, row, 8),
                            f"{prefix} 仲裁波特率",
                            allow_empty=True,
                            default=500000,
                        ),
                        "data_baud": _parse_int_text(
                            self._cell_text(self.binding_table, row, 9),
                            f"{prefix} 数据波特率",
                            allow_empty=True,
                            default=2000000,
                        ),
                        "resistance_enabled": _parse_bool_text(self._cell_text(self.binding_table, row, 10), f"{prefix} 终端电阻"),
                        "listen_only": _parse_bool_text(self._cell_text(self.binding_table, row, 11), f"{prefix} 只听"),
                        "tx_echo": _parse_bool_text(self._cell_text(self.binding_table, row, 12), f"{prefix} 回显"),
                        "merge_receive": _parse_bool_text(self._cell_text(self.binding_table, row, 13), f"{prefix} 合并接收"),
                        "network": _parse_json_object_text(self._cell_text(self.binding_table, row, 14), f"{prefix} 网络参数"),
                        "metadata": _parse_json_object_text(self._cell_text(self.binding_table, row, 15), f"{prefix} 元数据"),
                    }
                )
            return bindings

        def _database_bindings_from_table(self) -> list[dict]:
            bindings = []
            for row in range(self.database_table.rowCount()):
                if self._row_is_blank(self.database_table, row):
                    continue
                prefix = f"数据库绑定第 {row + 1} 行"
                bindings.append(
                    {
                        "logical_channel": _parse_int_text(self._cell_text(self.database_table, row, 0), f"{prefix} 逻辑通道"),
                        "path": _require_text(self._cell_text(self.database_table, row, 1), f"{prefix} 文件路径"),
                        "format": self._cell_text(self.database_table, row, 2) or "dbc",
                    }
                )
            return bindings

        def _scenario_overrides_from_table(self) -> list[dict]:
            overrides = []
            for row in range(self.scenario_override_table.rowCount()):
                if self._row_is_blank(self.scenario_override_table, row):
                    continue
                prefix = f"场景初始信号覆盖第 {row + 1} 行"
                overrides.append(
                    {
                        "logical_channel": _parse_int_text(
                            self._cell_text(self.scenario_override_table, row, 0),
                            f"{prefix} 逻辑通道",
                        ),
                        "message_id_or_pgn": _parse_int_text(
                            self._cell_text(self.scenario_override_table, row, 1),
                            f"{prefix} 报文ID/PGN",
                        ),
                        "signal_name": _require_text(self._cell_text(self.scenario_override_table, row, 2), f"{prefix} 信号名"),
                        "value": _parse_scalar_text(self._cell_text(self.scenario_override_table, row, 3)),
                    }
                )
            return overrides

        def _diagnostic_targets_from_table(self) -> list[dict]:
            targets = []
            for row in range(self.diagnostic_target_table.rowCount()):
                if self._row_is_blank(self.diagnostic_target_table, row):
                    continue
                prefix = f"诊断目标第 {row + 1} 行"
                targets.append(
                    {
                        "name": _require_text(self._cell_text(self.diagnostic_target_table, row, 0), f"{prefix} 名称"),
                        "transport": (self._cell_text(self.diagnostic_target_table, row, 1) or "CAN").upper(),
                        "adapter_id": self._cell_text(self.diagnostic_target_table, row, 2),
                        "logical_channel": _parse_int_text(
                            self._cell_text(self.diagnostic_target_table, row, 3),
                            f"{prefix} 逻辑通道",
                            allow_empty=True,
                            default=0,
                        ),
                        "tx_id": _parse_int_text(
                            self._cell_text(self.diagnostic_target_table, row, 4),
                            f"{prefix} TX ID",
                            allow_empty=True,
                            default=0x7E0,
                        ),
                        "rx_id": _parse_int_text(
                            self._cell_text(self.diagnostic_target_table, row, 5),
                            f"{prefix} RX ID",
                            allow_empty=True,
                            default=0x7E8,
                        ),
                        "host": self._cell_text(self.diagnostic_target_table, row, 6),
                        "port": _parse_int_text(
                            self._cell_text(self.diagnostic_target_table, row, 7),
                            f"{prefix} 端口",
                            allow_empty=True,
                            default=13400,
                        ),
                        "source_address": _parse_int_text(
                            self._cell_text(self.diagnostic_target_table, row, 8),
                            f"{prefix} 源地址",
                            allow_empty=True,
                            default=0x0E00,
                        ),
                        "target_address": _parse_int_text(
                            self._cell_text(self.diagnostic_target_table, row, 9),
                            f"{prefix} 目标地址",
                            allow_empty=True,
                            default=0x0001,
                        ),
                        "activation_type": _parse_int_text(
                            self._cell_text(self.diagnostic_target_table, row, 10),
                            f"{prefix} 激活类型",
                            allow_empty=True,
                            default=0x00,
                        ),
                        "timeout_ms": _parse_int_text(
                            self._cell_text(self.diagnostic_target_table, row, 11),
                            f"{prefix} 超时ms",
                            allow_empty=True,
                            default=1000,
                        ),
                        "metadata": _parse_json_object_text(
                            self._cell_text(self.diagnostic_target_table, row, 12),
                            f"{prefix} 元数据",
                        ),
                    }
                )
            return targets

        def _diagnostic_actions_from_table(self) -> list[dict]:
            actions = []
            for row in range(self.diagnostic_action_table.rowCount()):
                if self._row_is_blank(self.diagnostic_action_table, row):
                    continue
                prefix = f"诊断动作第 {row + 1} 行"
                actions.append(
                    {
                        "ts_ns": _parse_int_text(self._cell_text(self.diagnostic_action_table, row, 0), f"{prefix} 时间戳ns"),
                        "target": _require_text(self._cell_text(self.diagnostic_action_table, row, 1), f"{prefix} 目标名称"),
                        "service_id": _parse_int_text(self._cell_text(self.diagnostic_action_table, row, 2), f"{prefix} SID"),
                        "payload": _parse_hex_bytes_text(self._cell_text(self.diagnostic_action_table, row, 3), f"{prefix} Payload"),
                        "transport": (self._cell_text(self.diagnostic_action_table, row, 4) or "CAN").upper(),
                        "timeout_ms": _parse_int_text(
                            self._cell_text(self.diagnostic_action_table, row, 5),
                            f"{prefix} 超时ms",
                            allow_empty=True,
                            default=1000,
                        ),
                        "description": self._cell_text(self.diagnostic_action_table, row, 6),
                        "metadata": _parse_json_object_text(
                            self._cell_text(self.diagnostic_action_table, row, 7),
                            f"{prefix} 元数据",
                        ),
                    }
                )
            return actions

        def _link_actions_from_table(self) -> list[dict]:
            actions = []
            for row in range(self.link_action_table.rowCount()):
                if self._row_is_blank(self.link_action_table, row):
                    continue
                prefix = f"链路动作第 {row + 1} 行"
                logical_channel_raw = self._cell_text(self.link_action_table, row, 3)
                actions.append(
                    {
                        "ts_ns": _parse_int_text(self._cell_text(self.link_action_table, row, 0), f"{prefix} 时间戳ns"),
                        "adapter_id": _require_text(self._cell_text(self.link_action_table, row, 1), f"{prefix} 适配器ID"),
                        "action": (self._cell_text(self.link_action_table, row, 2) or "DISCONNECT").upper(),
                        "logical_channel": _parse_int_text(
                            logical_channel_raw,
                            f"{prefix} 逻辑通道",
                            allow_empty=True,
                            default=None,
                        ),
                        "description": self._cell_text(self.link_action_table, row, 4),
                        "metadata": _parse_json_object_text(
                            self._cell_text(self.link_action_table, row, 5),
                            f"{prefix} 元数据",
                        ),
                    }
                )
            return actions

    class MainWindow(QMainWindow, MainWindowMixin):
        def __init__(self) -> None:
            super().__init__()
            self.app_logic = app_logic
            self._log_cursor = 0
            self._scenario_editor: Optional[ScenarioEditorDialog] = None
            self._current_scenario_payload = ScenarioSpec.from_dict(self._default_scenario_payload()).to_dict()
            self.setWindowTitle("多总线回放与诊断平台")
            self.resize(1380, 920)
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
            splitter.setSizes([420, 960])

            files_box = QGroupBox("文件库")
            files_layout = QVBoxLayout(files_box)
            self.trace_list = QListWidget()
            self.trace_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
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
            scenarios_layout.addWidget(QLabel("单击查看摘要，双击或点击按钮打开二级编辑窗口。"))
            self.scenario_list = QListWidget()
            self.scenario_list.itemSelectionChanged.connect(self._load_selected_scenario)
            self.scenario_list.itemDoubleClicked.connect(self._edit_current_scenario)
            scenarios_layout.addWidget(self.scenario_list)
            scenario_buttons = QHBoxLayout()
            self.new_scenario_button = QPushButton("新建场景")
            self.new_scenario_button.clicked.connect(self._new_scenario)
            self.edit_scenario_button = QPushButton("编辑场景")
            self.edit_scenario_button.clicked.connect(self._edit_current_scenario)
            scenario_buttons.addWidget(self.new_scenario_button)
            scenario_buttons.addWidget(self.edit_scenario_button)
            scenarios_layout.addLayout(scenario_buttons)
            left_layout.addWidget(scenarios_box)

            current_box = QGroupBox("当前场景")
            current_layout = QVBoxLayout(current_box)
            self.current_scenario_name = QLabel()
            self.current_scenario_id = QLabel()
            self.current_scenario_counts = QLabel()
            self.current_scenario_detail = QLabel()
            self.current_scenario_detail.setWordWrap(True)
            self.open_editor_button = QPushButton("打开场景编辑器")
            self.open_editor_button.clicked.connect(self._edit_current_scenario)
            current_layout.addWidget(self.current_scenario_name)
            current_layout.addWidget(self.current_scenario_id)
            current_layout.addWidget(self.current_scenario_counts)
            current_layout.addWidget(self.current_scenario_detail)
            current_layout.addWidget(self.open_editor_button)
            right_layout.addWidget(current_box, stretch=2)

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
            right_layout.addWidget(override_box, stretch=2)

            log_box = QGroupBox("运行日志")
            log_layout = QVBoxLayout(log_box)
            self.log_view = QPlainTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.document().setMaximumBlockCount(self.app_logic.log_limit)
            log_layout.addWidget(self.log_view)
            right_layout.addWidget(log_box, stretch=3)

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
            editor.load_payload(payload)
            editor.show()
            editor.raise_()
            editor.activateWindow()

        def _handle_saved_scenario(self, payload: dict) -> None:
            self._set_current_scenario_payload(payload)
            self._refresh_scenarios()
            self._select_scenario(payload.get("scenario_id", ""))

        def _select_scenario(self, scenario_id: str) -> None:
            for index in range(self.scenario_list.count()):
                item = self.scenario_list.item(index)
                if item.data(USER_ROLE) == scenario_id:
                    self.scenario_list.setCurrentItem(item)
                    item.setSelected(True)
                    return

        def _set_current_scenario_payload(self, payload: dict) -> None:
            scenario = ScenarioSpec.from_dict(payload)
            self._current_scenario_payload = scenario.to_dict()
            self._refresh_current_scenario_summary()

        def _refresh_current_scenario_summary(self) -> None:
            payload = self._current_scenario_payload
            self.current_scenario_name.setText(f"场景名称：{payload.get('name', '未命名场景')}")
            self.current_scenario_id.setText(f"场景 ID：{payload.get('scenario_id', '')}")
            self.current_scenario_counts.setText(
                "文件 {files} 个 | 绑定 {bindings} 条 | 诊断目标 {targets} 个 | 诊断动作 {actions} 条 | 链路动作 {links} 条".format(
                    files=len(payload.get("trace_file_ids", [])),
                    bindings=len(payload.get("bindings", [])),
                    targets=len(payload.get("diagnostic_targets", [])),
                    actions=len(payload.get("diagnostic_actions", [])),
                    links=len(payload.get("link_actions", [])),
                )
            )
            trace_ids = payload.get("trace_file_ids", [])
            if trace_ids:
                trace_preview = "，".join(trace_ids[:3])
                if len(trace_ids) > 3:
                    trace_preview += " ..."
                detail = f"已绑定文件：{trace_preview}"
            else:
                detail = "未在场景中绑定回放文件，开始回放时会尝试使用主窗口当前选中的文件。"
            self.current_scenario_detail.setText(detail)

        def _refresh_all(self) -> None:
            self._refresh_traces()
            self._refresh_scenarios()
            self._refresh_overrides()
            self._refresh_status()
            self._refresh_current_scenario_summary()

        def _refresh_traces(self) -> None:
            self.trace_list.clear()
            for record in self.app_logic.list_traces():
                item = QListWidgetItem(f"{record.name} | {record.format.upper()} | {record.event_count} 帧")
                item.setData(USER_ROLE, record.trace_id)
                self.trace_list.addItem(item)
            if self._scenario_editor is not None:
                self._scenario_editor.refresh_trace_choices()

        def _refresh_scenarios(self) -> None:
            current_scenario_id = self._current_scenario_payload.get("scenario_id", "")
            self.scenario_list.clear()
            for scenario in self.app_logic.list_scenarios():
                item = QListWidgetItem(scenario.name)
                item.setData(USER_ROLE, scenario.scenario_id)
                self.scenario_list.addItem(item)
                if scenario.scenario_id == current_scenario_id:
                    item.setSelected(True)

        def _refresh_logs(self) -> None:
            base_index, logs = self.app_logic.log_snapshot()
            mode, offset = _plan_log_refresh(self._log_cursor, base_index, len(logs))
            if mode == "reset":
                self.log_view.setPlainText("\n".join(logs))
            else:
                for entry in logs[offset:]:
                    self.log_view.appendPlainText(entry)
            self._log_cursor = base_index + len(logs)
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
            payload = self._default_scenario_payload()
            self._set_current_scenario_payload(payload)
            self._open_scenario_editor(payload)

        def _load_selected_scenario(self) -> None:
            selected = self.scenario_list.selectedItems()
            if not selected:
                return
            scenario_id = selected[0].data(USER_ROLE)
            scenario = self.app_logic.library.load_scenario(scenario_id)
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

        def _selected_trace_ids(self) -> list[str]:
            return [item.data(USER_ROLE) for item in self.trace_list.selectedItems()]

        def _scenario_from_current_source(self, use_selected_trace_fallback: bool) -> ScenarioSpec:
            if self._scenario_editor is not None and self._scenario_editor.isVisible():
                scenario = self._scenario_editor.export_scenario(
                    use_selected_trace_fallback=use_selected_trace_fallback
                )
                self._set_current_scenario_payload(scenario.to_dict())
                return scenario
            payload = dict(self._current_scenario_payload)
            if use_selected_trace_fallback and not payload.get("trace_file_ids"):
                payload["trace_file_ids"] = self._selected_trace_ids()
            scenario = ScenarioSpec.from_dict(payload)
            self._set_current_scenario_payload(scenario.to_dict())
            return scenario

        def _start_replay(self) -> None:
            try:
                scenario = self._scenario_from_current_source(use_selected_trace_fallback=True)
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
