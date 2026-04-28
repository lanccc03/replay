from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from replay_platform.core import BusType, DiagnosticTransport, LinkActionType, ScenarioSpec


DRIVER_OPTIONS = ("zlg", "mock", "tongxing")
BUS_OPTIONS = tuple(item.value for item in BusType)
TRANSPORT_OPTIONS = tuple(item.value for item in DiagnosticTransport)
LINK_ACTION_OPTIONS = tuple(item.value for item in LinkActionType)
ZLG_DEVICE_TYPE_OPTIONS = (
    "USBCANFD_100U",
    "USBCANFD_200U",
    "USBCANFD_400U",
    "USBCANFD_800U",
    "USBCANFD_MINI",
    "CANFDNET_100U_TCP",
    "CANFDNET_100U_UDP",
    "CANFDNET_200U_TCP",
    "CANFDNET_200U_UDP",
    "CANFDNET_400U_TCP",
    "CANFDNET_400U_UDP",
    "CANFDNET_800U_TCP",
    "CANFDNET_800U_UDP",
)
DRIVER_DEVICE_TYPE_OPTIONS = {
    "zlg": ZLG_DEVICE_TYPE_OPTIONS,
    "mock": ("MOCK",),
    "tongxing": ("TC1014",),
}
LEGACY_ZLG_DEVICE_TYPES = frozenset({"USBCANFD"})


@dataclass(frozen=True)
class ValidationIssue:
    section: str
    path: str
    message: str


@dataclass
class DraftValidationResult:
    normalized_payload: Optional[dict]
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]


class FieldValidationError(ValueError):
    def __init__(self, path: str, message: str) -> None:
        super().__init__(message)
        self.path = path


def _clone_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True))


def _display_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True)
    return str(value)


def _normalize_driver_name(value: Any) -> str:
    return _display_text(value).strip().lower() or "zlg"


def _binding_device_type_options(driver: Any) -> tuple[str, ...]:
    return DRIVER_DEVICE_TYPE_OPTIONS.get(_normalize_driver_name(driver), ())


def _binding_device_type_placeholder(driver: Any) -> str:
    normalized_driver = _normalize_driver_name(driver)
    if normalized_driver == "zlg":
        return "请选择或填写具体型号，例如 USBCANFD_200U"
    if normalized_driver == "tongxing":
        return "请选择或填写设备子型号，例如 TC1014"
    if normalized_driver == "mock":
        return "可保留 MOCK 或按需要手工填写"
    return "请选择或填写设备类型"


def _parse_device_type_text(raw: Any, driver: Any) -> str:
    text = _display_text(raw).strip()
    if _normalize_driver_name(driver) == "zlg":
        return text
    return _require_text(text, "设备类型")


def _binding_warning_subject(binding: dict) -> str:
    adapter_id = _display_text(binding.get("adapter_id", "")).strip() or "未命名适配器"
    logical_channel = _display_text(binding.get("logical_channel", "")).strip()
    if logical_channel:
        return f"{adapter_id} / LC{logical_channel}"
    return adapter_id


def _binding_device_type_warning(binding: dict, index: int) -> Optional[ValidationIssue]:
    if _normalize_driver_name(binding.get("driver", "zlg")) != "zlg":
        return None
    device_type = _display_text(binding.get("device_type", "")).strip()
    subject = _binding_warning_subject(binding)
    if not device_type:
        return ValidationIssue(
            "bindings",
            f"bindings[{index}].device_type",
            f"{subject} 尚未选择具体 ZLG 设备类型，探测物理通道和实际运行前需要补全。",
        )
    if device_type.upper() in LEGACY_ZLG_DEVICE_TYPES:
        return ValidationIssue(
            "bindings",
            f"bindings[{index}].device_type",
            f"{subject} 仍使用旧写法 USBCANFD，建议改成具体型号后再探测或运行。",
        )
    return None


def _format_json_text(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=True)


def _format_field_value(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if kind in {"hex-int", "optional-hex-int"} and isinstance(value, int):
        return hex(value)
    if kind == "json" and isinstance(value, (dict, list)):
        return _format_json_text(value)
    return _display_text(value)


def _parse_int_text(
    raw: Any,
    field_name: str,
    *,
    allow_empty: bool = False,
    default: Optional[int] = None,
) -> Optional[int]:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    text = _display_text(raw).strip()
    if not text:
        if allow_empty:
            return default
        raise ValueError(f"{field_name} 不能为空。")
    try:
        return int(text, 0)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是十进制或十六进制整数：{raw}") from exc


def _parse_bool_text(raw: Any, field_name: str) -> bool:
    if isinstance(raw, bool):
        return raw
    text = _display_text(raw).strip().lower()
    if text in {"", "0", "false", "no", "n", "off", "否"}:
        return False
    if text in {"1", "true", "yes", "y", "on", "是"}:
        return True
    raise ValueError(f"{field_name} 必须是 true/false 或 1/0：{raw}")


def _parse_json_object_text(raw: Any, field_name: str) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    text = _display_text(raw).strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} 必须是 JSON 对象：{raw}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 JSON 对象。")
    return value


def _parse_choice_text(
    raw: Any,
    field_name: str,
    allowed: tuple[str, ...],
    *,
    default: Optional[str] = None,
    normalize: Callable[[str], str] = lambda value: value,
) -> str:
    text = _display_text(raw).strip()
    if not text and default is not None:
        text = default
    normalized = normalize(text)
    if normalized in allowed:
        return normalized
    allowed_text = "/".join(allowed)
    raise ValueError(f"{field_name} 必须是 {allowed_text}：{raw}")


def _require_text(raw: Any, field_name: str) -> str:
    text = _display_text(raw).strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空。")
    return text


def _parse_scalar_text(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
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


def _parse_hex_bytes_text(raw: Any, field_name: str) -> str:
    if isinstance(raw, bytes):
        return raw.hex()
    text = _display_text(raw).strip().replace(" ", "")
    if not text:
        return ""
    try:
        return bytes.fromhex(text).hex()
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是十六进制字节串：{raw}") from exc


def _normalize_scenario_payload(payload: dict) -> dict:
    return ScenarioSpec.from_dict(payload).to_dict()


def _scenario_payload_is_dirty(current_payload: Optional[dict], last_saved_payload: Optional[dict]) -> bool:
    if current_payload is None:
        return False
    if last_saved_payload is None:
        return True
    return _normalize_scenario_payload(current_payload) != _normalize_scenario_payload(last_saved_payload)


def _parse_optional_int_text(raw: Any) -> Optional[int]:
    text = _display_text(raw).strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


def _binding_uses_trace_source(item: dict | Any) -> bool:
    if hasattr(item, "trace_file_id"):
        return bool(getattr(item, "trace_file_id", "")) and getattr(item, "source_channel", None) is not None and getattr(
            item, "source_bus_type", None
        ) is not None
    trace_file_id = _display_text(item.get("trace_file_id", "")).strip()
    return bool(trace_file_id) and _parse_optional_int_text(item.get("source_channel")) is not None and bool(
        _display_text(item.get("source_bus_type", "")).strip()
    )


def _database_binding_map_from_items(items: Sequence[dict]) -> tuple[dict[int, dict], dict[int, int]]:
    mapping: dict[int, dict] = {}
    duplicate_counts: dict[int, int] = {}
    for item in items:
        logical_channel = _parse_optional_int_text(item.get("logical_channel"))
        if logical_channel is None:
            continue
        normalized_item = {
            "logical_channel": logical_channel,
            "path": _display_text(item.get("path", "")).strip(),
            "format": _display_text(item.get("format", "")).strip() or "dbc",
        }
        if logical_channel in mapping:
            duplicate_counts[logical_channel] = duplicate_counts.get(logical_channel, 1) + 1
        mapping[logical_channel] = normalized_item
    return mapping, duplicate_counts


def _database_binding_items_from_map(binding_map: Mapping[int, dict]) -> list[dict]:
    items: list[dict] = []
    for logical_channel in sorted(binding_map):
        item = binding_map[logical_channel]
        path = _display_text(item.get("path", "")).strip()
        if not path:
            continue
        items.append(
            {
                "logical_channel": logical_channel,
                "path": path,
                "format": _display_text(item.get("format", "")).strip() or "dbc",
            }
        )
    return items


def _database_binding_orphan_items(binding_map: Mapping[int, dict], bindings: Sequence[dict]) -> list[dict]:
    used_channels = {
        logical_channel
        for logical_channel in (_parse_optional_int_text(item.get("logical_channel")) for item in bindings)
        if logical_channel is not None
    }
    return [
        dict(binding_map[logical_channel])
        for logical_channel in sorted(binding_map)
        if logical_channel not in used_channels and _display_text(binding_map[logical_channel].get("path", "")).strip()
    ]


def _new_binding_draft(logical_channel: int = 0) -> dict:
    return {
        "trace_file_id": "",
        "source_channel": "",
        "source_bus_type": "",
        "adapter_id": "zlg0",
        "driver": "zlg",
        "logical_channel": str(logical_channel),
        "physical_channel": str(logical_channel),
        "bus_type": "CANFD",
        "device_type": "",
        "device_index": "0",
        "sdk_root": _default_sdk_root_for_driver("zlg"),
        "nominal_baud": "500000",
        "data_baud": "2000000",
        "resistance_enabled": True,
        "listen_only": False,
        "tx_echo": False,
        "merge_receive": False,
        "network": "{}",
        "metadata": "{}",
    }


def _default_sdk_root_for_driver(driver: Any) -> str:
    return "TSMasterApi" if _display_text(driver).strip().lower() == "tongxing" else "zlgcan_python_251211"


def _binding_draft_from_item(item: dict) -> dict:
    driver = _normalize_driver_name(item.get("driver", "zlg"))
    return {
        "trace_file_id": item.get("trace_file_id", ""),
        "source_channel": _display_text(item.get("source_channel", "")),
        "source_bus_type": _display_text(item.get("source_bus_type", "")).upper(),
        "adapter_id": item.get("adapter_id", ""),
        "driver": driver,
        "logical_channel": _display_text(item.get("logical_channel", 0)),
        "physical_channel": _display_text(item.get("physical_channel", 0)),
        "bus_type": _display_text(item.get("bus_type", "CANFD")).upper() or "CANFD",
        "device_type": item.get("device_type", ""),
        "device_index": _display_text(item.get("device_index", 0)),
        "sdk_root": item.get("sdk_root", _default_sdk_root_for_driver(driver)),
        "nominal_baud": _display_text(item.get("nominal_baud", 500000)),
        "data_baud": _display_text(item.get("data_baud", 2000000)),
        "resistance_enabled": bool(item.get("resistance_enabled", True)),
        "listen_only": bool(item.get("listen_only", False)),
        "tx_echo": bool(item.get("tx_echo", False)),
        "merge_receive": bool(item.get("merge_receive", False)),
        "network": _format_json_text(item.get("network", {})),
        "metadata": _format_json_text(item.get("metadata", {})),
    }


def _normalize_binding_item(item: dict, *, path_prefix: str = "bindings[0]") -> dict:
    try:
        driver = _parse_choice_text(
            item.get("driver", "zlg"),
            "驱动",
            DRIVER_OPTIONS,
            default="zlg",
            normalize=str.lower,
        )
        trace_file_id = _display_text(item.get("trace_file_id", "")).strip()
        source_channel_text = _display_text(item.get("source_channel", "")).strip()
        source_bus_type_text = _display_text(item.get("source_bus_type", "")).strip().upper()
        has_trace_source = bool(trace_file_id or source_channel_text or source_bus_type_text)
        if has_trace_source:
            trace_file_id = _require_text(trace_file_id, "文件")
            source_channel = _parse_int_text(source_channel_text, "源通道")
            source_bus_type = _parse_choice_text(source_bus_type_text, "源总线类型", BUS_OPTIONS, normalize=str.upper)
        else:
            source_channel = None
            source_bus_type = None
        bus_type = (
            source_bus_type
            if source_bus_type is not None
            else _parse_choice_text(
                item.get("bus_type", "CANFD"),
                "总线类型",
                BUS_OPTIONS,
                default="CANFD",
                normalize=str.upper,
            )
        )
        return {
            "trace_file_id": trace_file_id,
            "source_channel": source_channel,
            "source_bus_type": source_bus_type,
            "adapter_id": _require_text(item.get("adapter_id", ""), "适配器ID"),
            "driver": driver,
            "logical_channel": _parse_int_text(item.get("logical_channel", ""), "逻辑通道"),
            "physical_channel": _parse_int_text(item.get("physical_channel", ""), "物理通道"),
            "bus_type": bus_type,
            "device_type": _parse_device_type_text(item.get("device_type", ""), driver),
            "device_index": _parse_int_text(item.get("device_index", ""), "设备索引", allow_empty=True, default=0),
            "sdk_root": _display_text(item.get("sdk_root", "")).strip() or _default_sdk_root_for_driver(driver),
            "nominal_baud": _parse_int_text(item.get("nominal_baud", ""), "仲裁波特率", allow_empty=True, default=500000),
            "data_baud": _parse_int_text(item.get("data_baud", ""), "数据波特率", allow_empty=True, default=2000000),
            "resistance_enabled": _parse_bool_text(item.get("resistance_enabled", False), "终端电阻"),
            "listen_only": _parse_bool_text(item.get("listen_only", False), "只听"),
            "tx_echo": _parse_bool_text(item.get("tx_echo", False), "回显"),
            "merge_receive": _parse_bool_text(item.get("merge_receive", False), "合并接收"),
            "network": _parse_json_object_text(item.get("network", "{}"), "网络参数"),
            "metadata": _parse_json_object_text(item.get("metadata", "{}"), "元数据"),
        }
    except ValueError as exc:
        raise FieldValidationError(path_prefix, str(exc)) from exc


def _validate_binding_draft(item: dict, index: int) -> tuple[Optional[dict], list[ValidationIssue]]:
    prefix = f"bindings[{index}]"
    issues: list[ValidationIssue] = []
    normalized: dict[str, Any] = {}

    def capture(key: str, callback: Callable[[], Any]) -> None:
        try:
            normalized[key] = callback()
        except ValueError as exc:
            issues.append(ValidationIssue("bindings", f"{prefix}.{key}", str(exc)))

    capture("adapter_id", lambda: _require_text(item.get("adapter_id", ""), "适配器ID"))
    capture(
        "driver",
        lambda: _parse_choice_text(item.get("driver", "zlg"), "驱动", DRIVER_OPTIONS, default="zlg", normalize=str.lower),
    )
    driver = normalized.get("driver", "zlg")
    trace_file_id = _display_text(item.get("trace_file_id", "")).strip()
    source_channel_text = _display_text(item.get("source_channel", "")).strip()
    source_bus_type_text = _display_text(item.get("source_bus_type", "")).strip().upper()
    has_trace_source = bool(trace_file_id or source_channel_text or source_bus_type_text)
    if has_trace_source:
        capture("trace_file_id", lambda: _require_text(trace_file_id, "文件"))
        capture("source_channel", lambda: _parse_int_text(source_channel_text, "源通道"))
        capture(
            "source_bus_type",
            lambda: _parse_choice_text(source_bus_type_text, "源总线类型", BUS_OPTIONS, normalize=str.upper),
        )
    else:
        normalized["trace_file_id"] = ""
        normalized["source_channel"] = None
        normalized["source_bus_type"] = None
    capture("logical_channel", lambda: _parse_int_text(item.get("logical_channel", ""), "逻辑通道"))
    capture("physical_channel", lambda: _parse_int_text(item.get("physical_channel", ""), "物理通道"))
    if has_trace_source:
        normalized["bus_type"] = normalized.get("source_bus_type")
    else:
        capture(
            "bus_type",
            lambda: _parse_choice_text(item.get("bus_type", "CANFD"), "总线类型", BUS_OPTIONS, default="CANFD", normalize=str.upper),
        )
    capture("device_type", lambda: _parse_device_type_text(item.get("device_type", ""), driver))
    capture("device_index", lambda: _parse_int_text(item.get("device_index", ""), "设备索引", allow_empty=True, default=0))
    normalized["sdk_root"] = _display_text(item.get("sdk_root", "")).strip() or _default_sdk_root_for_driver(driver)
    capture("nominal_baud", lambda: _parse_int_text(item.get("nominal_baud", ""), "仲裁波特率", allow_empty=True, default=500000))
    capture("data_baud", lambda: _parse_int_text(item.get("data_baud", ""), "数据波特率", allow_empty=True, default=2000000))
    capture("resistance_enabled", lambda: _parse_bool_text(item.get("resistance_enabled", False), "终端电阻"))
    capture("listen_only", lambda: _parse_bool_text(item.get("listen_only", False), "只听"))
    capture("tx_echo", lambda: _parse_bool_text(item.get("tx_echo", False), "回显"))
    capture("merge_receive", lambda: _parse_bool_text(item.get("merge_receive", False), "合并接收"))
    capture("network", lambda: _parse_json_object_text(item.get("network", "{}"), "网络参数"))
    capture("metadata", lambda: _parse_json_object_text(item.get("metadata", "{}"), "元数据"))

    if issues:
        return None, issues
    return normalized, []


def _normalize_database_binding_item(item: dict, *, path_prefix: str = "database_bindings[0]") -> dict:
    try:
        return {
            "logical_channel": _parse_int_text(item.get("logical_channel", ""), "逻辑通道"),
            "path": _require_text(item.get("path", ""), "文件路径"),
            "format": _display_text(item.get("format", "")).strip() or "dbc",
        }
    except ValueError as exc:
        raise FieldValidationError(path_prefix, str(exc)) from exc


def _normalize_signal_override_item(item: dict, *, path_prefix: str = "signal_overrides[0]") -> dict:
    try:
        return {
            "logical_channel": _parse_int_text(item.get("logical_channel", ""), "逻辑通道"),
            "message_id_or_pgn": _parse_int_text(item.get("message_id_or_pgn", ""), "报文ID"),
            "signal_name": _require_text(item.get("signal_name", ""), "信号名"),
            "value": _parse_scalar_text(item.get("value", "")),
        }
    except ValueError as exc:
        raise FieldValidationError(path_prefix, str(exc)) from exc


def _normalize_diagnostic_target_item(item: dict, *, path_prefix: str = "diagnostic_targets[0]") -> dict:
    try:
        return {
            "name": _require_text(item.get("name", ""), "名称"),
            "transport": _parse_choice_text(
                item.get("transport", DiagnosticTransport.CAN.value),
                "传输",
                TRANSPORT_OPTIONS,
                default=DiagnosticTransport.CAN.value,
                normalize=str.upper,
            ),
            "adapter_id": _display_text(item.get("adapter_id", "")).strip(),
            "logical_channel": _parse_int_text(item.get("logical_channel", ""), "逻辑通道", allow_empty=True, default=0),
            "tx_id": _parse_int_text(item.get("tx_id", ""), "TX ID", allow_empty=True, default=0x7E0),
            "rx_id": _parse_int_text(item.get("rx_id", ""), "RX ID", allow_empty=True, default=0x7E8),
            "host": _display_text(item.get("host", "")).strip(),
            "port": _parse_int_text(item.get("port", ""), "端口", allow_empty=True, default=13400),
            "source_address": _parse_int_text(item.get("source_address", ""), "源地址", allow_empty=True, default=0x0E00),
            "target_address": _parse_int_text(item.get("target_address", ""), "目标地址", allow_empty=True, default=0x0001),
            "activation_type": _parse_int_text(item.get("activation_type", ""), "激活类型", allow_empty=True, default=0x00),
            "timeout_ms": _parse_int_text(item.get("timeout_ms", ""), "超时ms", allow_empty=True, default=1000),
            "metadata": _parse_json_object_text(item.get("metadata", "{}"), "元数据"),
        }
    except ValueError as exc:
        raise FieldValidationError(path_prefix, str(exc)) from exc


def _normalize_diagnostic_action_item(item: dict, *, path_prefix: str = "diagnostic_actions[0]") -> dict:
    try:
        return {
            "ts_ns": _parse_int_text(item.get("ts_ns", ""), "时间戳ns"),
            "target": _require_text(item.get("target", ""), "目标名称"),
            "service_id": _parse_int_text(item.get("service_id", ""), "SID"),
            "payload": _parse_hex_bytes_text(item.get("payload", ""), "Payload"),
            "transport": _parse_choice_text(
                item.get("transport", DiagnosticTransport.CAN.value),
                "传输",
                TRANSPORT_OPTIONS,
                default=DiagnosticTransport.CAN.value,
                normalize=str.upper,
            ),
            "timeout_ms": _parse_int_text(item.get("timeout_ms", ""), "超时ms", allow_empty=True, default=1000),
            "description": _display_text(item.get("description", "")).strip(),
            "metadata": _parse_json_object_text(item.get("metadata", "{}"), "元数据"),
        }
    except ValueError as exc:
        raise FieldValidationError(path_prefix, str(exc)) from exc


def _normalize_link_action_item(item: dict, *, path_prefix: str = "link_actions[0]") -> dict:
    try:
        return {
            "ts_ns": _parse_int_text(item.get("ts_ns", ""), "时间戳ns"),
            "adapter_id": _require_text(item.get("adapter_id", ""), "适配器ID"),
            "action": _parse_choice_text(
                item.get("action", LinkActionType.DISCONNECT.value),
                "动作",
                LINK_ACTION_OPTIONS,
                default=LinkActionType.DISCONNECT.value,
                normalize=str.upper,
            ),
            "logical_channel": _parse_int_text(item.get("logical_channel", ""), "逻辑通道", allow_empty=True, default=None),
            "description": _display_text(item.get("description", "")).strip(),
            "metadata": _parse_json_object_text(item.get("metadata", "{}"), "元数据"),
        }
    except ValueError as exc:
        raise FieldValidationError(path_prefix, str(exc)) from exc


def validate_scenario_draft(
    *,
    scenario_id: str,
    name: str,
    metadata_text: str,
    trace_ids: Sequence[str],
    existing_trace_ids: set[str],
    draft_bindings: Sequence[dict],
    database_binding_items: Sequence[dict],
    database_binding_drafts: Mapping[int, dict],
    database_binding_duplicate_counts: Mapping[int, int],
    collection_data: Mapping[str, Sequence[dict]],
    trace_source_summaries: Callable[[str], Sequence[Mapping[str, Any]]],
) -> DraftValidationResult:
    issues: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    normalized_bindings: list[tuple[int, dict]] = []
    normalized_database_bindings: list[dict] = []
    for index, item in enumerate(draft_bindings):
        normalized_item, item_issues = _validate_binding_draft(item, index)
        if item_issues:
            issues.extend(item_issues)
            continue
        normalized_bindings.append((index, normalized_item or {}))

    normalized_collections = _normalize_collection_items(collection_data, issues)

    for index, item in enumerate(database_binding_items):
        try:
            normalized_database_bindings.append(
                _normalize_database_binding_item(item, path_prefix=f"database_bindings[{index}]")
            )
        except FieldValidationError as exc:
            issues.append(ValidationIssue("bindings", exc.path, str(exc)))

    try:
        metadata = _parse_json_object_text(metadata_text, "场景元数据")
    except ValueError as exc:
        issues.append(ValidationIssue("metadata", "metadata", str(exc)))
        metadata = {}

    missing_trace_ids = [trace_id for trace_id in trace_ids if trace_id not in existing_trace_ids]
    if missing_trace_ids:
        warnings.append(
            ValidationIssue(
                "traces",
                "trace_file_ids",
                f"当前场景仍引用 {len(missing_trace_ids)} 个缺失文件。",
            )
        )

    _validate_trace_mappings(
        normalized_bindings,
        trace_ids,
        existing_trace_ids,
        trace_source_summaries,
        issues,
    )
    _append_binding_warnings(normalized_bindings, warnings)
    _append_database_binding_warnings(
        database_binding_duplicate_counts,
        database_binding_drafts,
        normalized_bindings,
        warnings,
    )

    if issues:
        return DraftValidationResult(None, issues, warnings)

    payload = {
        "scenario_id": scenario_id.strip() or uuid.uuid4().hex,
        "name": name.strip() or "新场景",
        "trace_file_ids": list(trace_ids),
        "bindings": [binding for _, binding in normalized_bindings],
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


def _normalize_collection_items(
    collection_data: Mapping[str, Sequence[dict]],
    issues: list[ValidationIssue],
) -> dict[str, list[dict]]:
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
    for key, items in collection_data.items():
        normalizer = collection_normalizers.get(key)
        if normalizer is None:
            continue
        for index, item in enumerate(items):
            try:
                normalized_collections[key].append(normalizer(item, path_prefix=f"{key}[{index}]"))
            except FieldValidationError as exc:
                issues.append(ValidationIssue(key, exc.path, str(exc)))
    return normalized_collections


def _validate_trace_mappings(
    normalized_bindings: Sequence[tuple[int, dict]],
    trace_ids: Sequence[str],
    existing_trace_ids: set[str],
    trace_source_summaries: Callable[[str], Sequence[Mapping[str, Any]]],
    issues: list[ValidationIssue],
) -> None:
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
            summaries = trace_source_summaries(trace_file_id)
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

    if has_file_mapping:
        missing_mapped_trace_ids = sorted(set(trace_ids) - file_mapped_trace_ids)
        if missing_mapped_trace_ids:
            issues.append(ValidationIssue("traces", "trace_file_ids", "已勾选的场景文件必须全部完成文件映射。"))


def _append_binding_warnings(
    normalized_bindings: Sequence[tuple[int, dict]],
    warnings: list[ValidationIssue],
) -> None:
    for binding_index, binding in normalized_bindings:
        warning = _binding_device_type_warning(binding, binding_index)
        if warning is not None:
            warnings.append(warning)


def _append_database_binding_warnings(
    duplicate_counts: Mapping[int, int],
    database_binding_drafts: Mapping[int, dict],
    normalized_bindings: Sequence[tuple[int, dict]],
    warnings: list[ValidationIssue],
) -> None:
    for logical_channel, duplicate_count in sorted(duplicate_counts.items()):
        warnings.append(
            ValidationIssue(
                "bindings",
                f"database_bindings[{logical_channel}]",
                f"LC{logical_channel} 存在 {duplicate_count} 条DBC绑定，编辑器已按最后一条展示，保存时会去重。",
            )
        )

    orphan_database_bindings = _database_binding_orphan_items(
        database_binding_drafts,
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


def _database_binding_file_name(item: Optional[dict]) -> str:
    if not item:
        return "未绑定DBC"
    path = _display_text(item.get("path", "")).strip()
    if not path:
        return "未绑定DBC"
    return Path(path).name or path


__all__ = (
    "DRIVER_OPTIONS",
    "BUS_OPTIONS",
    "TRANSPORT_OPTIONS",
    "LINK_ACTION_OPTIONS",
    "ZLG_DEVICE_TYPE_OPTIONS",
    "DRIVER_DEVICE_TYPE_OPTIONS",
    "LEGACY_ZLG_DEVICE_TYPES",
    "ValidationIssue",
    "DraftValidationResult",
    "FieldValidationError",
    "_clone_jsonable",
    "_display_text",
    "_normalize_driver_name",
    "_binding_device_type_options",
    "_binding_device_type_placeholder",
    "_parse_device_type_text",
    "_binding_warning_subject",
    "_binding_device_type_warning",
    "_format_json_text",
    "_format_field_value",
    "_parse_int_text",
    "_parse_bool_text",
    "_parse_json_object_text",
    "_parse_choice_text",
    "_require_text",
    "_parse_scalar_text",
    "_parse_hex_bytes_text",
    "_normalize_scenario_payload",
    "_scenario_payload_is_dirty",
    "_parse_optional_int_text",
    "_binding_uses_trace_source",
    "_database_binding_map_from_items",
    "_database_binding_items_from_map",
    "_database_binding_orphan_items",
    "_new_binding_draft",
    "_default_sdk_root_for_driver",
    "_binding_draft_from_item",
    "_normalize_binding_item",
    "_validate_binding_draft",
    "_normalize_database_binding_item",
    "_normalize_signal_override_item",
    "_normalize_diagnostic_target_item",
    "_normalize_diagnostic_action_item",
    "_normalize_link_action_item",
    "_database_binding_file_name",
    "validate_scenario_draft",
)
