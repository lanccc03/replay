from __future__ import annotations

from typing import Any, Callable, Optional

from replay_platform.ui.qt_imports import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from replay_platform.ui.window_presenters import (
    FieldValidationError,
    EditorFieldSpec,
    USER_ROLE,
    _format_field_value,
)

class CollectionItemDialog(QDialog):
    def __init__(
        self,
        title: str,
        fields: list[EditorFieldSpec],
        normalize_item: Callable[[dict], dict],
        initial_value: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._normalize_item = normalize_item
        self._fields = fields
        self._inputs: dict[str, QWidget] = {}
        self._value: Optional[dict] = None
        self.setWindowTitle(title)
        self.resize(540, 520)
        layout = QVBoxLayout(self)
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        for row, field in enumerate(fields):
            title_label = QLabel(field.label)
            widget = self._create_input(field)
            self._inputs[field.key] = widget
            form.addWidget(title_label, row, 0)
            form.addWidget(widget, row, 1)
        layout.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b42318;")
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if initial_value is not None:
            self._set_initial_values(initial_value)

    def value(self) -> dict:
        return dict(self._value or {})

    def _create_input(self, field: EditorFieldSpec) -> QWidget:
        if field.kind == "combo":
            widget = QComboBox()
            for option in field.options:
                if isinstance(option, tuple) and len(option) >= 2:
                    label, value = option[0], option[1]
                else:
                    label = value = option
                widget.addItem(_display_text(label), value)
            return widget
        if field.kind == "bool":
            return QCheckBox("启用")
        if field.kind == "json":
            widget = QPlainTextEdit()
            widget.setFixedHeight(90)
            return widget
        return QLineEdit()

    def _set_initial_values(self, payload: dict) -> None:
        for field in self._fields:
            widget = self._inputs[field.key]
            value = payload.get(field.key)
            if isinstance(widget, QComboBox):
                matched_index = -1
                for index in range(widget.count()):
                    option_value = widget.itemData(index, USER_ROLE)
                    if option_value == value or _display_text(option_value) == _display_text(value):
                        matched_index = index
                        break
                if matched_index == -1:
                    text = _format_field_value(value, field.kind)
                    if text:
                        widget.addItem(text, value)
                        matched_index = widget.count() - 1
                if matched_index >= 0:
                    widget.setCurrentIndex(matched_index)
                continue
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
                continue
            if isinstance(widget, QPlainTextEdit):
                widget.setPlainText(_format_field_value(value, field.kind))
                continue
            widget.setText(_format_field_value(value, field.kind))

    def _raw_payload(self) -> dict:
        payload: dict[str, Any] = {}
        for field in self._fields:
            widget = self._inputs[field.key]
            if isinstance(widget, QComboBox):
                value = widget.currentData(USER_ROLE)
                payload[field.key] = widget.currentText() if value is None else value
            elif isinstance(widget, QCheckBox):
                payload[field.key] = widget.isChecked()
            elif isinstance(widget, QPlainTextEdit):
                payload[field.key] = widget.toPlainText()
            else:
                payload[field.key] = widget.text()
        return payload

    def _accept(self) -> None:
        try:
            self._value = self._normalize_item(self._raw_payload())
        except FieldValidationError as exc:
            self.error_label.setText(str(exc))
            field_key = exc.path.rsplit(".", 1)[-1]
            widget = self._inputs.get(field_key)
            if widget is not None:
                widget.setFocus()
            return
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.error_label.clear()
        self.accept()
