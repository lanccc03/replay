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
from replay_platform.ui.styles import MAIN_WINDOW_STYLESHEET, refresh_widget_style, set_badge, set_button_variant, set_tone


class MainWindowUiMixin:

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("mainRoot")
        self.setCentralWidget(root)
        self._apply_main_window_styles()

        layout = QHBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter)

        self.resource_tabs = QTabWidget()
        splitter.addWidget(self.resource_tabs)
        self._build_trace_tab()
        self._build_scenario_tab()

        self.right_splitter = QSplitter(Qt.Vertical)
        self.right_splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.right_splitter)
        splitter.setSizes([380, 1080])

        top_panel = QWidget()
        top_layout = QVBoxLayout(top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(14)
        self.right_splitter.addWidget(top_panel)

        current_box = QGroupBox("当前场景")
        current_layout = QVBoxLayout(current_box)
        current_layout.setSpacing(8)

        header_row = QHBoxLayout()
        self.current_scenario_name = QLabel("未命名场景")
        self.current_scenario_name.setProperty("role", "title")
        header_row.addWidget(self.current_scenario_name, 1)
        self.current_scenario_badge = QLabel("未就绪")
        self._set_badge(self.current_scenario_badge, "未就绪", "warn")
        header_row.addWidget(self.current_scenario_badge, 0)
        current_layout.addLayout(header_row)

        self.current_scenario_counts = QLabel()
        self.current_scenario_counts.setProperty("role", "muted")
        current_layout.addWidget(self.current_scenario_counts)

        self.current_scenario_trace_text = QLabel()
        self.current_scenario_trace_text.setWordWrap(True)
        current_layout.addWidget(self.current_scenario_trace_text)

        self.current_scenario_binding_text = QLabel()
        self.current_scenario_binding_text.setWordWrap(True)
        current_layout.addWidget(self.current_scenario_binding_text)

        self.current_scenario_database_text = QLabel()
        self.current_scenario_database_text.setWordWrap(True)
        current_layout.addWidget(self.current_scenario_database_text)

        self.current_scenario_source = QLabel()
        self.current_scenario_source.setWordWrap(True)
        current_layout.addWidget(self.current_scenario_source)

        self.current_scenario_issue = QLabel()
        self.current_scenario_issue.setWordWrap(True)
        self.current_scenario_issue.hide()
        current_layout.addWidget(self.current_scenario_issue)

        footer_row = QHBoxLayout()
        self.current_scenario_id = QLabel()
        self.current_scenario_id.setProperty("role", "muted")
        footer_row.addWidget(self.current_scenario_id, 1)
        self.copy_scenario_id_button = QPushButton("复制 ID")
        self.copy_scenario_id_button.clicked.connect(self._copy_scenario_id)
        self._set_button_variant(self.copy_scenario_id_button, "secondary")
        footer_row.addWidget(self.copy_scenario_id_button, 0)
        self.open_editor_button = QPushButton("打开场景编辑器")
        self.open_editor_button.clicked.connect(self._edit_current_scenario)
        self._set_button_variant(self.open_editor_button, "secondary")
        footer_row.addWidget(self.open_editor_button, 0)
        current_layout.addLayout(footer_row)
        top_layout.addWidget(current_box)

        controls_box = QGroupBox("回放控制")
        controls_layout = QVBoxLayout(controls_box)
        controls_layout.setSpacing(8)

        runtime_row = QHBoxLayout()
        self.runtime_badge = QLabel("已停止")
        self._set_badge(self.runtime_badge, "已停止", "muted")
        runtime_row.addWidget(self.runtime_badge, 0)
        self.status_label = QLabel("运行状态：已停止。")
        runtime_row.addWidget(self.status_label, 1)
        controls_layout.addLayout(runtime_row)

        controls_buttons = QHBoxLayout()
        controls_buttons.setSpacing(10)
        self.start_button = QPushButton("开始回放")
        self.start_button.clicked.connect(self._begin_start_replay)
        self._set_button_variant(self.start_button, "primary")
        controls_buttons.addWidget(self.start_button)

        self.pause_button = QPushButton("暂停")
        self.pause_button.clicked.connect(self._pause_replay)
        self._set_button_variant(self.pause_button, "secondary")
        controls_buttons.addWidget(self.pause_button)

        self.resume_button = QPushButton("继续")
        self.resume_button.clicked.connect(self._resume_replay)
        self._set_button_variant(self.resume_button, "secondary")
        controls_buttons.addWidget(self.resume_button)

        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self._stop_replay)
        self._set_button_variant(self.stop_button, "danger")
        controls_buttons.addWidget(self.stop_button)
        controls_layout.addLayout(controls_buttons)

        self.loop_playback_checkbox = QCheckBox("循环回放")
        self.loop_playback_checkbox.setChecked(False)
        controls_layout.addWidget(self.loop_playback_checkbox)

        self.stats_label = QLabel()
        self.stats_label.setProperty("role", "muted")
        controls_layout.addWidget(self.stats_label)

        self.runtime_progress_label = QLabel()
        self.runtime_progress_label.setWordWrap(True)
        controls_layout.addWidget(self.runtime_progress_label)

        self.runtime_source_label = QLabel()
        self.runtime_source_label.setWordWrap(True)
        controls_layout.addWidget(self.runtime_source_label)

        self.runtime_device_label = QLabel()
        self.runtime_device_label.setWordWrap(True)
        controls_layout.addWidget(self.runtime_device_label)

        self.runtime_launch_label = QLabel()
        self.runtime_launch_label.setWordWrap(True)
        controls_layout.addWidget(self.runtime_launch_label)
        top_layout.addWidget(controls_box)

        self.workspace_tabs = QTabWidget()
        self._build_override_tab()
        self._build_frame_enable_tab()
        self._build_log_tab()
        self.right_splitter.addWidget(self.workspace_tabs)
        self.right_splitter.setSizes([320, 560])

    def _build_trace_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        hint = QLabel("选择回放文件。仅在当前场景未绑定文件时，开始回放才会回退到这里的选中文件。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        self.trace_search_edit = QLineEdit()
        self.trace_search_edit.setPlaceholderText("搜索回放文件")
        self.trace_search_edit.textChanged.connect(self._render_trace_list)
        layout.addWidget(self.trace_search_edit)

        self.trace_count_label = QLabel()
        self.trace_count_label.setProperty("role", "muted")
        layout.addWidget(self.trace_count_label)

        self.trace_list = QListWidget()
        self.trace_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.trace_list.itemSelectionChanged.connect(self._handle_trace_selection_changed)
        layout.addWidget(self.trace_list, 1)

        self.trace_selection_summary = QLabel()
        self.trace_selection_summary.setWordWrap(True)
        self.trace_selection_summary.setProperty("role", "muted")
        layout.addWidget(self.trace_selection_summary)

        self.trace_operation_label = QLabel()
        self.trace_operation_label.setWordWrap(True)
        self.trace_operation_label.setProperty("role", "muted")
        self.trace_operation_label.hide()
        layout.addWidget(self.trace_operation_label)

        buttons = QHBoxLayout()
        self.import_button = QPushButton("导入回放文件")
        self.import_button.clicked.connect(self._begin_trace_import)
        self._set_button_variant(self.import_button, "secondary")
        buttons.addWidget(self.import_button)

        self.delete_trace_button = QPushButton("删除文件")
        self.delete_trace_button.clicked.connect(self._delete_selected_trace)
        self._set_button_variant(self.delete_trace_button, "danger")
        buttons.addWidget(self.delete_trace_button)

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self._refresh_all)
        self._set_button_variant(self.refresh_button, "secondary")
        buttons.addWidget(self.refresh_button)
        layout.addLayout(buttons)

        self.resource_tabs.addTab(tab, "回放文件")

    def _build_scenario_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        hint = QLabel("单击查看当前场景摘要，双击或点击按钮打开二级编辑窗口。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        self.scenario_search_edit = QLineEdit()
        self.scenario_search_edit.setPlaceholderText("搜索场景")
        self.scenario_search_edit.textChanged.connect(self._render_scenario_list)
        layout.addWidget(self.scenario_search_edit)

        self.scenario_count_label = QLabel()
        self.scenario_count_label.setProperty("role", "muted")
        layout.addWidget(self.scenario_count_label)

        self.scenario_list = QListWidget()
        self.scenario_list.itemSelectionChanged.connect(self._load_selected_scenario)
        self.scenario_list.itemDoubleClicked.connect(self._edit_current_scenario)
        layout.addWidget(self.scenario_list, 1)

        self.scenario_selection_summary = QLabel()
        self.scenario_selection_summary.setWordWrap(True)
        self.scenario_selection_summary.setProperty("role", "muted")
        layout.addWidget(self.scenario_selection_summary)

        buttons = QHBoxLayout()
        self.new_scenario_button = QPushButton("新建场景")
        self.new_scenario_button.clicked.connect(self._new_scenario)
        self._set_button_variant(self.new_scenario_button, "secondary")
        buttons.addWidget(self.new_scenario_button)

        self.edit_scenario_button = QPushButton("编辑场景")
        self.edit_scenario_button.clicked.connect(self._edit_current_scenario)
        self._set_button_variant(self.edit_scenario_button, "secondary")
        buttons.addWidget(self.edit_scenario_button)

        self.delete_scenario_button = QPushButton("删除场景")
        self.delete_scenario_button.clicked.connect(self._delete_selected_scenario)
        self._set_button_variant(self.delete_scenario_button, "danger")
        buttons.addWidget(self.delete_scenario_button)
        layout.addLayout(buttons)

        self.resource_tabs.addTab(tab, "场景")

    def _build_override_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        hint = QLabel("已加载数据库时可直接选择报文和信号；未加载时仍可手动输入。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        self.override_catalog_status = QLabel("数据库状态：当前场景未配置数据库。")
        self.override_catalog_status.setWordWrap(True)
        self.override_catalog_status.setProperty("role", "muted")
        layout.addWidget(self.override_catalog_status)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(QLabel("通道"), 0, 0)
        self.override_channel = QSpinBox()
        self.override_channel.setRange(0, 255)
        self.override_channel.valueChanged.connect(self._handle_override_channel_changed)
        form.addWidget(self.override_channel, 0, 1)

        form.addWidget(QLabel("报文"), 0, 2)
        self.override_message = QComboBox()
        self.override_message.setEditable(True)
        self.override_message.setInsertPolicy(QComboBox.NoInsert)
        self.override_message.currentTextChanged.connect(self._handle_override_message_changed)
        self.override_message.lineEdit().setPlaceholderText("输入 0x123，或选择已加载报文")
        form.addWidget(self.override_message, 0, 3)

        form.addWidget(QLabel("信号"), 1, 0)
        self.override_signal = QComboBox()
        self.override_signal.setEditable(True)
        self.override_signal.setInsertPolicy(QComboBox.NoInsert)
        self.override_signal.currentTextChanged.connect(self._update_override_actions)
        self.override_signal.lineEdit().setPlaceholderText("输入信号名，或选择数据库信号")
        form.addWidget(self.override_signal, 1, 1, 1, 3)

        self.override_signal_hint = QLabel("信号说明：选择数据库信号后会显示单位、范围和枚举值。")
        self.override_signal_hint.setWordWrap(True)
        self.override_signal_hint.setProperty("role", "muted")
        form.addWidget(self.override_signal_hint, 2, 0, 1, 4)

        form.addWidget(QLabel("值"), 3, 0)
        self.override_value = QLineEdit()
        self.override_value.setPlaceholderText("输入覆盖值，例如 10、12.5 或 true")
        self.override_value.textChanged.connect(self._update_override_actions)
        form.addWidget(self.override_value, 3, 1, 1, 2)

        self.override_apply = QPushButton("应用覆盖")
        self.override_apply.clicked.connect(self._apply_override)
        self._set_button_variant(self.override_apply, "secondary")
        form.addWidget(self.override_apply, 3, 3)
        layout.addLayout(form)

        action_row = QHBoxLayout()
        self.load_scenario_overrides_button = QPushButton("载入场景初始覆盖")
        self.load_scenario_overrides_button.clicked.connect(self._load_scenario_signal_overrides)
        self._set_button_variant(self.load_scenario_overrides_button, "secondary")
        action_row.addWidget(self.load_scenario_overrides_button)

        self.write_back_overrides_button = QPushButton("写回当前场景")
        self.write_back_overrides_button.clicked.connect(self._write_workspace_overrides_to_scenario)
        self._set_button_variant(self.write_back_overrides_button, "secondary")
        action_row.addWidget(self.write_back_overrides_button)

        action_row.addStretch(1)
        self.delete_override_button = QPushButton("删除选中覆盖")
        self.delete_override_button.clicked.connect(self._delete_selected_overrides)
        self._set_button_variant(self.delete_override_button, "secondary")
        action_row.addWidget(self.delete_override_button)

        self.clear_overrides_button = QPushButton("清空全部覆盖")
        self.clear_overrides_button.clicked.connect(self._clear_all_overrides)
        self._set_button_variant(self.clear_overrides_button, "danger")
        action_row.addWidget(self.clear_overrides_button)
        layout.addLayout(action_row)

        self.override_content_stack = QStackedWidget()
        self.override_empty_state = self._build_empty_state("当前未设置覆盖；如已加载 DBC，可先选择通道和报文")
        self.override_table = QTableWidget(0, 4)
        self.override_table.setHorizontalHeaderLabels(["通道", "报文", "信号", "值"])
        self.override_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.override_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.override_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.override_table.itemSelectionChanged.connect(self._update_override_actions)
        self.override_table.horizontalHeader().setStretchLastSection(True)
        self.override_content_stack.addWidget(self.override_empty_state)
        self.override_content_stack.addWidget(self.override_table)
        layout.addWidget(self.override_content_stack, 1)

        self.workspace_tabs.addTab(tab, "信号覆盖")

    def _build_frame_enable_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        hint = QLabel("按逻辑通道 + 报文 ID 临时控制发送；仅影响当前回放，停止后恢复默认全启用。")
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        layout.addWidget(hint)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        form.addWidget(QLabel("通道"), 0, 0)
        self.frame_enable_channel = QSpinBox()
        self.frame_enable_channel.setRange(0, 255)
        self.frame_enable_channel.valueChanged.connect(self._handle_frame_enable_channel_changed)
        form.addWidget(self.frame_enable_channel, 0, 1)

        form.addWidget(QLabel("报文"), 0, 2)
        self.frame_enable_message = QComboBox()
        self.frame_enable_message.setEditable(True)
        self.frame_enable_message.setInsertPolicy(QComboBox.NoInsert)
        self.frame_enable_message.currentTextChanged.connect(self._handle_frame_enable_message_changed)
        self.frame_enable_message.lineEdit().setPlaceholderText("输入 0x123，或选择当前回放文件中的报文")
        form.addWidget(self.frame_enable_message, 0, 3)

        form.addWidget(QLabel("状态"), 1, 0)
        self.frame_enable_status = QComboBox()
        self.frame_enable_status.addItems(list(FRAME_ENABLE_STATUS_OPTIONS))
        self.frame_enable_status.currentTextChanged.connect(self._update_frame_enable_actions)
        form.addWidget(self.frame_enable_status, 1, 1)

        self.frame_enable_apply = QPushButton("应用状态")
        self.frame_enable_apply.clicked.connect(self._apply_frame_enable)
        self._set_button_variant(self.frame_enable_apply, "secondary")
        form.addWidget(self.frame_enable_apply, 1, 3)
        layout.addLayout(form)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.delete_frame_enable_button = QPushButton("删除选中规则")
        self.delete_frame_enable_button.clicked.connect(self._delete_selected_frame_enables)
        self._set_button_variant(self.delete_frame_enable_button, "secondary")
        action_row.addWidget(self.delete_frame_enable_button)

        self.clear_frame_enable_button = QPushButton("清空全部规则")
        self.clear_frame_enable_button.clicked.connect(self._clear_all_frame_enables)
        self._set_button_variant(self.clear_frame_enable_button, "danger")
        action_row.addWidget(self.clear_frame_enable_button)
        layout.addLayout(action_row)

        self.frame_enable_content_stack = QStackedWidget()
        self.frame_enable_empty_state = self._build_empty_state("当前未禁用任何报文；仅对当前回放生效。")
        self.frame_enable_table = QTableWidget(0, 3)
        self.frame_enable_table.setHorizontalHeaderLabels(["通道", "报文", "状态"])
        self.frame_enable_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.frame_enable_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.frame_enable_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.frame_enable_table.itemSelectionChanged.connect(self._update_frame_enable_actions)
        self.frame_enable_table.horizontalHeader().setStretchLastSection(True)
        self.frame_enable_content_stack.addWidget(self.frame_enable_empty_state)
        self.frame_enable_content_stack.addWidget(self.frame_enable_table)
        layout.addWidget(self.frame_enable_content_stack, 1)

        self.workspace_tabs.addTab(tab, "帧使能")

    def _build_log_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.log_level_label = QLabel("日志级别")
        self.log_level_label.setProperty("role", "muted")
        actions.addWidget(self.log_level_label)

        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(LOG_LEVEL_OPTIONS)
        self.log_level_combo.setCurrentText(_log_level_option(self.app_logic.current_log_level_preset()))
        actions.addWidget(self.log_level_combo)

        self.auto_scroll_checkbox = QCheckBox("自动滚动")
        self.auto_scroll_checkbox.setChecked(True)
        actions.addWidget(self.auto_scroll_checkbox)

        self.clear_logs_button = QPushButton("清空日志")
        self.clear_logs_button.clicked.connect(self._clear_logs)
        self._set_button_variant(self.clear_logs_button, "danger")
        actions.addWidget(self.clear_logs_button)
        layout.addLayout(actions)

        self.log_level_hint = QLabel()
        self.log_level_hint.setProperty("role", "muted")
        self.log_level_hint.setWordWrap(True)
        layout.addWidget(self.log_level_hint)
        self._refresh_log_level_hint()
        self.log_level_combo.currentTextChanged.connect(self._handle_log_level_changed)

        self.log_content_stack = QStackedWidget()
        self.log_empty_state = self._build_empty_state("暂无运行日志，开始回放后会持续刷新")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(self.app_logic.log_limit)
        self.log_content_stack.addWidget(self.log_empty_state)
        self.log_content_stack.addWidget(self.log_view)
        layout.addWidget(self.log_content_stack, 1)

        self.workspace_tabs.addTab(tab, "运行日志")

    def _build_empty_state(self, message: str) -> QWidget:
        widget = QWidget()
        widget.setProperty("emptyState", True)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addStretch(1)
        label = QLabel(message)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        label.setProperty("role", "emptyState")
        layout.addWidget(label)
        layout.addStretch(1)
        return widget

    def _apply_main_window_styles(self) -> None:
        self.setStyleSheet(MAIN_WINDOW_STYLESHEET)

    def _set_button_variant(self, button: QPushButton, variant: str) -> None:
        set_button_variant(button, variant)

    def _set_badge(self, label: QLabel, text: str, tone: str) -> None:
        set_badge(label, text, tone)

    def _set_tone(self, label: QLabel, tone: str) -> None:
        set_tone(label, tone)

    def _refresh_style(self, widget: QWidget) -> None:
        refresh_widget_style(widget)
