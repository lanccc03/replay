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
from replay_platform.ui.collection_dialog import CollectionItemDialog
from replay_platform.ui.styles import SCENARIO_EDITOR_STYLESHEET, refresh_widget_style, set_button_variant


class ScenarioEditorUiMixin:

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        toolbar = QFrame()
        toolbar.setObjectName("editorToolbar")
        toolbar_layout = QVBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(16, 14, 16, 14)
        toolbar_layout.setSpacing(10)

        actions = QHBoxLayout()
        self.header_label = QLabel("场景编辑器")
        self.header_label.setProperty("role", "headerTitle")
        actions.addWidget(self.header_label)
        actions.addStretch(1)

        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.close)
        self._set_button_variant(self.close_button, "secondary")
        actions.addWidget(self.close_button)

        self.validate_button = QPushButton("校验场景")
        self.validate_button.clicked.connect(self._validate_scenario)
        self._set_button_variant(self.validate_button, "secondary")
        actions.addWidget(self.validate_button)

        self.save_button = QPushButton("保存场景")
        self.save_button.clicked.connect(self._save_scenario)
        self._set_button_variant(self.save_button, "primary")
        actions.addWidget(self.save_button)
        toolbar_layout.addLayout(actions)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.status_badge_label = QLabel("已保存")
        self.status_badge_label.setProperty("tone", "good")
        self.status_detail_label = QLabel("当前草稿与已保存版本一致。")
        self.status_detail_label.setWordWrap(True)
        self.status_detail_label.setProperty("tone", "muted")
        status_row.addWidget(self.status_badge_label, 0)
        status_row.addWidget(self.status_detail_label, 1)
        toolbar_layout.addLayout(status_row)

        layout.addWidget(toolbar)

        self.editor_tabs = QTabWidget()
        self.editor_tabs.currentChanged.connect(self._handle_tab_changed)
        layout.addWidget(self.editor_tabs, 1)
        self._build_form_tab()
        self._build_json_tab()

    def _build_form_tab(self) -> None:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)

        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        tab_layout.addWidget(self.form_scroll)

        scroll_body = QWidget()
        self.form_scroll.setWidget(scroll_body)
        body_layout = QVBoxLayout(scroll_body)
        body_layout.setContentsMargins(4, 4, 4, 4)
        body_layout.setSpacing(14)

        self._build_basic_section(body_layout)
        self._build_resource_mapping_section(body_layout)
        self._create_summary_list_section(
            body_layout,
            key="database_bindings",
            title="数据库绑定",
            hint="用数据库文件把逻辑通道映射到 CAN / CAN FD DBC。",
            fields=[
                EditorFieldSpec("logical_channel", "逻辑通道", "int"),
                EditorFieldSpec("path", "文件路径"),
                EditorFieldSpec("format", "格式", "combo", ("dbc",)),
            ],
            normalize_item=lambda payload: _normalize_database_binding_item(payload, path_prefix="database_bindings[0]"),
            summary=_database_binding_summary,
            default_item=lambda: {"logical_channel": 0, "path": "", "format": "dbc"},
        )
        self._create_summary_list_section(
            body_layout,
            key="signal_overrides",
            title="场景初始信号覆盖",
            hint="回放启动前先写入的信号默认值。",
            fields=[
                EditorFieldSpec("logical_channel", "逻辑通道", "int"),
                EditorFieldSpec("message_id_or_pgn", "报文ID", "hex-int"),
                EditorFieldSpec("signal_name", "信号名"),
                EditorFieldSpec("value", "值", "scalar"),
            ],
            normalize_item=lambda payload: _normalize_signal_override_item(payload, path_prefix="signal_overrides[0]"),
            summary=_signal_override_summary,
            default_item=lambda: {"logical_channel": 0, "message_id_or_pgn": 0x123, "signal_name": "vehicle_speed", "value": 0},
        )
        self._create_summary_list_section(
            body_layout,
            key="diagnostic_targets",
            title="诊断目标",
            hint="配置 CAN / DoIP 诊断目标。",
            fields=[
                EditorFieldSpec("name", "名称"),
                EditorFieldSpec("transport", "传输", "combo", TRANSPORT_OPTIONS),
                EditorFieldSpec("adapter_id", "适配器ID"),
                EditorFieldSpec("logical_channel", "逻辑通道", "int"),
                EditorFieldSpec("tx_id", "TX ID", "hex-int"),
                EditorFieldSpec("rx_id", "RX ID", "hex-int"),
                EditorFieldSpec("host", "主机"),
                EditorFieldSpec("port", "端口", "int"),
                EditorFieldSpec("source_address", "源地址", "hex-int"),
                EditorFieldSpec("target_address", "目标地址", "hex-int"),
                EditorFieldSpec("activation_type", "激活类型", "hex-int"),
                EditorFieldSpec("timeout_ms", "超时ms", "int"),
                EditorFieldSpec("metadata", "元数据(JSON)", "json"),
            ],
            normalize_item=lambda payload: _normalize_diagnostic_target_item(payload, path_prefix="diagnostic_targets[0]"),
            summary=_diagnostic_target_summary,
            default_item=lambda: {
                "name": "diag0",
                "transport": DiagnosticTransport.CAN.value,
                "adapter_id": "",
                "logical_channel": 0,
                "tx_id": 0x7E0,
                "rx_id": 0x7E8,
                "host": "",
                "port": 13400,
                "source_address": 0x0E00,
                "target_address": 0x0001,
                "activation_type": 0x00,
                "timeout_ms": 1000,
                "metadata": {},
            },
        )
        self._create_summary_list_section(
            body_layout,
            key="diagnostic_actions",
            title="诊断动作",
            hint="统一时间轴上的诊断请求。",
            fields=[
                EditorFieldSpec("ts_ns", "时间戳ns", "int"),
                EditorFieldSpec("target", "目标名称"),
                EditorFieldSpec("service_id", "SID", "hex-int"),
                EditorFieldSpec("payload", "Payload(hex)", "hex"),
                EditorFieldSpec("transport", "传输", "combo", TRANSPORT_OPTIONS),
                EditorFieldSpec("timeout_ms", "超时ms", "int"),
                EditorFieldSpec("description", "说明"),
                EditorFieldSpec("metadata", "元数据(JSON)", "json"),
            ],
            normalize_item=lambda payload: _normalize_diagnostic_action_item(payload, path_prefix="diagnostic_actions[0]"),
            summary=_diagnostic_action_summary,
            default_item=lambda: {
                "ts_ns": 0,
                "target": "diag0",
                "service_id": 0x10,
                "payload": "",
                "transport": DiagnosticTransport.CAN.value,
                "timeout_ms": 1000,
                "description": "",
                "metadata": {},
            },
        )
        self._create_summary_list_section(
            body_layout,
            key="link_actions",
            title="链路动作",
            hint="统一时间轴上的断连 / 重连动作。",
            fields=[
                EditorFieldSpec("ts_ns", "时间戳ns", "int"),
                EditorFieldSpec("adapter_id", "适配器ID"),
                EditorFieldSpec("action", "动作", "combo", LINK_ACTION_OPTIONS),
                EditorFieldSpec("logical_channel", "逻辑通道", "optional-int"),
                EditorFieldSpec("description", "说明"),
                EditorFieldSpec("metadata", "元数据(JSON)", "json"),
            ],
            normalize_item=lambda payload: _normalize_link_action_item(payload, path_prefix="link_actions[0]"),
            summary=_link_action_summary,
            default_item=lambda: {
                "ts_ns": 0,
                "adapter_id": "zlg0",
                "action": LinkActionType.DISCONNECT.value,
                "logical_channel": None,
                "description": "",
                "metadata": {},
            },
        )
        self._collection_sections["database_bindings"]["box"].hide()
        self._section_boxes.pop("database_bindings", None)
        self._section_titles.pop("database_bindings", None)
        self._build_metadata_section(body_layout)
        body_layout.addStretch(1)

        self.editor_tabs.addTab(tab, "表单编辑")

    def _build_basic_section(self, parent_layout: QVBoxLayout) -> None:
        box = QGroupBox("基础信息")
        self._register_section("basic", box, "基础信息")
        layout = QGridLayout(box)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)

        self.scenario_id_edit = QLineEdit()
        self.scenario_id_edit.setReadOnly(True)
        scenario_id_container = self._make_field_container("场景 ID", self.scenario_id_edit)
        layout.addWidget(scenario_id_container[0], 0, 0)

        self.scenario_name_edit = QLineEdit()
        self.scenario_name_edit.textChanged.connect(self._handle_user_edit)
        scenario_name_container = self._make_field_container("场景名称", self.scenario_name_edit, "name")
        layout.addWidget(scenario_name_container[0], 0, 1)
        parent_layout.addWidget(box)

    def _build_resource_mapping_section(self, parent_layout: QVBoxLayout) -> None:
        box = QGroupBox("资源映射")
        self._register_section("resources", box, "资源映射")
        layout = QVBoxLayout(box)

        hint = QLabel("先在上方勾选当前场景使用的回放文件，再在下方配置文件映射与当前逻辑通道的DBC。")
        hint.setWordWrap(True)
        hint.setProperty("role", "sectionHint")
        layout.addWidget(hint)

        self.trace_warning_label = QLabel()
        self.trace_warning_label.setWordWrap(True)
        self.trace_warning_label.hide()
        layout.addWidget(self.trace_warning_label)

        self.binding_warning_label = QLabel()
        self.binding_warning_label.setWordWrap(True)
        self.binding_warning_label.hide()
        layout.addWidget(self.binding_warning_label)

        self.orphan_database_label = QLabel()
        self.orphan_database_label.setWordWrap(True)
        self.orphan_database_label.hide()
        layout.addWidget(self.orphan_database_label)

        self.orphan_database_list = QListWidget()
        self.orphan_database_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.orphan_database_list.itemSelectionChanged.connect(self._update_orphan_database_buttons)
        self.orphan_database_list.hide()
        layout.addWidget(self.orphan_database_list)

        orphan_action_row = QHBoxLayout()
        self.remove_orphan_database_button = QPushButton("移除选中孤立DBC")
        self.remove_orphan_database_button.clicked.connect(self._remove_selected_orphan_database_binding)
        self._set_button_variant(self.remove_orphan_database_button, "danger")
        self.remove_orphan_database_button.hide()
        orphan_action_row.addWidget(self.remove_orphan_database_button)
        orphan_action_row.addStretch(1)
        layout.addLayout(orphan_action_row)

        trace_title = QLabel("场景文件")
        trace_title.setProperty("role", "sectionHint")
        layout.addWidget(trace_title)

        self.scenario_trace_list = QListWidget()
        self.scenario_trace_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.scenario_trace_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._apply_checkable_list_style_compatibility(self.scenario_trace_list)
        self.scenario_trace_list.itemChanged.connect(self._handle_user_edit)
        layout.addWidget(self.scenario_trace_list)

        action_row = QHBoxLayout()
        self.add_binding_button = QPushButton("新增文件映射")
        self.add_binding_button.clicked.connect(self._add_binding)
        self._set_button_variant(self.add_binding_button, "secondary")
        action_row.addWidget(self.add_binding_button)

        self.remove_binding_button = QPushButton("删除选中")
        self.remove_binding_button.clicked.connect(self._remove_selected_binding)
        self._set_button_variant(self.remove_binding_button, "danger")
        action_row.addWidget(self.remove_binding_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        self.binding_list = QListWidget()
        self.binding_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.binding_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.binding_list.itemSelectionChanged.connect(self._handle_binding_selection_changed)
        self.binding_list.setMinimumWidth(420)
        content_layout.addWidget(self.binding_list, 1)

        self.binding_editor_frame = QFrame()
        self.binding_editor_frame.setObjectName("bindingEditorPanel")
        editor_layout = QVBoxLayout(self.binding_editor_frame)
        editor_layout.setContentsMargins(14, 14, 14, 14)
        editor_layout.setSpacing(10)

        self.binding_editor_hint = QLabel("选择一条文件映射后即可编辑；新增时会优先选中尚未映射的场景文件。")
        self.binding_editor_hint.setWordWrap(True)
        self.binding_editor_hint.setProperty("tone", "muted")
        editor_layout.addWidget(self.binding_editor_hint)

        self.binding_editor_grid = QGridLayout()
        self.binding_editor_grid.setHorizontalSpacing(12)
        self.binding_editor_grid.setVerticalSpacing(10)
        editor_layout.addLayout(self.binding_editor_grid)

        self.binding_trace_file_combo = QComboBox()
        self._add_binding_field("trace_file_id", "文件", self.binding_trace_file_combo, 0, 0)

        self.binding_source_combo = QComboBox()
        self._add_binding_field("source_selector", "源项", self.binding_source_combo, 0, 1)

        self.binding_adapter_id_edit = QLineEdit()
        self._add_binding_field("adapter_id", "适配器ID", self.binding_adapter_id_edit, 1, 0)

        self.binding_driver_combo = QComboBox()
        self.binding_driver_combo.addItems(list(DRIVER_OPTIONS))
        self._add_binding_field("driver", "驱动", self.binding_driver_combo, 1, 1)

        self.binding_logical_channel_edit = QLineEdit()
        self._add_binding_field("logical_channel", "托管逻辑通道", self.binding_logical_channel_edit, 2, 0)

        self.binding_physical_channel_edit = QLineEdit()
        self._add_binding_field("physical_channel", "物理通道", self.binding_physical_channel_edit, 2, 1)

        self.binding_bus_type_edit = QLineEdit()
        self.binding_bus_type_edit.setReadOnly(True)
        self._add_binding_field("bus_type", "总线类型", self.binding_bus_type_edit, 3, 0)

        self.binding_device_type_combo = QComboBox()
        self.binding_device_type_combo.setEditable(True)
        self._add_binding_field("device_type", "设备类型", self.binding_device_type_combo, 3, 1)

        self.binding_device_index_edit = QLineEdit()
        self._add_binding_field("device_index", "设备索引", self.binding_device_index_edit, 4, 0)

        self.binding_sdk_root_edit = QLineEdit()
        self._add_binding_field("sdk_root", "SDK路径", self.binding_sdk_root_edit, 4, 1)

        self.binding_nominal_baud_edit = QLineEdit()
        self._add_binding_field("nominal_baud", "仲裁波特率", self.binding_nominal_baud_edit, 5, 0)

        self.binding_data_baud_edit = QLineEdit()
        self._add_binding_field("data_baud", "数据波特率", self.binding_data_baud_edit, 5, 1)

        self.binding_resistance_checkbox = QCheckBox("开启")
        self._add_binding_field("resistance_enabled", "终端电阻", self.binding_resistance_checkbox, 6, 0)

        self.binding_listen_only_checkbox = QCheckBox("开启")
        self._add_binding_field("listen_only", "只听", self.binding_listen_only_checkbox, 6, 1)

        self.binding_tx_echo_checkbox = QCheckBox("开启")
        self._add_binding_field("tx_echo", "回显", self.binding_tx_echo_checkbox, 7, 0)

        self.binding_merge_receive_checkbox = QCheckBox("开启")
        self._add_binding_field("merge_receive", "合并接收", self.binding_merge_receive_checkbox, 7, 1)

        self.binding_network_editor = QPlainTextEdit()
        self.binding_network_editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._add_binding_field("network", "网络参数(JSON)", self.binding_network_editor, 8, 0, column_span=2)

        self.binding_metadata_editor = QPlainTextEdit()
        self.binding_metadata_editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._add_binding_field("metadata", "元数据(JSON)", self.binding_metadata_editor, 9, 0, column_span=2)

        self.binding_database_group = QGroupBox("数据库绑定")
        database_layout = QVBoxLayout(self.binding_database_group)
        database_layout.setContentsMargins(12, 12, 12, 12)
        database_layout.setSpacing(8)

        self.binding_database_scope_label = QLabel("当前DBC作用于逻辑通道，共享同通道映射。")
        self.binding_database_scope_label.setWordWrap(True)
        self.binding_database_scope_label.setProperty("tone", "muted")
        database_layout.addWidget(self.binding_database_scope_label)

        database_grid = QGridLayout()
        database_grid.setHorizontalSpacing(12)
        database_grid.setVerticalSpacing(10)
        database_layout.addLayout(database_grid)

        database_channel_label = QLabel("逻辑通道")
        database_grid.addWidget(database_channel_label, 0, 0)
        self.binding_database_channel_edit = QLineEdit()
        self.binding_database_channel_edit.setReadOnly(True)
        database_grid.addWidget(self.binding_database_channel_edit, 0, 1)

        database_format_label = QLabel("格式")
        database_grid.addWidget(database_format_label, 0, 2)
        self.binding_database_format_edit = QLineEdit("dbc")
        self.binding_database_format_edit.setReadOnly(True)
        database_grid.addWidget(self.binding_database_format_edit, 0, 3)

        database_path_label = QLabel("DBC文件")
        database_grid.addWidget(database_path_label, 1, 0)
        path_row = QWidget()
        path_row_layout = QHBoxLayout(path_row)
        path_row_layout.setContentsMargins(0, 0, 0, 0)
        path_row_layout.setSpacing(8)
        self.binding_database_path_edit = QLineEdit()
        path_row_layout.addWidget(self.binding_database_path_edit, 1)
        self.binding_database_browse_button = QPushButton("浏览")
        self.binding_database_browse_button.clicked.connect(self._browse_binding_database_path)
        self._set_button_variant(self.binding_database_browse_button, "secondary")
        path_row_layout.addWidget(self.binding_database_browse_button)
        self.binding_database_clear_button = QPushButton("清空")
        self.binding_database_clear_button.clicked.connect(self._clear_binding_database_path)
        self._set_button_variant(self.binding_database_clear_button, "secondary")
        path_row_layout.addWidget(self.binding_database_clear_button)
        database_grid.addWidget(path_row, 1, 1, 1, 3)

        self.binding_database_status_label = QLabel("状态：当前逻辑通道未绑定DBC。")
        self.binding_database_status_label.setWordWrap(True)
        database_layout.addWidget(self.binding_database_status_label)

        editor_layout.addWidget(self.binding_database_group)
        content_layout.addWidget(self.binding_editor_frame, 2)
        layout.addWidget(content)
        parent_layout.addWidget(box)

        self.binding_database_path_edit.textChanged.connect(self._handle_binding_database_path_text_changed)
        self.binding_database_path_edit.editingFinished.connect(self._handle_binding_database_path_editing_finished)
        self._set_binding_editor_enabled(False)
        self._update_orphan_database_buttons()

    def _build_trace_section(self, parent_layout: QVBoxLayout) -> None:
        box = QGroupBox("场景文件")
        self._register_section("traces", box, "场景文件")
        layout = QVBoxLayout(box)
        hint = QLabel("勾选当前场景要回放的导入文件。缺失文件会保留引用，并以警告提示。")
        hint.setWordWrap(True)
        hint.setProperty("role", "sectionHint")
        layout.addWidget(hint)

        self.trace_warning_label = QLabel()
        self.trace_warning_label.setWordWrap(True)
        self.trace_warning_label.hide()
        layout.addWidget(self.trace_warning_label)

        self.scenario_trace_list = QListWidget()
        self.scenario_trace_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.scenario_trace_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._apply_checkable_list_style_compatibility(self.scenario_trace_list)
        self.scenario_trace_list.itemChanged.connect(self._handle_user_edit)
        layout.addWidget(self.scenario_trace_list)
        parent_layout.addWidget(box)

    def _build_binding_section(self, parent_layout: QVBoxLayout) -> None:
        box = QGroupBox("文件映射")
        self._register_section("bindings", box, "文件映射")
        layout = QVBoxLayout(box)

        hint = QLabel("左侧查看文件映射摘要，右侧配置当前文件的映射参数。")
        hint.setWordWrap(True)
        hint.setProperty("role", "sectionHint")
        layout.addWidget(hint)

        self.binding_warning_label = QLabel()
        self.binding_warning_label.setWordWrap(True)
        self.binding_warning_label.hide()
        layout.addWidget(self.binding_warning_label)

        action_row = QHBoxLayout()
        self.add_binding_button = QPushButton("新增文件映射")
        self.add_binding_button.clicked.connect(self._add_binding)
        self._set_button_variant(self.add_binding_button, "secondary")
        action_row.addWidget(self.add_binding_button)

        self.remove_binding_button = QPushButton("删除选中")
        self.remove_binding_button.clicked.connect(self._remove_selected_binding)
        self._set_button_variant(self.remove_binding_button, "danger")
        action_row.addWidget(self.remove_binding_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        self.binding_list = QListWidget()
        self.binding_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.binding_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.binding_list.itemSelectionChanged.connect(self._handle_binding_selection_changed)
        self.binding_list.setMinimumWidth(360)
        content_layout.addWidget(self.binding_list, 1)

        self.binding_editor_frame = QFrame()
        self.binding_editor_frame.setObjectName("bindingEditorPanel")
        editor_layout = QVBoxLayout(self.binding_editor_frame)
        editor_layout.setContentsMargins(14, 14, 14, 14)
        editor_layout.setSpacing(10)

        self.binding_editor_hint = QLabel("选择一个文件映射后即可编辑；新增时会优先选中尚未映射的场景文件。")
        self.binding_editor_hint.setWordWrap(True)
        self.binding_editor_hint.setProperty("tone", "muted")
        editor_layout.addWidget(self.binding_editor_hint)

        self.binding_editor_grid = QGridLayout()
        self.binding_editor_grid.setHorizontalSpacing(12)
        self.binding_editor_grid.setVerticalSpacing(10)
        editor_layout.addLayout(self.binding_editor_grid)

        self.binding_trace_file_combo = QComboBox()
        self._add_binding_field("trace_file_id", "文件", self.binding_trace_file_combo, 0, 0)

        self.binding_source_combo = QComboBox()
        self._add_binding_field("source_selector", "源项", self.binding_source_combo, 0, 1)

        self.binding_adapter_id_edit = QLineEdit()
        self._add_binding_field("adapter_id", "适配器ID", self.binding_adapter_id_edit, 1, 0)

        self.binding_driver_combo = QComboBox()
        self.binding_driver_combo.addItems(list(DRIVER_OPTIONS))
        self._add_binding_field("driver", "驱动", self.binding_driver_combo, 1, 1)

        self.binding_logical_channel_edit = QLineEdit()
        self.binding_logical_channel_edit.setReadOnly(True)
        self._add_binding_field("logical_channel", "托管逻辑通道", self.binding_logical_channel_edit, 2, 0)

        self.binding_physical_channel_edit = QLineEdit()
        self._add_binding_field("physical_channel", "物理通道", self.binding_physical_channel_edit, 2, 1)

        self.binding_bus_type_edit = QLineEdit()
        self.binding_bus_type_edit.setReadOnly(True)
        self._add_binding_field("bus_type", "总线类型", self.binding_bus_type_edit, 3, 0)

        self.binding_device_type_combo = QComboBox()
        self.binding_device_type_combo.setEditable(True)
        self._add_binding_field("device_type", "设备类型", self.binding_device_type_combo, 3, 1)

        self.binding_device_index_edit = QLineEdit()
        self._add_binding_field("device_index", "设备索引", self.binding_device_index_edit, 4, 0)

        self.binding_sdk_root_edit = QLineEdit()
        self._add_binding_field("sdk_root", "SDK路径", self.binding_sdk_root_edit, 4, 1)

        self.binding_nominal_baud_edit = QLineEdit()
        self._add_binding_field("nominal_baud", "仲裁波特率", self.binding_nominal_baud_edit, 5, 0)

        self.binding_data_baud_edit = QLineEdit()
        self._add_binding_field("data_baud", "数据波特率", self.binding_data_baud_edit, 5, 1)

        self.binding_resistance_checkbox = QCheckBox("开启")
        self._add_binding_field("resistance_enabled", "终端电阻", self.binding_resistance_checkbox, 6, 0)

        self.binding_listen_only_checkbox = QCheckBox("开启")
        self._add_binding_field("listen_only", "只听", self.binding_listen_only_checkbox, 6, 1)

        self.binding_tx_echo_checkbox = QCheckBox("开启")
        self._add_binding_field("tx_echo", "回显", self.binding_tx_echo_checkbox, 7, 0)

        self.binding_merge_receive_checkbox = QCheckBox("开启")
        self._add_binding_field("merge_receive", "合并接收", self.binding_merge_receive_checkbox, 7, 1)

        self.binding_network_editor = QPlainTextEdit()
        self.binding_network_editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._add_binding_field("network", "网络参数(JSON)", self.binding_network_editor, 8, 0, column_span=2)

        self.binding_metadata_editor = QPlainTextEdit()
        self.binding_metadata_editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._add_binding_field("metadata", "元数据(JSON)", self.binding_metadata_editor, 9, 0, column_span=2)

        content_layout.addWidget(self.binding_editor_frame, 2)
        layout.addWidget(content)
        parent_layout.addWidget(box)
        self._set_binding_editor_enabled(False)

    def _build_metadata_section(self, parent_layout: QVBoxLayout) -> None:
        box = QGroupBox("场景元数据")
        self._register_section("metadata", box, "场景元数据")
        layout = QVBoxLayout(box)
        hint = QLabel("填写 JSON 对象；不需要时保持 `{}`。")
        hint.setProperty("role", "sectionHint")
        layout.addWidget(hint)
        self.metadata_editor = QPlainTextEdit()
        self.metadata_editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.metadata_editor.textChanged.connect(self._handle_metadata_changed)
        metadata_container = self._make_field_container("元数据(JSON)", self.metadata_editor, "metadata", show_label=False)
        layout.addWidget(metadata_container[0])
        parent_layout.addWidget(box)

    def _build_json_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.json_preview_note = QLabel("JSON 预览与当前最近一次有效草稿一致。")
        self.json_preview_note.setWordWrap(True)
        self.json_preview_note.setProperty("tone", "muted")
        layout.addWidget(self.json_preview_note)
        self.scenario_editor = QPlainTextEdit()
        self.scenario_editor.setReadOnly(True)
        layout.addWidget(self.scenario_editor)
        self.editor_tabs.addTab(tab, "JSON 预览")

    def _register_section(self, key: str, box: QGroupBox, title: str) -> None:
        self._section_boxes[key] = box
        self._section_titles[key] = title

    def _apply_checkable_list_style_compatibility(self, widget: QListWidget) -> None:
        current_style = widget.style()
        style_name = current_style.objectName().strip().lower() if current_style is not None else ""
        if not style_name.startswith("windows"):
            return
        # Windows 原生 style 与当前 QSS 叠加时会让 checkable QListWidget 的勾选框几乎不可见。
        fusion_style = QStyleFactory.create("Fusion")
        if fusion_style is not None:
            widget.setStyle(fusion_style)

    def _make_field_container(
        self,
        label_text: str,
        widget: QWidget,
        path: Optional[str] = None,
        *,
        show_label: bool = True,
    ) -> tuple[QWidget, QLabel]:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        if show_label:
            label = QLabel(label_text)
            layout.addWidget(label)
        layout.addWidget(widget)
        error_label = QLabel()
        error_label.setProperty("errorLabel", "true")
        error_label.setWordWrap(True)
        layout.addWidget(error_label)
        if path is not None:
            self._field_widgets[path] = widget
            self._field_error_labels[path] = error_label
        return container, error_label

    def _add_binding_field(
        self,
        key: str,
        label_text: str,
        widget: QWidget,
        row: int,
        column: int,
        *,
        column_span: int = 1,
    ) -> None:
        container, error_label = self._make_field_container(label_text, widget)
        self.binding_editor_grid.addWidget(container, row, column, 1, column_span)
        self._binding_field_widgets[key] = widget
        self._binding_field_error_labels[key] = error_label
        self._connect_binding_widget(widget)
        if isinstance(widget, QPlainTextEdit):
            self._sync_text_edit_height(widget, min_lines=4)

    def _create_summary_list_section(
        self,
        parent_layout: QVBoxLayout,
        *,
        key: str,
        title: str,
        hint: str,
        fields: list[EditorFieldSpec],
        normalize_item: Callable[[dict], dict],
        summary: Callable[[dict], str],
        default_item: Callable[[], dict],
    ) -> None:
        box = QGroupBox(title)
        self._register_section(key, box, title)
        layout = QVBoxLayout(box)

        hint_label = QLabel(hint)
        hint_label.setWordWrap(True)
        hint_label.setProperty("role", "sectionHint")
        layout.addWidget(hint_label)

        button_row = QHBoxLayout()
        add_button = QPushButton("新增")
        self._set_button_variant(add_button, "secondary")
        add_button.clicked.connect(lambda _checked=False, section_key=key: self._edit_collection_item(section_key, None))
        button_row.addWidget(add_button)

        edit_button = QPushButton("编辑选中")
        self._set_button_variant(edit_button, "secondary")
        edit_button.clicked.connect(lambda _checked=False, section_key=key: self._edit_selected_collection_item(section_key))
        button_row.addWidget(edit_button)

        remove_button = QPushButton("删除选中")
        self._set_button_variant(remove_button, "danger")
        remove_button.clicked.connect(lambda _checked=False, section_key=key: self._remove_selected_collection_item(section_key))
        button_row.addWidget(remove_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        list_widget = QListWidget()
        list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        list_widget.itemDoubleClicked.connect(lambda _item, section_key=key: self._edit_selected_collection_item(section_key))
        list_widget.itemSelectionChanged.connect(lambda section_key=key: self._update_collection_buttons(section_key))
        layout.addWidget(list_widget)
        parent_layout.addWidget(box)

        self._collection_sections[key] = {
            "box": box,
            "title": title,
            "hint_label": hint_label,
            "list": list_widget,
            "fields": fields,
            "normalize": normalize_item,
            "summary": summary,
            "default_item": default_item,
            "add_button": add_button,
            "edit_button": edit_button,
            "remove_button": remove_button,
        }
        self._update_collection_buttons(key)

    def _sync_list_height(self, widget: QListWidget, *, min_rows: int = 1) -> None:
        row_count = max(widget.count(), min_rows)
        row_height = widget.sizeHintForRow(0)
        if row_height <= 0:
            row_height = widget.fontMetrics().height() + 16
        height = widget.frameWidth() * 2 + row_height * row_count + 8
        widget.setFixedHeight(height)

    def _sync_text_edit_height(self, editor: QPlainTextEdit, *, min_lines: int = 3) -> None:
        line_height = editor.fontMetrics().lineSpacing()
        block_count = max(editor.document().blockCount(), min_lines)
        height = editor.frameWidth() * 2 + block_count * line_height + 24
        editor.setFixedHeight(height)

    def _apply_editor_styles(self) -> None:
        self.setStyleSheet(SCENARIO_EDITOR_STYLESHEET)

    def _set_button_variant(self, button: QPushButton, variant: str) -> None:
        set_button_variant(button, variant)

    def _refresh_style(self, widget: QWidget) -> None:
        refresh_widget_style(widget)
