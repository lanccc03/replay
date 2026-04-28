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

__all__ = (
    'USER_ROLE',
    'DRIVER_OPTIONS',
    'BUS_OPTIONS',
    'TRANSPORT_OPTIONS',
    'LINK_ACTION_OPTIONS',
    'FRAME_ENABLE_STATUS_OPTIONS',
    'LOG_LEVEL_OPTION_LABELS',
    'LOG_LEVEL_OPTIONS',
    'LOG_LEVEL_DEFAULT_HINT',
    'ZLG_DEVICE_TYPE_OPTIONS',
    'DRIVER_DEVICE_TYPE_OPTIONS',
    'LEGACY_ZLG_DEVICE_TYPES',
    'ValidationIssue',
    'DraftValidationResult',
    'EditorFieldSpec',
    'ScenarioLaunchAssessment',
    'PlaybackButtonState',
    'ScenarioBusinessSummary',
    'RuntimeVisibilitySummary',
    'FieldValidationError',
    '_clone_jsonable',
    '_display_text',
    '_normalize_driver_name',
    '_binding_device_type_options',
    '_binding_device_type_placeholder',
    '_parse_device_type_text',
    '_binding_warning_subject',
    '_binding_device_type_warning',
    '_format_table_value',
    '_format_json_text',
    '_format_field_value',
    '_parse_int_text',
    '_parse_bool_text',
    '_parse_json_object_text',
    '_parse_choice_text',
    '_require_text',
    '_parse_scalar_text',
    '_parse_hex_bytes_text',
    '_plan_log_refresh',
    '_log_level_option',
    '_parse_log_level_option',
    '_build_log_level_hint',
    '_normalize_scenario_payload',
    '_scenario_payload_is_dirty',
    '_build_json_preview',
    '_format_trace_preview',
    '_build_scenario_counts_summary',
    '_assess_scenario_launch',
    '_playback_button_state',
    '_format_replay_stats',
    '_format_duration_ns',
    '_trace_display_name',
    '_build_scenario_business_summary',
    '_database_binding_text',
    '_build_override_catalog_status_text',
    '_filter_trace_records',
    '_filter_scenarios',
    '_build_trace_selection_summary',
    '_normalize_trace_message_id_summary_item',
    '_build_frame_enable_candidate_ids_from_trace_summaries',
    '_build_scenario_selection_summary',
    '_build_trace_delete_summary',
    '_build_scenario_delete_summary',
    '_should_reset_current_scenario_after_delete',
    '_format_launch_source',
    '_build_device_status_text',
    '_build_runtime_visibility_summary',
    '_parse_optional_int_text',
    '_binding_uses_trace_source',
    '_trace_record_name',
    '_binding_source_label',
    '_binding_label_map',
    '_logical_channel_label',
    '_binding_summary',
    '_database_binding_summary',
    '_database_binding_map_from_items',
    '_database_binding_items_from_map',
    '_database_binding_orphan_items',
    '_database_binding_file_name',
    '_database_binding_status_summary',
    '_database_binding_status_detail',
    '_resource_mapping_summary',
    '_trace_mapping_completion_text',
    '_build_orphan_database_binding_text',
    '_signal_override_summary',
    '_format_override_message_option',
    '_parse_message_combo_text',
    '_build_signal_catalog_hint',
    '_signal_override_payload_items',
    '_frame_enable_status_text',
    '_frame_enable_rule_summary',
    '_diagnostic_target_summary',
    '_diagnostic_action_summary',
    '_link_action_summary',
    '_new_binding_draft',
    '_default_sdk_root_for_driver',
    '_binding_draft_from_item',
    '_normalize_binding_item',
    '_validate_binding_draft',
    '_normalize_database_binding_item',
    '_normalize_signal_override_item',
    '_normalize_diagnostic_target_item',
    '_normalize_diagnostic_action_item',
    '_normalize_link_action_item',
    'MainWindowMixin',
)
