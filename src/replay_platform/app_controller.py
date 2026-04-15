from __future__ import annotations

import heapq
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from replay_platform.adapters.base import DiagnosticClient, DeviceAdapter
from replay_platform.adapters.mock import MockDeviceAdapter
from replay_platform.adapters.tongxing import TongxingDeviceAdapter
from replay_platform.adapters.zlg import ZlgDeviceAdapter
from replay_platform.core import (
    AdapterCapabilities,
    BusType,
    ChannelDescriptor,
    DeviceChannelBinding,
    DiagnosticTransport,
    FrameEvent,
    ReplayFrameLogMode,
    ReplayLaunchSource,
    ReplayLogConfig,
    ReplayLogLevel,
    ReplayRuntimeSnapshot,
    ReplayState,
    ScenarioSpec,
    TraceFileRecord,
)
from replay_platform.services.frame_enable import FrameEnableService
from replay_platform.diagnostics.can_uds import CanUdsClient, IsoTpConfig
from replay_platform.diagnostics.doip import DoipDiagnosticClient, DoipLinkAdapter
from replay_platform.paths import AppPaths
from replay_platform.runtime.engine import ReplayEngine
from replay_platform.services.library import DeleteTraceResult, FileLibraryService
from replay_platform.services.signal_catalog import SignalOverrideService
from replay_platform.services.trace_loader import TraceLoader


LOG_BUFFER_LIMIT = 2000
DEBUG_LOG_FRAME_SAMPLE_RATE = 10
LOG_LEVEL_PRESET_WARNING = "warning"
LOG_LEVEL_PRESET_INFO = "info"
LOG_LEVEL_PRESET_DEBUG_SAMPLED = "debug_sampled"
LOG_LEVEL_PRESET_DEBUG_ALL = "debug_all"
LOG_LEVEL_PRESET_OPTIONS = (
    LOG_LEVEL_PRESET_WARNING,
    LOG_LEVEL_PRESET_INFO,
    LOG_LEVEL_PRESET_DEBUG_SAMPLED,
    LOG_LEVEL_PRESET_DEBUG_ALL,
)
PREPARED_TRACE_CACHE_LIMIT = 6
LOG_LEVEL_PRESET_CONFIGS = {
    LOG_LEVEL_PRESET_WARNING: (ReplayLogLevel.WARNING, ReplayFrameLogMode.OFF, DEBUG_LOG_FRAME_SAMPLE_RATE),
    LOG_LEVEL_PRESET_INFO: (ReplayLogLevel.INFO, ReplayFrameLogMode.OFF, DEBUG_LOG_FRAME_SAMPLE_RATE),
    LOG_LEVEL_PRESET_DEBUG_SAMPLED: (
        ReplayLogLevel.DEBUG,
        ReplayFrameLogMode.SAMPLED,
        DEBUG_LOG_FRAME_SAMPLE_RATE,
    ),
    LOG_LEVEL_PRESET_DEBUG_ALL: (ReplayLogLevel.DEBUG, ReplayFrameLogMode.ALL, DEBUG_LOG_FRAME_SAMPLE_RATE),
}


@dataclass(frozen=True)
class ReplayPreparation:
    scenario: ScenarioSpec
    frames: List[FrameEvent]
    launch_source: ReplayLaunchSource
    loop_enabled: bool = False


@dataclass(frozen=True)
class PreparedTraceCacheKey:
    trace_id: str
    source_label: str
    source_filters: tuple[tuple[int, str], ...]
    mapped_bindings: tuple[tuple[int, int, str], ...]


class ReplayApplication:
    def __init__(self, workspace: Path, log_config: ReplayLogConfig | None = None) -> None:
        self.paths = AppPaths(root=workspace)
        self.trace_loader = TraceLoader()
        self.library = FileLibraryService(self.paths, self.trace_loader)
        self.signal_overrides = SignalOverrideService()
        self.frame_enables = FrameEnableService()
        self.log_config = log_config or ReplayLogConfig()
        self.engine = ReplayEngine(
            signal_overrides=self.signal_overrides,
            frame_enables=self.frame_enables,
            logger=self.log,
            log_config=self.log_config,
        )
        self._logs: List[str] = []
        self._log_base_index = 0
        self._log_lock = threading.Lock()
        self._last_runtime_state = ReplayState.STOPPED
        self._prepared_trace_cache: OrderedDict[PreparedTraceCacheKey, tuple[FrameEvent, ...]] = OrderedDict()
        self._prepared_trace_cache_lock = threading.Lock()

    def log(self, message: str) -> None:
        self._append_log(message)

    def _append_log(self, message: str) -> None:
        with self._log_lock:
            self._logs.append(message)
            overflow = len(self._logs) - LOG_BUFFER_LIMIT
            if overflow > 0:
                del self._logs[:overflow]
                self._log_base_index += overflow

    def log_warning(self, message: str) -> None:
        if self.log_config.allows(ReplayLogLevel.WARNING):
            self._append_log(message)

    def log_info(self, message: str) -> None:
        if self.log_config.allows(ReplayLogLevel.INFO):
            self._append_log(message)

    def log_debug(self, message: str) -> None:
        if self.log_config.allows(ReplayLogLevel.DEBUG):
            self._append_log(message)

    def current_log_level_preset(self) -> str:
        level = self.log_config.level if isinstance(self.log_config.level, ReplayLogLevel) else ReplayLogLevel(self.log_config.level)
        frame_mode = (
            self.log_config.frame_mode
            if isinstance(self.log_config.frame_mode, ReplayFrameLogMode)
            else ReplayFrameLogMode(self.log_config.frame_mode)
        )
        if level == ReplayLogLevel.WARNING:
            return LOG_LEVEL_PRESET_WARNING
        if level == ReplayLogLevel.INFO:
            return LOG_LEVEL_PRESET_INFO
        if frame_mode == ReplayFrameLogMode.ALL:
            return LOG_LEVEL_PRESET_DEBUG_ALL
        return LOG_LEVEL_PRESET_DEBUG_SAMPLED

    def apply_log_level_preset(self, preset: ReplayLogLevel | str) -> None:
        normalized_preset = self._normalize_log_level_preset(preset)
        preset_level, frame_mode, sample_rate = LOG_LEVEL_PRESET_CONFIGS[normalized_preset]
        self.log_config.level = preset_level
        self.log_config.frame_mode = frame_mode
        self.log_config.frame_sample_rate = sample_rate
        if self.engine.log_config is not self.log_config:
            self.engine.log_config.level = preset_level
            self.engine.log_config.frame_mode = frame_mode
            self.engine.log_config.frame_sample_rate = sample_rate

    def _normalize_log_level_preset(self, preset: ReplayLogLevel | str) -> str:
        if isinstance(preset, ReplayLogLevel):
            if preset == ReplayLogLevel.WARNING:
                return LOG_LEVEL_PRESET_WARNING
            if preset == ReplayLogLevel.INFO:
                return LOG_LEVEL_PRESET_INFO
            return LOG_LEVEL_PRESET_DEBUG_SAMPLED
        if preset in LOG_LEVEL_PRESET_CONFIGS:
            return preset
        normalized_level = ReplayLogLevel(preset)
        return self._normalize_log_level_preset(normalized_level)

    @property
    def log_limit(self) -> int:
        return LOG_BUFFER_LIMIT

    @property
    def logs(self) -> List[str]:
        _, entries = self.log_snapshot()
        return entries

    def log_snapshot(self) -> tuple[int, List[str]]:
        with self._log_lock:
            return self._log_base_index, list(self._logs)

    def clear_logs(self) -> None:
        with self._log_lock:
            self._logs.clear()
            self._log_base_index = 0

    def new_scenario(self, name: str) -> ScenarioSpec:
        return ScenarioSpec(scenario_id=uuid.uuid4().hex, name=name)

    def import_trace(self, path: str):
        return self.library.import_trace(path)

    def list_traces(self):
        return self.library.list_trace_files()

    def get_trace_file(self, trace_id: str):
        return self.library.get_trace_file(trace_id)

    def get_trace_source_summaries(self, trace_id: str) -> list[dict]:
        return self.library.get_trace_source_summaries(trace_id)

    def get_trace_message_id_summaries(self, trace_id: str) -> list[dict]:
        return self.library.get_trace_message_id_summaries(trace_id)

    def find_scenarios_referencing_trace(self, trace_id: str):
        return self.library.find_scenarios_referencing_trace(trace_id)

    def list_scenarios(self):
        return self.library.list_scenarios()

    def save_scenario(self, scenario: ScenarioSpec) -> None:
        self.library.save_scenario(scenario)

    def delete_trace(self, trace_id: str) -> DeleteTraceResult:
        result = self.library.delete_trace(trace_id)
        self._invalidate_prepared_trace_cache(trace_id)
        return result

    def delete_scenario(self, scenario_id: str) -> None:
        self.library.delete_scenario(scenario_id)

    def runtime_snapshot(self) -> ReplayRuntimeSnapshot:
        snapshot = self.engine.snapshot()
        if snapshot.state == ReplayState.STOPPED and self.engine.has_pending_completion_cleanup():
            self.engine.finalize_completed_replay()
            snapshot = self.engine.snapshot()
        if snapshot.state == ReplayState.STOPPED and self._last_runtime_state != ReplayState.STOPPED:
            self.frame_enables.clear_all()
        self._last_runtime_state = snapshot.state
        return snapshot

    def start_replay(
        self,
        scenario: ScenarioSpec,
        *,
        launch_source: ReplayLaunchSource = ReplayLaunchSource.SCENARIO_BOUND,
        loop_enabled: bool = False,
    ) -> None:
        preparation = self.prepare_replay(
            scenario,
            launch_source=launch_source,
            loop_enabled=loop_enabled,
        )
        self.start_prepared_replay(preparation)

    def prepare_replay(
        self,
        scenario: ScenarioSpec,
        *,
        launch_source: ReplayLaunchSource = ReplayLaunchSource.SCENARIO_BOUND,
        loop_enabled: bool = False,
    ) -> ReplayPreparation:
        frames = self._load_replay_frames(scenario)
        return ReplayPreparation(
            scenario=scenario,
            frames=frames,
            launch_source=launch_source,
            loop_enabled=bool(loop_enabled),
        )

    def start_prepared_replay(self, preparation: ReplayPreparation) -> None:
        scenario = preparation.scenario
        self.frame_enables.clear_all()
        for binding in scenario.database_bindings:
            self.signal_overrides.load_database(binding.logical_channel, binding.path)
        for override in scenario.signal_overrides:
            self.signal_overrides.set_override(override)
        adapters = self._build_adapters(scenario)
        diagnostics = self._build_diagnostics(scenario, adapters)
        self.engine.configure(
            scenario,
            preparation.frames,
            adapters,
            diagnostics,
            launch_source=preparation.launch_source,
            loop_enabled=preparation.loop_enabled,
        )
        self.engine.start()
        self._last_runtime_state = self.engine.state

    def stop_replay(self) -> None:
        self.engine.stop()
        self.frame_enables.clear_all()
        self._last_runtime_state = ReplayState.STOPPED

    def pause_replay(self) -> None:
        self.engine.pause()

    def resume_replay(self) -> None:
        self.engine.resume()

    def probe_binding_channels(self, binding: DeviceChannelBinding) -> dict[str, Any]:
        result: dict[str, Any] = {
            "channels": [],
            "capabilities": AdapterCapabilities(),
            "error": "",
        }
        scenario = ScenarioSpec(scenario_id="probe-binding", name="probe-binding", bindings=[binding])
        adapter: DeviceAdapter | None = None
        try:
            adapters = self._build_adapters(scenario)
            adapter = adapters.get(binding.adapter_id)
            if adapter is None:
                result["error"] = f"未找到适配器 {binding.adapter_id}。"
                return result
            result["capabilities"] = adapter.capabilities()
            result["channels"] = list(adapter.enumerate_channels())
            return result
        except Exception as exc:
            result["error"] = str(exc)
            return result
        finally:
            if adapter is not None:
                try:
                    adapter.close()
                except Exception:
                    pass

    def _load_replay_frames(self, scenario: ScenarioSpec) -> List[FrameEvent]:
        trace_sequences: List[Sequence[FrameEvent]] = []
        trace_bound_bindings: Dict[str, List[DeviceChannelBinding]] = {}
        for binding in scenario.bindings:
            if not binding.trace_file_id:
                continue
            if binding.source_channel is None or binding.source_bus_type is None:
                raise ValueError(f"逻辑通道 {binding.logical_channel} 的文件映射不完整。")
            trace_bound_bindings.setdefault(binding.trace_file_id, []).append(binding)
        missing_trace_ids = sorted(set(trace_bound_bindings) - set(scenario.trace_file_ids))
        if missing_trace_ids:
            raise ValueError(f"存在未勾选但仍被映射的文件：{', '.join(missing_trace_ids)}")

        for trace_id in scenario.trace_file_ids:
            record = self.library.get_trace_file(trace_id)
            if record is None:
                raise FileNotFoundError(trace_id)
            trace_sequences.append(
                self._prepared_trace_sequence(
                    record,
                    trace_bound_bindings.get(trace_id, []),
                )
            )
        return self._merge_sorted_frame_groups(trace_sequences)

    def _prepared_trace_sequence(
        self,
        record: TraceFileRecord,
        mapped_bindings: Sequence[DeviceChannelBinding],
    ) -> Sequence[FrameEvent]:
        cache_key = self._prepared_trace_cache_key(record, mapped_bindings)
        cached = self._get_prepared_trace_cache(cache_key)
        if cached is not None:
            return cached
        source_filters = self._source_filters_for_bindings(mapped_bindings)
        trace_events = self.library.load_trace_events(record.trace_id, source_filters=source_filters)
        source_label = record.original_path or record.name
        if source_label:
            trace_events = [event.clone(source_file=source_label) for event in trace_events]
        if mapped_bindings:
            events_by_source: Dict[tuple[int, BusType], List[FrameEvent]] = {}
            for event in trace_events:
                events_by_source.setdefault((event.channel, event.bus_type), []).append(event)
            mapped_sequences = [
                self._map_trace_events_for_binding(
                    events_by_source.get((int(binding.source_channel), binding.source_bus_type), []),
                    binding,
                )
                for binding in mapped_bindings
            ]
            prepared_sequence = tuple(self._merge_sorted_frame_groups(mapped_sequences))
        else:
            prepared_sequence = tuple(trace_events)
        self._store_prepared_trace_cache(cache_key, prepared_sequence)
        return prepared_sequence

    @staticmethod
    def _source_filters_for_bindings(
        mapped_bindings: Sequence[DeviceChannelBinding],
    ) -> set[tuple[int, BusType]] | None:
        if not mapped_bindings:
            return None
        return {
            (int(binding.source_channel), binding.source_bus_type)
            for binding in mapped_bindings
            if binding.source_channel is not None and binding.source_bus_type is not None
        } or None

    @staticmethod
    def _prepared_trace_cache_key(
        record: TraceFileRecord,
        mapped_bindings: Sequence[DeviceChannelBinding],
    ) -> PreparedTraceCacheKey:
        source_label = record.original_path or record.name
        source_filters = tuple(
            sorted(
                (int(binding.source_channel), binding.source_bus_type.value)
                for binding in mapped_bindings
                if binding.source_channel is not None and binding.source_bus_type is not None
            )
        )
        mapping_signature = tuple(
            (
                binding.logical_channel,
                int(binding.source_channel),
                binding.source_bus_type.value,
            )
            for binding in mapped_bindings
            if binding.source_channel is not None and binding.source_bus_type is not None
        )
        return PreparedTraceCacheKey(
            trace_id=record.trace_id,
            source_label=source_label,
            source_filters=source_filters,
            mapped_bindings=mapping_signature,
        )

    def _get_prepared_trace_cache(
        self,
        cache_key: PreparedTraceCacheKey,
    ) -> tuple[FrameEvent, ...] | None:
        with self._prepared_trace_cache_lock:
            cached = self._prepared_trace_cache.get(cache_key)
            if cached is None:
                return None
            self._prepared_trace_cache.move_to_end(cache_key)
            return cached

    def _store_prepared_trace_cache(
        self,
        cache_key: PreparedTraceCacheKey,
        frames: tuple[FrameEvent, ...],
    ) -> None:
        with self._prepared_trace_cache_lock:
            self._prepared_trace_cache[cache_key] = frames
            self._prepared_trace_cache.move_to_end(cache_key)
            while len(self._prepared_trace_cache) > PREPARED_TRACE_CACHE_LIMIT:
                self._prepared_trace_cache.popitem(last=False)

    def _invalidate_prepared_trace_cache(self, trace_id: str) -> None:
        with self._prepared_trace_cache_lock:
            stale_keys = [key for key in self._prepared_trace_cache if key.trace_id == trace_id]
            for cache_key in stale_keys:
                self._prepared_trace_cache.pop(cache_key, None)

    @staticmethod
    def _merge_sorted_frame_groups(frame_groups: Sequence[Sequence[FrameEvent]]) -> List[FrameEvent]:
        non_empty_groups = [group for group in frame_groups if group]
        if not non_empty_groups:
            return []
        if len(non_empty_groups) == 1:
            return list(non_empty_groups[0])
        return list(heapq.merge(*non_empty_groups, key=lambda item: item.ts_ns))

    @staticmethod
    def _map_trace_events_for_binding(
        trace_events: List[FrameEvent],
        binding: DeviceChannelBinding,
    ) -> List[FrameEvent]:
        assert binding.source_channel is not None
        assert binding.source_bus_type is not None
        mapped_events: List[FrameEvent] = []
        for event in trace_events:
            if event.channel != binding.source_channel or event.bus_type != binding.source_bus_type:
                continue
            mapped_events.append(event.clone(channel=binding.logical_channel))
        return mapped_events

    def _build_adapters(self, scenario: ScenarioSpec) -> Dict[str, DeviceAdapter]:
        adapters: Dict[str, DeviceAdapter] = {}
        bindings_by_adapter: Dict[str, List[DeviceChannelBinding]] = {}
        for binding in scenario.bindings:
            bindings_by_adapter.setdefault(binding.adapter_id, []).append(binding)
        for adapter_id, binding_group in bindings_by_adapter.items():
            binding = binding_group[0]
            driver = binding.driver.lower()
            if driver == "zlg":
                adapters[adapter_id] = ZlgDeviceAdapter(adapter_id, binding)
            elif driver == "tongxing":
                seed_binding = max(binding_group, key=lambda item: int(item.physical_channel))
                adapters[adapter_id] = TongxingDeviceAdapter(adapter_id, seed_binding)
            elif driver == "mock":
                adapters[adapter_id] = MockDeviceAdapter(adapter_id)
            else:
                raise ValueError(f"不支持的驱动类型：{binding.driver}")
        return adapters

    def _build_diagnostics(
        self,
        scenario: ScenarioSpec,
        adapters: Dict[str, DeviceAdapter],
    ) -> Dict[str, DiagnosticClient]:
        diagnostics: Dict[str, DiagnosticClient] = {}
        for target in scenario.diagnostic_targets:
            if target.transport == DiagnosticTransport.DOIP:
                diagnostics[target.name] = DoipDiagnosticClient(
                    DoipLinkAdapter(
                        host=target.host,
                        port=target.port,
                        source_address=target.source_address,
                        target_address=target.target_address,
                        activation_type=target.activation_type,
                        timeout_ms=target.timeout_ms,
                    )
                )
                continue
            binding = scenario.find_binding(target.logical_channel)
            if binding is None:
                raise ValueError(f"诊断逻辑通道 {target.logical_channel} 未绑定设备。")
            adapter = adapters[binding.adapter_id]
            diagnostics[target.name] = CanUdsClient(
                adapter,
                IsoTpConfig(
                    channel=binding.physical_channel,
                    tx_id=target.tx_id,
                    rx_id=target.rx_id,
                    bus_type=binding.bus_type,
                    timeout_ms=target.timeout_ms,
                ),
            )
        return diagnostics
