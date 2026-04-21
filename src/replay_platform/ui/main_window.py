from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from replay_platform.app_controller import (
    DEBUG_LOG_FRAME_SAMPLE_RATE,
    LOG_LEVEL_PRESET_DEBUG_ALL,
    LOG_LEVEL_PRESET_DEBUG_SAMPLED,
    LOG_LEVEL_PRESET_INFO,
    LOG_LEVEL_PRESET_OPTIONS,
    LOG_LEVEL_PRESET_WARNING,
    ReplayApplication,
    ReplayPreparation,
)
from replay_platform.core import (
    BusType,
    DatabaseBinding,
    DiagnosticTransport,
    FrameEnableRule,
    LinkActionType,
    ReplayLaunchSource,
    ReplayRuntimeSnapshot,
    ReplayState,
    ReplayStats,
    ScenarioSpec,
    SignalOverride,
    TimelineKind,
    TraceFileRecord,
)
from replay_platform.services.signal_catalog import MessageCatalogEntry, SignalCatalogEntry


USER_ROLE = 32
DRIVER_OPTIONS = ("zlg", "mock", "tongxing")
BUS_OPTIONS = tuple(item.value for item in BusType)
TRANSPORT_OPTIONS = tuple(item.value for item in DiagnosticTransport)
LINK_ACTION_OPTIONS = tuple(item.value for item in LinkActionType)
FRAME_ENABLE_STATUS_OPTIONS = ("启用", "禁用")
LOG_LEVEL_OPTION_LABELS = {
    LOG_LEVEL_PRESET_WARNING: "仅警告",
    LOG_LEVEL_PRESET_INFO: "信息及以上",
    LOG_LEVEL_PRESET_DEBUG_SAMPLED: "调试（帧采样）",
    LOG_LEVEL_PRESET_DEBUG_ALL: "调试（逐帧）",
}
LOG_LEVEL_OPTIONS = tuple(LOG_LEVEL_OPTION_LABELS[preset] for preset in LOG_LEVEL_PRESET_OPTIONS)
LOG_LEVEL_DEFAULT_HINT = "仅影响之后新产生的日志，已有内容不会重新过滤"
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


@dataclass(frozen=True)
class EditorFieldSpec:
    key: str
    label: str
    kind: str = "text"
    options: tuple[Any, ...] = ()


@dataclass(frozen=True)
class ScenarioLaunchAssessment:
    ready: bool
    badge_text: str
    tone: str
    source_text: str
    detail_text: str
    issue_text: str = ""


@dataclass(frozen=True)
class PlaybackButtonState:
    start_enabled: bool
    pause_enabled: bool
    resume_enabled: bool
    stop_enabled: bool


@dataclass(frozen=True)
class ScenarioBusinessSummary:
    trace_text: str
    binding_text: str
    database_text: str


@dataclass(frozen=True)
class RuntimeVisibilitySummary:
    progress_text: str
    source_text: str
    device_text: str
    launch_text: str


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


def _format_table_value(value: Any) -> str:
    return _display_text(value)


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


def _plan_log_refresh(cursor: int, base_index: int, entry_count: int) -> tuple[str, int]:
    if cursor < base_index:
        return "reset", 0
    offset = min(max(cursor - base_index, 0), entry_count)
    return "append", offset


def _log_level_option(preset: str) -> str:
    return LOG_LEVEL_OPTION_LABELS[preset]


def _parse_log_level_option(option: str) -> str:
    for preset, label in LOG_LEVEL_OPTION_LABELS.items():
        if label == option:
            return preset
    raise ValueError(f"未知日志级别选项：{option}")


def _build_log_level_hint(preset: str) -> str:
    if preset == LOG_LEVEL_PRESET_DEBUG_SAMPLED:
        return f"调试模式会按每 {DEBUG_LOG_FRAME_SAMPLE_RATE} 帧采样追加帧明细"
    if preset == LOG_LEVEL_PRESET_DEBUG_ALL:
        return "调试模式会逐帧追加帧明细，日志量可能很大"
    return LOG_LEVEL_DEFAULT_HINT


def _normalize_scenario_payload(payload: dict) -> dict:
    return ScenarioSpec.from_dict(payload).to_dict()


def _scenario_payload_is_dirty(current_payload: Optional[dict], last_saved_payload: Optional[dict]) -> bool:
    if current_payload is None:
        return False
    if last_saved_payload is None:
        return True
    return _normalize_scenario_payload(current_payload) != _normalize_scenario_payload(last_saved_payload)


def _build_json_preview(last_valid_payload: Optional[dict], error_count: int) -> tuple[str, str]:
    payload = last_valid_payload or {}
    if error_count:
        return f"当前表单存在 {error_count} 个错误，预览未更新。", _format_json_text(payload)
    return "JSON 预览与当前最近一次有效草稿一致。", _format_json_text(payload)


def _format_trace_preview(names: list[str], *, max_items: int = 3) -> str:
    if not names:
        return "无"
    preview = "，".join(names[:max_items])
    if len(names) > max_items:
        preview += " ..."
    return preview


def _build_scenario_counts_summary(payload: dict) -> str:
    return (
        "文件 {files} 个 | 绑定 {bindings} 条 | 诊断目标 {targets} 个 | 诊断动作 {actions} 条 | 链路动作 {links} 条"
    ).format(
        files=len(payload.get("trace_file_ids", [])),
        bindings=len(payload.get("bindings", [])),
        targets=len(payload.get("diagnostic_targets", [])),
        actions=len(payload.get("diagnostic_actions", [])),
        links=len(payload.get("link_actions", [])),
    )


def _assess_scenario_launch(payload: Optional[dict], selected_trace_ids: list[str]) -> ScenarioLaunchAssessment:
    if payload is None:
        return ScenarioLaunchAssessment(
            ready=False,
            badge_text="未就绪",
            tone="warn",
            source_text="启动来源：未选择场景。",
            detail_text="请先选择或创建场景。",
            issue_text="当前没有可用场景。",
        )
    try:
        scenario = ScenarioSpec.from_dict(payload)
    except Exception as exc:
        return ScenarioLaunchAssessment(
            ready=False,
            badge_text="场景错误",
            tone="error",
            source_text="启动来源：当前场景无法解析。",
            detail_text=f"场景校验失败：{exc}",
            issue_text="当前场景存在结构错误，无法启动回放。",
        )

    issues: list[str] = []
    trace_bound_bindings = [binding for binding in scenario.bindings if binding.uses_trace_source()]
    trace_bound_trace_ids = {binding.trace_file_id for binding in trace_bound_bindings}
    if scenario.trace_file_ids:
        source_text = "启动来源：将使用场景内已绑定文件启动。"
        detail_text = "场景已绑定回放文件。"
        if trace_bound_bindings:
            missing_trace_mappings = sorted(set(scenario.trace_file_ids) - trace_bound_trace_ids)
            if missing_trace_mappings:
                issues.append("已勾选文件尚未完成文件映射。")
    elif selected_trace_ids:
        if trace_bound_bindings:
            source_text = "启动来源：文件映射场景必须使用场景内绑定文件。"
            detail_text = "当前存在文件映射，但场景未勾选回放文件。"
            issues.append("文件映射场景不能回退到主窗口选中文件。")
        else:
            source_text = "启动来源：将使用主窗口当前选中的文件启动。"
            detail_text = "将回退到主窗口当前选中的文件。"
    else:
        source_text = "启动来源：未找到可回放文件。"
        detail_text = "请在场景中绑定回放文件，或在左侧“回放文件”页签中至少选中一个文件。"
        issues.append("缺少可回放文件。")

    if not scenario.bindings:
        issues.append("场景未配置任何通道绑定。")

    if issues:
        return ScenarioLaunchAssessment(
            ready=False,
            badge_text="未就绪",
            tone="warn",
            source_text=source_text,
            detail_text=detail_text,
            issue_text="；".join(issues),
        )
    return ScenarioLaunchAssessment(
        ready=True,
        badge_text="已就绪",
        tone="good",
        source_text=source_text,
        detail_text=detail_text,
    )


def _playback_button_state(state: ReplayState | str, ready: bool) -> PlaybackButtonState:
    state_value = state.value if isinstance(state, ReplayState) else str(state)
    if state_value == ReplayState.RUNNING.value:
        return PlaybackButtonState(False, True, False, True)
    if state_value == ReplayState.PAUSED.value:
        return PlaybackButtonState(False, False, True, True)
    if state_value == ReplayState.STOPPED.value and ready:
        return PlaybackButtonState(True, False, False, False)
    return PlaybackButtonState(False, False, False, False)


def _format_replay_stats(stats: ReplayStats, snapshot: ReplayRuntimeSnapshot) -> str:
    loop_text = ""
    if snapshot.loop_enabled:
        if snapshot.state in {ReplayState.RUNNING, ReplayState.PAUSED}:
            loop_text = f"循环回放：当前第 {snapshot.completed_loops + 1} 圈 / 已完成 {snapshot.completed_loops} 圈 | "
        else:
            loop_text = f"循环回放：已完成 {snapshot.completed_loops} 圈 | "
    return (
        "{loop}已发帧 {sent} | 跳过帧 {skipped} | 诊断动作 {diagnostic} | 链路动作 {links} | 错误 {errors}"
    ).format(
        loop=loop_text,
        sent=stats.sent_frames,
        skipped=stats.skipped_frames,
        diagnostic=stats.diagnostic_actions,
        links=stats.link_actions,
        errors=len(stats.errors),
    )


def _format_duration_ns(duration_ns: int) -> str:
    if duration_ns >= 1_000_000_000:
        return f"{duration_ns / 1_000_000_000:.3f} s"
    return f"{duration_ns / 1_000_000:.3f} ms"


def _trace_display_name(trace_id: str, trace_lookup: dict[str, TraceFileRecord]) -> str:
    record = trace_lookup.get(trace_id)
    if record is None:
        return f"缺失文件（{trace_id}）"
    return record.name


def _build_scenario_business_summary(
    payload: Optional[dict],
    trace_lookup: dict[str, TraceFileRecord],
    database_status_by_channel: Optional[dict[int, dict[str, Any]]] = None,
) -> ScenarioBusinessSummary:
    if payload is None:
        return ScenarioBusinessSummary("回放文件：无", "通道绑定：无", "数据库绑定：无")
    try:
        scenario = ScenarioSpec.from_dict(payload)
    except Exception:
        return ScenarioBusinessSummary("回放文件：无", "通道绑定：无", "数据库绑定：无")

    trace_names = [_trace_display_name(trace_id, trace_lookup) for trace_id in scenario.trace_file_ids]
    if trace_names:
        trace_text = f"回放文件：{_format_trace_preview(trace_names)}"
    else:
        trace_text = "回放文件：场景未绑定文件"

    label_map = _binding_label_map(scenario.bindings, trace_lookup)
    if scenario.bindings:
        binding_text = "通道绑定：" + "；".join(
            f"{_binding_source_label(binding, trace_lookup)} -> {binding.adapter_id}/PC{binding.physical_channel} {binding.bus_type.value}"
            for binding in scenario.bindings
        )
    else:
        binding_text = "通道绑定：未配置通道绑定"

    database_map = {binding.logical_channel: binding for binding in scenario.database_bindings}
    database_parts = [
        _database_binding_text(
            binding.logical_channel,
            database_map.get(binding.logical_channel),
            label_map,
            database_status_by_channel or {},
        )
        for binding in scenario.bindings
    ]
    orphan_channels = sorted(set(database_map) - {binding.logical_channel for binding in scenario.bindings})
    database_parts.extend(
        _database_binding_text(channel, database_map[channel], label_map, database_status_by_channel or {})
        for channel in orphan_channels
    )
    if not database_parts:
        database_text = "数据库绑定：未配置数据库"
    else:
        database_text = "数据库绑定：" + "；".join(database_parts)
    return ScenarioBusinessSummary(trace_text, binding_text, database_text)


def _database_binding_text(
    logical_channel: int,
    binding: DatabaseBinding | None,
    label_map: dict[int, str],
    database_status_by_channel: dict[int, dict[str, Any]],
) -> str:
    channel_text = _logical_channel_label(logical_channel, label_map)
    if binding is None:
        return f"{channel_text} -> 未配置数据库"
    path_text = Path(binding.path).name or binding.path
    status = database_status_by_channel.get(logical_channel)
    if not status:
        return f"{channel_text} -> {path_text}"
    if status.get("loaded"):
        message_count = int(status.get("message_count", 0))
        return f"{channel_text} -> {path_text}（已加载，{message_count} 个报文）"
    error_text = _display_text(status.get("error", "")).strip() or "未知错误"
    return f"{channel_text} -> {path_text}（加载失败：{error_text}）"


def _build_override_catalog_status_text(
    database_status_by_channel: dict[int, dict[str, Any]],
    *,
    label_map: Optional[dict[int, str]] = None,
) -> str:
    if not database_status_by_channel:
        return "数据库状态：当前场景未配置数据库。"
    parts: list[str] = []
    resolved_label_map = label_map or {}
    for logical_channel, status in sorted(database_status_by_channel.items()):
        channel_text = _logical_channel_label(logical_channel, resolved_label_map)
        if status.get("loaded"):
            parts.append(f"{channel_text} 已加载 {int(status.get('message_count', 0))} 个报文")
            continue
        error_text = _display_text(status.get("error", "")).strip() or "未知错误"
        parts.append(f"{channel_text} 加载失败：{error_text}")
    return "数据库状态：" + "；".join(parts)


def _filter_trace_records(records: list[TraceFileRecord], query: str) -> list[TraceFileRecord]:
    normalized = query.strip().casefold()
    if not normalized:
        return list(records)
    return [record for record in records if normalized in record.name.casefold()]


def _filter_scenarios(records: list[ScenarioSpec], query: str) -> list[ScenarioSpec]:
    normalized = query.strip().casefold()
    if not normalized:
        return list(records)
    return [record for record in records if normalized in record.name.casefold()]


def _build_trace_selection_summary(records: list[TraceFileRecord]) -> str:
    if not records:
        return "当前未选中文件；当前场景未绑定文件时会回退到这里的选中文件。"
    names = _format_trace_preview([record.name for record in records])
    total_frames = sum(record.event_count for record in records)
    start_ns = min((record.start_ns for record in records), default=0)
    end_ns = max((record.end_ns for record in records), default=0)
    return (
        f"已选 {len(records)} 个文件 | {names} | 累计 {total_frames} 帧 | 时间跨度 {_format_duration_ns(max(end_ns - start_ns, 0))}"
    )


def _normalize_trace_message_id_summary_item(raw_item: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw_item, dict):
        return None
    source_channel = _parse_optional_int_text(raw_item.get("source_channel"))
    bus_type = _display_text(raw_item.get("bus_type", "")).strip().upper()
    if source_channel is None or not bus_type:
        return None
    message_ids: list[int] = []
    for raw_message_id in raw_item.get("message_ids", []):
        parsed_message_id = _parse_optional_int_text(raw_message_id)
        if parsed_message_id is None:
            continue
        message_ids.append(parsed_message_id)
    return {
        "source_channel": source_channel,
        "bus_type": bus_type,
        "message_ids": sorted(set(message_ids)),
    }


def _build_frame_enable_candidate_ids_from_trace_summaries(
    trace_ids: Sequence[str],
    bindings: Sequence[dict[str, Any]],
    summary_lookup: dict[str, Sequence[dict[str, Any]]],
) -> dict[int, list[int]]:
    candidates: dict[int, set[int]] = {}
    bindings_by_trace_id: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        trace_file_id = _display_text(binding.get("trace_file_id", "")).strip()
        source_channel = _parse_optional_int_text(binding.get("source_channel"))
        source_bus_type = _display_text(binding.get("source_bus_type", "")).strip().upper()
        logical_channel = _parse_optional_int_text(binding.get("logical_channel"))
        if not trace_file_id or source_channel is None or not source_bus_type or logical_channel is None:
            continue
        bindings_by_trace_id.setdefault(trace_file_id, []).append(
            {
                "logical_channel": logical_channel,
                "source_channel": source_channel,
                "source_bus_type": source_bus_type,
            }
        )

    for trace_id in trace_ids:
        normalized_summaries = [
            normalized
            for normalized in (
                _normalize_trace_message_id_summary_item(raw_item)
                for raw_item in summary_lookup.get(trace_id, [])
            )
            if normalized is not None
        ]
        mapped_bindings = bindings_by_trace_id.get(trace_id, [])
        if mapped_bindings:
            for binding in mapped_bindings:
                matching_summary = next(
                    (
                        summary
                        for summary in normalized_summaries
                        if summary["source_channel"] == binding["source_channel"]
                        and summary["bus_type"] == binding["source_bus_type"]
                    ),
                    None,
                )
                if matching_summary is None:
                    continue
                candidates.setdefault(binding["logical_channel"], set()).update(matching_summary["message_ids"])
            continue
        for summary in normalized_summaries:
            candidates.setdefault(summary["source_channel"], set()).update(summary["message_ids"])
    return {
        logical_channel: sorted(message_ids)
        for logical_channel, message_ids in candidates.items()
    }


def _build_scenario_selection_summary(record: Optional[ScenarioSpec]) -> str:
    if record is None:
        return "当前未选中场景。"
    return (
        f"{record.name} | 文件 {len(record.trace_file_ids)} | 绑定 {len(record.bindings)} | "
        f"数据库 {len(record.database_bindings)} | 诊断动作 {len(record.diagnostic_actions)}"
    )


def _build_trace_delete_summary(record: TraceFileRecord, referencing_scenarios: list[ScenarioSpec]) -> str:
    lines = [
        f"文件：{record.name}",
        f"帧数：{record.event_count}",
        f"时间跨度：{_format_duration_ns(max(record.end_ns - record.start_ns, 0))}",
    ]
    if referencing_scenarios:
        scenario_names: list[str] = []
        for scenario in referencing_scenarios:
            if scenario.name not in scenario_names:
                scenario_names.append(scenario.name)
        lines.append(f"仍被 {len(scenario_names)} 个场景引用：{_format_trace_preview(scenario_names)}")
        lines.append("删除后这些场景会显示缺失文件警告。")
    else:
        lines.append("该文件当前未被已保存场景引用。")
    return "\n".join(lines)


def _build_scenario_delete_summary(record: ScenarioSpec) -> str:
    return "\n".join(
        [
            f"场景：{record.name}",
            _build_scenario_selection_summary(record),
        ]
    )


def _should_reset_current_scenario_after_delete(current_payload: Optional[dict], deleted_scenario_id: str) -> bool:
    if current_payload is None:
        return False
    current_id = _display_text(current_payload.get("scenario_id", "")).strip()
    deleted_id = _display_text(deleted_scenario_id).strip()
    return bool(current_id and deleted_id and current_id == deleted_id)


def _format_launch_source(source: Optional[ReplayLaunchSource]) -> str:
    if source == ReplayLaunchSource.SCENARIO_BOUND:
        return "场景绑定文件"
    if source == ReplayLaunchSource.SELECTED_FALLBACK:
        return "主窗口选中文件回退"
    return "未开始回放"


def _build_device_status_text(
    snapshot: ReplayRuntimeSnapshot,
    bindings: list[Any],
    trace_lookup: Optional[dict[str, TraceFileRecord]] = None,
) -> str:
    label_map = _binding_label_map(bindings, trace_lookup or {})
    adapter_ids: list[str] = []
    for binding in bindings:
        adapter_id = _display_text(binding.get("adapter_id", "")).strip()
        if adapter_id and adapter_id not in adapter_ids:
            adapter_ids.append(adapter_id)
    if not adapter_ids:
        return "设备状态：未配置适配器"
    if snapshot.state == ReplayState.STOPPED:
        return "设备状态：" + "；".join(f"{adapter_id} 未启动" for adapter_id in adapter_ids)

    parts: list[str] = []
    for adapter_id in adapter_ids:
        health = snapshot.adapter_health.get(adapter_id)
        adapter_bindings = [binding for binding in bindings if binding.get("adapter_id") == adapter_id]
        if health is None:
            parts.append(f"{adapter_id} 未知")
            continue
        channel_parts: list[str] = []
        for binding in adapter_bindings:
            physical_channel = binding.get("physical_channel")
            logical_channel = binding.get("logical_channel")
            channel_online = health.per_channel.get(int(physical_channel)) if physical_channel is not None else None
            if channel_online is None:
                continue
            channel_parts.append(f"{_logical_channel_label(logical_channel, label_map)} {'正常' if channel_online else '离线'}")
        state_text = "在线" if health.online else "离线"
        detail_text = f"，{' / '.join(channel_parts)}" if channel_parts else (f"（{health.detail}）" if health.detail else "")
        parts.append(f"{adapter_id} {state_text}{detail_text}")
    return "设备状态：" + "；".join(parts)


def _build_runtime_visibility_summary(
    snapshot: ReplayRuntimeSnapshot,
    bindings: list[Any],
    trace_lookup: Optional[dict[str, TraceFileRecord]] = None,
) -> RuntimeVisibilitySummary:
    total_ns = max(snapshot.total_ts_ns, 0)
    progress = 0.0
    if total_ns > 0:
        progress = min(max(snapshot.current_ts_ns / total_ns, 0.0), 1.0)
    elif snapshot.timeline_size == 0:
        progress = 0.0
    progress_text = (
        f"进度 {progress * 100:.1f}% | 当前时间 {snapshot.current_ts_ns / 1_000_000:.3f} ms / "
        f"{total_ns / 1_000_000:.3f} ms"
    )
    source_name = snapshot.current_source_file or "未开始"
    source_text = f"当前来源：{source_name}"
    device_text = _build_device_status_text(snapshot, bindings, trace_lookup)
    launch_text = f"启动来源：{_format_launch_source(snapshot.launch_source)}"
    return RuntimeVisibilitySummary(progress_text, source_text, device_text, launch_text)


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


def _trace_record_name(trace_id: str, trace_lookup: dict[str, TraceFileRecord]) -> str:
    record = trace_lookup.get(trace_id)
    if record is None:
        return f"缺失文件({trace_id})"
    return record.name


def _binding_source_label(item: dict | Any, trace_lookup: dict[str, TraceFileRecord]) -> str:
    if not _binding_uses_trace_source(item):
        logical_channel = getattr(item, "logical_channel", None) if hasattr(item, "logical_channel") else item.get("logical_channel")
        logical_text = _display_text(logical_channel).strip() or "?"
        return f"LC{logical_text}（旧映射）"
    if hasattr(item, "trace_file_id"):
        trace_file_id = _display_text(getattr(item, "trace_file_id", "")).strip()
        source_channel = getattr(item, "source_channel", None)
        source_bus_type = getattr(item, "source_bus_type", None)
        bus_text = source_bus_type.value if isinstance(source_bus_type, BusType) else _display_text(source_bus_type).strip().upper()
    else:
        trace_file_id = _display_text(item.get("trace_file_id", "")).strip()
        source_channel = _parse_optional_int_text(item.get("source_channel"))
        bus_text = _display_text(item.get("source_bus_type", "")).strip().upper()
    return f"{_trace_record_name(trace_file_id, trace_lookup)} | CH{source_channel} | {bus_text or '?'}"


def _binding_label_map(bindings: list[Any], trace_lookup: dict[str, TraceFileRecord]) -> dict[int, str]:
    labels: dict[int, str] = {}
    for binding in bindings:
        logical_channel = getattr(binding, "logical_channel", None) if hasattr(binding, "logical_channel") else binding.get("logical_channel")
        try:
            channel_key = int(logical_channel)
        except (TypeError, ValueError):
            continue
        labels[channel_key] = _binding_source_label(binding, trace_lookup)
    return labels


def _logical_channel_label(logical_channel: Any, label_map: dict[int, str]) -> str:
    try:
        channel_key = int(logical_channel)
    except (TypeError, ValueError):
        return f"LC{_display_text(logical_channel).strip() or '?'}（旧映射/缺失）"
    return label_map.get(channel_key, f"LC{channel_key}（旧映射/缺失）")


def _binding_summary(item: dict, trace_lookup: Optional[dict[str, TraceFileRecord]] = None) -> str:
    adapter_id = _display_text(item.get("adapter_id", "")).strip() or "未命名适配器"
    driver = _display_text(item.get("driver", "")).strip().lower() or "?"
    physical_channel = _display_text(item.get("physical_channel", "")).strip() or "?"
    bus_type = _display_text(item.get("bus_type", "")).strip().upper() or "?"
    device_type = _display_text(item.get("device_type", "")).strip() or "?"
    if trace_lookup:
        source_label = _binding_source_label(item, trace_lookup)
        return f"{source_label} -> {adapter_id}/PC{physical_channel} | {driver} | {bus_type}/{device_type}"
    logical_channel = _display_text(item.get("logical_channel", "")).strip() or "?"
    return f"{adapter_id} | {driver} | LC{logical_channel}->PC{physical_channel} | {bus_type}/{device_type}"


def _database_binding_summary(item: dict, label_map: Optional[dict[int, str]] = None) -> str:
    logical_channel = _display_text(item.get("logical_channel", "")).strip() or "?"
    path = _display_text(item.get("path", "")).strip() or "未选择文件"
    fmt = _display_text(item.get("format", "")).strip() or "dbc"
    channel_text = _logical_channel_label(logical_channel, label_map or {}) if label_map else f"LC{logical_channel}"
    return f"{channel_text} | {fmt} | {path}"


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


def _database_binding_items_from_map(binding_map: dict[int, dict]) -> list[dict]:
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


def _database_binding_orphan_items(binding_map: dict[int, dict], bindings: Sequence[dict]) -> list[dict]:
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


def _database_binding_file_name(item: Optional[dict]) -> str:
    if not item:
        return "未绑定DBC"
    path = _display_text(item.get("path", "")).strip()
    if not path:
        return "未绑定DBC"
    return Path(path).name or path


def _database_binding_status_summary(item: Optional[dict], status: Optional[dict]) -> str:
    file_name = _database_binding_file_name(item)
    if not item or not _display_text(item.get("path", "")).strip():
        return file_name
    if not status:
        return file_name
    if status.get("loaded"):
        return f"{file_name}（已加载）"
    return f"{file_name}（加载失败）"


def _database_binding_status_detail(item: Optional[dict], status: Optional[dict]) -> str:
    if not item or not _display_text(item.get("path", "")).strip():
        return "状态：当前逻辑通道未绑定DBC。"
    file_name = _database_binding_file_name(item)
    if not status:
        return f"状态：已选择 {file_name}，尚未执行加载预览。"
    if status.get("loaded"):
        message_count = int(status.get("message_count", 0))
        return f"状态：{file_name} 已加载，可用 {message_count} 个报文。"
    error_text = _display_text(status.get("error", "")).strip() or "未知错误"
    return f"状态：{file_name} 加载失败：{error_text}"


def _resource_mapping_summary(
    item: dict,
    trace_lookup: dict[str, TraceFileRecord],
    *,
    database_binding: Optional[dict] = None,
    database_status: Optional[dict] = None,
) -> str:
    source_label = _binding_source_label(item, trace_lookup)
    logical_channel = _display_text(item.get("logical_channel", "")).strip() or "?"
    adapter_id = _display_text(item.get("adapter_id", "")).strip() or "未命名适配器"
    physical_channel = _display_text(item.get("physical_channel", "")).strip() or "?"
    dbc_text = _database_binding_status_summary(database_binding, database_status)
    return f"{source_label} -> LC{logical_channel} -> {adapter_id}/PC{physical_channel} | {dbc_text}"


def _trace_mapping_completion_text(trace_id: str, bindings: Sequence[dict]) -> str:
    mapping_count = sum(1 for item in bindings if _display_text(item.get("trace_file_id", "")).strip() == trace_id)
    if mapping_count <= 0:
        return "未映射"
    return f"已映射 {mapping_count} 条源项"


def _build_orphan_database_binding_text(orphan_items: Sequence[dict], label_map: Optional[dict[int, str]] = None) -> str:
    if not orphan_items:
        return "当前没有孤立DBC绑定。"
    parts = [_database_binding_summary(item, label_map) for item in orphan_items]
    return "孤立DBC绑定：" + "；".join(parts)


def _signal_override_summary(item: dict, label_map: Optional[dict[int, str]] = None) -> str:
    logical_channel = _display_text(item.get("logical_channel", "")).strip() or "?"
    message_id = _display_text(item.get("message_id_or_pgn", "")).strip() or "?"
    signal_name = _display_text(item.get("signal_name", "")).strip() or "未命名信号"
    value = _display_text(item.get("value", "")).strip() or "空值"
    channel_text = _logical_channel_label(logical_channel, label_map or {}) if label_map else f"LC{logical_channel}"
    return f"{channel_text} | {message_id} | {signal_name} = {value}"


def _format_override_message_option(entry: MessageCatalogEntry) -> str:
    message_name = _display_text(entry.message_name).strip()
    if not message_name:
        return hex(entry.message_id)
    return f"{hex(entry.message_id)} | {message_name}"


def _parse_message_combo_text(raw: str) -> Optional[int]:
    text = raw.strip()
    if not text:
        return None
    candidate = text.split("|", 1)[0].strip()
    try:
        return int(candidate, 0)
    except ValueError:
        return None


def _build_signal_catalog_hint(entry: Optional[SignalCatalogEntry]) -> str:
    if entry is None:
        return "信号说明：选择数据库信号后会显示单位、范围和枚举值。"
    parts: list[str] = [f"信号说明：{entry.signal_name}"]
    if entry.unit:
        parts.append(f"单位 {entry.unit}")
    if entry.minimum is not None or entry.maximum is not None:
        minimum = _display_text(entry.minimum) if entry.minimum is not None else "-inf"
        maximum = _display_text(entry.maximum) if entry.maximum is not None else "+inf"
        parts.append(f"范围 {minimum} ~ {maximum}")
    if entry.choices:
        choice_text = ", ".join(f"{key}={value}" for key, value in sorted(entry.choices.items()))
        parts.append(f"枚举 {choice_text}")
    return " | ".join(parts)


def _signal_override_payload_items(overrides: Sequence[SignalOverride]) -> list[dict]:
    return [
        {
            "logical_channel": item.logical_channel,
            "message_id_or_pgn": item.message_id_or_pgn,
            "signal_name": item.signal_name,
            "value": item.value,
        }
        for item in overrides
    ]


def _frame_enable_status_text(enabled: bool) -> str:
    return "启用" if enabled else "禁用"


def _frame_enable_rule_summary(rule: FrameEnableRule) -> str:
    return f"LC{rule.logical_channel} | {hex(rule.message_id)} | {_frame_enable_status_text(rule.enabled)}"


def _diagnostic_target_summary(item: dict, label_map: Optional[dict[int, str]] = None) -> str:
    name = _display_text(item.get("name", "")).strip() or "未命名目标"
    transport = _display_text(item.get("transport", "")).strip().upper() or "?"
    if transport == DiagnosticTransport.DOIP.value:
        host = _display_text(item.get("host", "")).strip() or "未配置主机"
        port = _display_text(item.get("port", "")).strip() or "?"
        return f"{name} | DOIP | {host}:{port}"
    logical_channel = _display_text(item.get("logical_channel", "")).strip() or "?"
    tx_id = _format_field_value(item.get("tx_id", ""), "hex-int") or "?"
    rx_id = _format_field_value(item.get("rx_id", ""), "hex-int") or "?"
    channel_text = _logical_channel_label(logical_channel, label_map or {}) if label_map else f"LC{logical_channel}"
    return f"{name} | CAN | {channel_text} | TX {tx_id} / RX {rx_id}"


def _diagnostic_action_summary(item: dict) -> str:
    ts_ns = _display_text(item.get("ts_ns", "")).strip() or "?"
    target = _display_text(item.get("target", "")).strip() or "未命名目标"
    sid = _format_field_value(item.get("service_id", ""), "hex-int") or "?"
    transport = _display_text(item.get("transport", "")).strip().upper() or "?"
    return f"{ts_ns} ns | {target} | SID {sid} | {transport}"


def _link_action_summary(item: dict, label_map: Optional[dict[int, str]] = None) -> str:
    ts_ns = _display_text(item.get("ts_ns", "")).strip() or "?"
    adapter_id = _display_text(item.get("adapter_id", "")).strip() or "未命名适配器"
    action = _display_text(item.get("action", "")).strip().upper() or "?"
    logical_channel = _display_text(item.get("logical_channel", "")).strip()
    if logical_channel and label_map is not None:
        channel_text = f" | {_logical_channel_label(logical_channel, label_map)}"
    else:
        channel_text = f" | LC{logical_channel}" if logical_channel else ""
    return f"{ts_ns} ns | {adapter_id} | {action}{channel_text}"


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
    capture(
        "nominal_baud",
        lambda: _parse_int_text(item.get("nominal_baud", ""), "仲裁波特率", allow_empty=True, default=500000),
    )
    capture(
        "data_baud",
        lambda: _parse_int_text(item.get("data_baud", ""), "数据波特率", allow_empty=True, default=2000000),
    )
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
    from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QStackedWidget,
        QStyleFactory,
        QSplitter,
        QTabWidget,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    class BackgroundTask(QObject):
        succeeded = Signal(object)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, task: Callable[[], Any]) -> None:
            super().__init__()
            self._task = task

        @Slot()
        def run(self) -> None:
            try:
                result = self._task()
            except Exception as exc:
                message = str(exc).strip() or exc.__class__.__name__
                self.failed.emit(message)
            else:
                self.succeeded.emit(result)
            finally:
                self.finished.emit()

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
            self._last_saved_payload: Optional[dict] = None
            self._last_valid_payload: Optional[dict] = None
            self._validation_errors: list[ValidationIssue] = []
            self._validation_warnings: list[ValidationIssue] = []
            self._feedback_message = ""
            self._feedback_tone = "muted"
            self._is_dirty = False
            self._raw_dirty = False
            self._suspend_updates = False
            self._section_boxes: dict[str, QGroupBox] = {}
            self._section_titles: dict[str, str] = {}
            self._field_widgets: dict[str, QWidget] = {}
            self._field_error_labels: dict[str, QLabel] = {}
            self._binding_field_widgets: dict[str, QWidget] = {}
            self._binding_field_error_labels: dict[str, QLabel] = {}
            self._binding_error_counts: dict[int, int] = {}
            self._binding_list_error_messages: dict[int, list[str]] = {}
            self._draft_bindings: list[dict] = []
            self._database_binding_drafts: dict[int, dict] = {}
            self._database_binding_duplicate_counts: dict[int, int] = {}
            self._database_binding_statuses: dict[int, dict[str, Any]] = {}
            self._collection_data = {
                "database_bindings": [],
                "signal_overrides": [],
                "diagnostic_targets": [],
                "diagnostic_actions": [],
                "link_actions": [],
            }
            self._collection_sections: dict[str, dict[str, Any]] = {}
            self._trace_records_cache: dict[str, TraceFileRecord] = {}
            self._trace_source_summary_cache: dict[str, list[dict[str, Any]]] = {}
            self._validation_timer = QTimer(self)
            self._validation_timer.setSingleShot(True)
            self._validation_timer.setInterval(150)
            self._validation_timer.timeout.connect(self._run_live_validation)
            self.setObjectName("scenarioEditorDialog")
            self.setWindowTitle("场景编辑器")
            self.resize(1280, 980)
            self._build_ui()
            self._apply_editor_styles()
            self.load_payload(self._default_scenario_payload())

        def current_scenario_id(self) -> str:
            return self.scenario_id_edit.text().strip()

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

        def _apply_editor_styles(self) -> None:
            self.setStyleSheet(
                """
                QDialog#scenarioEditorDialog {
                    background: #f7f1ea;
                    color: #1f1b16;
                }
                QDialog#scenarioEditorDialog QFrame#editorToolbar {
                    background: #fffaf5;
                    border: 1px solid #e6d7c7;
                    border-radius: 16px;
                }
                QDialog#scenarioEditorDialog QLabel[role="headerTitle"] {
                    font-size: 18px;
                    font-weight: 700;
                }
                QDialog#scenarioEditorDialog QLabel[role="sectionHint"] {
                    color: #6b7280;
                }
                QDialog#scenarioEditorDialog QLabel[tone="good"] {
                    color: #15803d;
                }
                QDialog#scenarioEditorDialog QLabel[tone="warn"] {
                    color: #b45309;
                }
                QDialog#scenarioEditorDialog QLabel[tone="error"] {
                    color: #b42318;
                }
                QDialog#scenarioEditorDialog QLabel[tone="muted"] {
                    color: #6b7280;
                }
                QDialog#scenarioEditorDialog QLabel[errorLabel="true"] {
                    color: #b42318;
                    min-height: 16px;
                }
                QDialog#scenarioEditorDialog QGroupBox {
                    background: #fffaf5;
                    border: 1px solid #e6d7c7;
                    border-radius: 16px;
                    margin-top: 14px;
                    font-weight: 700;
                }
                QDialog#scenarioEditorDialog QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 12px;
                    padding: 0 6px;
                }
                QDialog#scenarioEditorDialog QFrame#bindingEditorPanel {
                    background: #fff;
                    border: 1px solid #e6d7c7;
                    border-radius: 14px;
                }
                QDialog#scenarioEditorDialog QPushButton {
                    border-radius: 10px;
                    padding: 8px 14px;
                    font-weight: 700;
                    border: 1px solid #d1d5db;
                    background: #f3f4f6;
                    color: #1f2937;
                }
                QDialog#scenarioEditorDialog QPushButton:hover {
                    background: #e5e7eb;
                }
                QDialog#scenarioEditorDialog QPushButton[variant="primary"] {
                    border-color: #2563eb;
                    background: #2563eb;
                    color: white;
                }
                QDialog#scenarioEditorDialog QPushButton[variant="primary"]:hover {
                    background: #1d4ed8;
                }
                QDialog#scenarioEditorDialog QPushButton[variant="danger"] {
                    border-color: #dc2626;
                    background: #dc2626;
                    color: white;
                }
                QDialog#scenarioEditorDialog QPushButton[variant="danger"]:hover {
                    background: #b91c1c;
                }
                QDialog#scenarioEditorDialog QLineEdit,
                QDialog#scenarioEditorDialog QPlainTextEdit,
                QDialog#scenarioEditorDialog QListWidget,
                QDialog#scenarioEditorDialog QComboBox {
                    background: #fff;
                    border: 1px solid #d6c8bb;
                    border-radius: 10px;
                    padding: 6px 8px;
                }
                QDialog#scenarioEditorDialog QLineEdit[readOnly="true"] {
                    background: #f3ede7;
                    color: #6b5f55;
                }
                QDialog#scenarioEditorDialog QLineEdit[errorState="true"],
                QDialog#scenarioEditorDialog QPlainTextEdit[errorState="true"],
                QDialog#scenarioEditorDialog QComboBox[errorState="true"] {
                    border-color: #b42318;
                    background: #fff5f5;
                }
                QDialog#scenarioEditorDialog QListWidget {
                    padding: 4px;
                }
                QDialog#scenarioEditorDialog QListWidget::item {
                    border-radius: 8px;
                    padding: 6px;
                }
                QDialog#scenarioEditorDialog QListWidget::item:selected {
                    background: #dbeafe;
                    color: #1d4ed8;
                }
                QDialog#scenarioEditorDialog QTabWidget::pane {
                    border: 1px solid #e6d7c7;
                    border-radius: 14px;
                    background: #fffaf5;
                }
                QDialog#scenarioEditorDialog QTabBar::tab {
                    background: #efe5da;
                    border: 1px solid #e6d7c7;
                    border-bottom: none;
                    border-top-left-radius: 10px;
                    border-top-right-radius: 10px;
                    padding: 8px 14px;
                    margin-right: 4px;
                }
                QDialog#scenarioEditorDialog QTabBar::tab:selected {
                    background: #fffaf5;
                    color: #2563eb;
                }
                """
            )

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

        def closeEvent(self, event) -> None:
            if self._confirm_close_with_unsaved_changes():
                event.accept()
                return
            event.ignore()

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

        def _set_button_variant(self, button: QPushButton, variant: str) -> None:
            button.setProperty("variant", variant)
            self._refresh_style(button)

        def _refresh_style(self, widget: QWidget) -> None:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

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

    class MainWindow(QMainWindow, MainWindowMixin):
        def __init__(self) -> None:
            super().__init__()
            self.app_logic = app_logic
            self._log_cursor = 0
            self._scenario_editor: Optional[ScenarioEditorDialog] = None
            self._current_scenario_payload = ScenarioSpec.from_dict(self._default_scenario_payload()).to_dict()
            self._override_catalog_channels: set[int] = set()
            self._override_catalog_statuses: dict[int, dict[str, Any]] = {}
            self._frame_enable_candidate_ids: dict[int, list[int]] = {}
            self._frame_enable_candidate_trace_ids: tuple[str, ...] = ()
            self._frame_enable_candidate_binding_signature: tuple[tuple[str, int, int, str], ...] = ()
            self._all_trace_records: list[TraceFileRecord] = []
            self._trace_lookup: dict[str, TraceFileRecord] = {}
            self._all_scenarios: list[ScenarioSpec] = []
            self._scenario_lookup: dict[str, ScenarioSpec] = {}
            self._trace_import_in_progress = False
            self._trace_import_thread: Optional[QThread] = None
            self._trace_import_worker: Optional[BackgroundTask] = None
            self._replay_prepare_in_progress = False
            self._replay_prepare_thread: Optional[QThread] = None
            self._replay_prepare_worker: Optional[BackgroundTask] = None
            self._replay_prepare_message = ""
            self.setWindowTitle("多总线回放与诊断平台")
            self.resize(1480, 980)
            self._build_ui()
            self._refresh_all()
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._refresh_runtime_view)
            self._timer.start(250)

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
            self.setStyleSheet(
                """
                QMainWindow, QWidget#mainRoot {
                    background: #f6f1ea;
                    color: #1f2933;
                }
                QGroupBox {
                    background: #fffaf5;
                    border: 1px solid #e2d5c8;
                    border-radius: 16px;
                    margin-top: 14px;
                    font-weight: 700;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 12px;
                    padding: 0 6px;
                }
                QTabWidget::pane {
                    border: 1px solid #e2d5c8;
                    border-radius: 14px;
                    background: #fffaf5;
                }
                QTabBar::tab {
                    background: #efe4d8;
                    border: 1px solid #e2d5c8;
                    border-bottom: none;
                    border-top-left-radius: 10px;
                    border-top-right-radius: 10px;
                    padding: 8px 14px;
                    margin-right: 4px;
                }
                QTabBar::tab:selected {
                    background: #fffaf5;
                    color: #1d4ed8;
                }
                QSplitter::handle {
                    background: #eadfd2;
                }
                QSplitter::handle:vertical {
                    height: 8px;
                }
                QPushButton {
                    border-radius: 10px;
                    padding: 8px 14px;
                    font-weight: 700;
                    border: 1px solid #d1d5db;
                    background: #f3f4f6;
                    color: #1f2937;
                }
                QPushButton:hover {
                    background: #e5e7eb;
                }
                QPushButton:disabled {
                    background: #f3efe9;
                    color: #a0a6ad;
                    border-color: #e5ddd2;
                }
                QPushButton[variant="primary"] {
                    background: #c2410c;
                    border-color: #c2410c;
                    color: white;
                }
                QPushButton[variant="primary"]:hover {
                    background: #9a3412;
                }
                QPushButton[variant="primary"]:disabled {
                    background: #ebe3db;
                    border-color: #ddd2c6;
                    color: #a39a92;
                }
                QPushButton[variant="secondary"] {
                    background: #f8fafc;
                    border-color: #cbd5e1;
                    color: #0f172a;
                }
                QPushButton[variant="secondary"]:hover {
                    background: #e2e8f0;
                }
                QPushButton[variant="secondary"]:disabled {
                    background: #f3efe9;
                    border-color: #e5ddd2;
                    color: #a0a6ad;
                }
                QPushButton[variant="danger"] {
                    background: #dc2626;
                    border-color: #dc2626;
                    color: white;
                }
                QPushButton[variant="danger"]:hover {
                    background: #b91c1c;
                }
                QPushButton[variant="danger"]:disabled {
                    background: #ede8e3;
                    border-color: #ddd5cb;
                    color: #a59d95;
                }
                QLabel[role="title"] {
                    font-size: 18px;
                    font-weight: 700;
                }
                QLabel[role="muted"] {
                    color: #6b7280;
                }
                QLabel[role="emptyState"] {
                    color: #7b756d;
                    font-size: 14px;
                }
                QLabel[tone="warn"] {
                    color: #b45309;
                }
                QLabel[tone="error"] {
                    color: #b42318;
                }
                QLabel[tone="good"] {
                    color: #15803d;
                }
                QLabel[badgeTone="good"] {
                    background: #dcfce7;
                    color: #166534;
                    border: 1px solid #86efac;
                }
                QLabel[badgeTone="warn"] {
                    background: #fef3c7;
                    color: #92400e;
                    border: 1px solid #fcd34d;
                }
                QLabel[badgeTone="error"] {
                    background: #fee2e2;
                    color: #991b1b;
                    border: 1px solid #fca5a5;
                }
                QLabel[badgeTone="info"] {
                    background: #dbeafe;
                    color: #1d4ed8;
                    border: 1px solid #93c5fd;
                }
                QLabel[badgeTone="muted"] {
                    background: #e5e7eb;
                    color: #4b5563;
                    border: 1px solid #d1d5db;
                }
                QLabel[badgeTone] {
                    border-radius: 999px;
                    padding: 4px 10px;
                    font-weight: 700;
                }
                QWidget[emptyState="true"] {
                    background: #fff;
                    border: 1px dashed #d8ccbf;
                    border-radius: 12px;
                }
                QLineEdit,
                QPlainTextEdit,
                QListWidget,
                QTableWidget,
                QSpinBox,
                QComboBox {
                    background: #ffffff;
                    border: 1px solid #d6c8bb;
                    border-radius: 10px;
                    padding: 6px 8px;
                }
                QListWidget::item,
                QTableWidget::item {
                    padding: 6px;
                }
                QListWidget::item:selected,
                QTableWidget::item:selected {
                    background: #dbeafe;
                    color: #1d4ed8;
                }
                QHeaderView::section {
                    background: #f4ede5;
                    border: none;
                    border-right: 1px solid #e2d5c8;
                    border-bottom: 1px solid #e2d5c8;
                    padding: 8px;
                    font-weight: 700;
                }
                """
            )

        def _set_button_variant(self, button: QPushButton, variant: str) -> None:
            button.setProperty("variant", variant)
            self._refresh_style(button)

        def _set_badge(self, label: QLabel, text: str, tone: str) -> None:
            label.setText(text)
            label.setProperty("badgeTone", tone)
            self._refresh_style(label)

        def _set_tone(self, label: QLabel, tone: str) -> None:
            label.setProperty("tone", tone)
            self._refresh_style(label)

        def _refresh_style(self, widget: QWidget) -> None:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

        def _set_trace_operation_message(self, message: str, *, tone: Optional[str] = None) -> None:
            self.trace_operation_label.setText(message)
            self.trace_operation_label.setProperty("tone", tone)
            self._refresh_style(self.trace_operation_label)
            self.trace_operation_label.setVisible(bool(message))

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

    return MainWindow()
