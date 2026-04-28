from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from replay_platform.adapters.base import DiagnosticClient, DeviceAdapter
from replay_platform.adapters.factory import build_adapters as create_adapters
from replay_platform.adapters.factory import build_diagnostics as create_diagnostics
from replay_platform.adapters.mock import MockDeviceAdapter
from replay_platform.adapters.tongxing import TongxingDeviceAdapter
from replay_platform.adapters.zlg import ZlgDeviceAdapter
from replay_platform.core import (
    AdapterCapabilities,
    BusType,
    DatabaseBinding,
    DeviceChannelBinding,
    FrameEvent,
    ReplayFrameLogMode,
    ReplayLaunchSource,
    ReplayLogConfig,
    ReplayLogLevel,
    ReplayRuntimeSnapshot,
    ReplayState,
    ScenarioSpec,
    SignalOverride,
    TraceFileRecord,
)
from replay_platform.paths import AppPaths
from replay_platform.runtime.engine import ReplayEngine
from replay_platform.services.frame_enable import FrameEnableService
from replay_platform.services.library import DeleteTraceResult, FileLibraryService
from replay_platform.services.replay_preparation import (
    PREPARED_TRACE_CACHE_LIMIT,
    PreparedTraceCacheKey,
    ReplayFramePreparer,
)
from replay_platform.services.runtime_overrides import RuntimeOverrideCoordinator
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


class ReplayApplication:
    def __init__(self, workspace: Path, log_config: ReplayLogConfig | None = None) -> None:
        self.paths = AppPaths(root=workspace)
        self.trace_loader = TraceLoader()
        self.library = FileLibraryService(self.paths, self.trace_loader)
        self.replay_preparer = ReplayFramePreparer(self.library)
        self.signal_overrides = SignalOverrideService()
        self._runtime_signal_overrides = SignalOverrideService()
        self.frame_enables = FrameEnableService()
        self.runtime_overrides = RuntimeOverrideCoordinator(
            workspace_overrides=self.list_workspace_signal_overrides,
            log_warning=self.log_warning,
        )
        self.log_config = log_config or ReplayLogConfig()
        self.engine = ReplayEngine(
            signal_overrides=self._runtime_signal_overrides,
            frame_enables=self.frame_enables,
            logger=self.log,
            log_config=self.log_config,
        )
        self._logs: List[str] = []
        self._log_base_index = 0
        self._log_lock = threading.Lock()
        self._last_runtime_state = ReplayState.STOPPED
        self._workspace_overrides: Dict[tuple[int, int, str], SignalOverride] = {}
        self._workspace_override_lock = threading.Lock()
        self._active_runtime_scenario: ScenarioSpec | None = None

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

    def list_workspace_signal_overrides(self) -> List[SignalOverride]:
        with self._workspace_override_lock:
            return sorted(
                self._workspace_overrides.values(),
                key=lambda item: (item.logical_channel, item.message_id_or_pgn, item.signal_name),
            )

    def replace_workspace_signal_overrides(
        self,
        overrides: Sequence[SignalOverride],
        *,
        sync_runtime: bool = False,
    ) -> None:
        with self._workspace_override_lock:
            self._workspace_overrides = {
                (item.logical_channel, item.message_id_or_pgn, item.signal_name): item
                for item in overrides
            }
        if sync_runtime:
            self._sync_runtime_signal_overrides()

    def set_workspace_signal_override(self, override: SignalOverride, *, sync_runtime: bool = False) -> None:
        with self._workspace_override_lock:
            self._workspace_overrides[(override.logical_channel, override.message_id_or_pgn, override.signal_name)] = override
        if sync_runtime:
            self._sync_runtime_signal_overrides()

    def clear_workspace_signal_override(
        self,
        logical_channel: int,
        message_id_or_pgn: int,
        signal_name: str,
        *,
        sync_runtime: bool = False,
    ) -> None:
        with self._workspace_override_lock:
            self._workspace_overrides.pop((logical_channel, message_id_or_pgn, signal_name), None)
        if sync_runtime:
            self._sync_runtime_signal_overrides()

    def clear_workspace_signal_overrides(self, *, sync_runtime: bool = False) -> None:
        with self._workspace_override_lock:
            self._workspace_overrides.clear()
        if sync_runtime:
            self._sync_runtime_signal_overrides()

    def rebuild_override_preview(self, bindings: Sequence[DatabaseBinding]) -> dict[int, dict[str, Any]]:
        return self._load_database_bindings(self.signal_overrides, bindings)

    def validate_workspace_signal_overrides(self, bindings: Sequence[DatabaseBinding]) -> dict[int, dict[str, Any]]:
        statuses = self.rebuild_override_preview(bindings)
        self._validate_signal_overrides(
            [("工作区覆盖", item) for item in self.list_workspace_signal_overrides()],
            statuses,
            self.signal_overrides,
        )
        return statuses

    def delete_trace(self, trace_id: str) -> DeleteTraceResult:
        result = self.library.delete_trace(trace_id)
        self.replay_preparer.invalidate_prepared_trace_cache(trace_id)
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
            self._active_runtime_scenario = None
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
        runtime_statuses = self._load_database_bindings(self._runtime_signal_overrides, scenario.database_bindings)
        self._log_database_binding_statuses(runtime_statuses)
        override_items = [("场景初始覆盖", item) for item in scenario.signal_overrides]
        override_items.extend(("工作区覆盖", item) for item in self.list_workspace_signal_overrides())
        self._validate_signal_overrides(override_items, runtime_statuses, self._runtime_signal_overrides)
        self._apply_runtime_signal_overrides(scenario)
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
        self._active_runtime_scenario = scenario
        self._last_runtime_state = self.engine.state

    def stop_replay(self) -> None:
        self.engine.stop()
        self.frame_enables.clear_all()
        self._active_runtime_scenario = None
        self._last_runtime_state = ReplayState.STOPPED

    def pause_replay(self) -> None:
        self.engine.pause()

    def resume_replay(self) -> None:
        self.engine.resume()

    def _runtime_override_coordinator(self) -> RuntimeOverrideCoordinator:
        coordinator = getattr(self, "runtime_overrides", None)
        if coordinator is None:
            log_warning = self.log_warning if hasattr(self, "log_config") and hasattr(self, "_log_lock") else (lambda message: None)
            coordinator = RuntimeOverrideCoordinator(
                workspace_overrides=self.list_workspace_signal_overrides,
                log_warning=log_warning,
            )
            self.runtime_overrides = coordinator
        return coordinator

    def _apply_runtime_signal_overrides(self, scenario: ScenarioSpec) -> None:
        self._runtime_override_coordinator().apply_runtime_signal_overrides(self._runtime_signal_overrides, scenario)

    def _sync_runtime_signal_overrides(self) -> None:
        scenario = self._active_runtime_scenario
        if scenario is None or self.engine.state == ReplayState.STOPPED:
            return
        self._apply_runtime_signal_overrides(scenario)

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

    def _load_database_bindings(
        self,
        service: SignalOverrideService,
        bindings: Sequence[DatabaseBinding],
    ) -> dict[int, dict[str, Any]]:
        return self._runtime_override_coordinator().load_database_bindings(service, bindings)

    def _validate_signal_overrides(
        self,
        overrides: Sequence[tuple[str, SignalOverride]],
        statuses: dict[int, dict[str, Any]],
        service: SignalOverrideService,
    ) -> None:
        self._runtime_override_coordinator().validate_signal_overrides(overrides, statuses, service)

    def _log_database_binding_statuses(self, statuses: dict[int, dict[str, Any]]) -> None:
        self._runtime_override_coordinator().log_database_binding_statuses(statuses)

    def _load_replay_frames(self, scenario: ScenarioSpec) -> List[FrameEvent]:
        return self.replay_preparer.load_replay_frames(scenario)

    def _prepared_trace_sequence(
        self,
        record: TraceFileRecord,
        mapped_bindings: Sequence[DeviceChannelBinding],
    ) -> Sequence[FrameEvent]:
        return self.replay_preparer.prepared_trace_sequence(record, mapped_bindings)

    @staticmethod
    def _source_filters_for_bindings(
        mapped_bindings: Sequence[DeviceChannelBinding],
    ) -> set[tuple[int, BusType]] | None:
        return ReplayFramePreparer.source_filters_for_bindings(mapped_bindings)

    @staticmethod
    def _prepared_trace_cache_key(
        record: TraceFileRecord,
        mapped_bindings: Sequence[DeviceChannelBinding],
    ) -> PreparedTraceCacheKey:
        return ReplayFramePreparer.prepared_trace_cache_key(record, mapped_bindings)

    def _get_prepared_trace_cache(
        self,
        cache_key: PreparedTraceCacheKey,
    ) -> tuple[FrameEvent, ...] | None:
        return self.replay_preparer.get_prepared_trace_cache(cache_key)

    def _store_prepared_trace_cache(
        self,
        cache_key: PreparedTraceCacheKey,
        frames: tuple[FrameEvent, ...],
    ) -> None:
        self.replay_preparer.store_prepared_trace_cache(cache_key, frames)

    def _invalidate_prepared_trace_cache(self, trace_id: str) -> None:
        self.replay_preparer.invalidate_prepared_trace_cache(trace_id)

    @staticmethod
    def _merge_sorted_frame_groups(frame_groups: Sequence[Sequence[FrameEvent]]) -> List[FrameEvent]:
        return ReplayFramePreparer.merge_sorted_frame_groups(frame_groups)

    @staticmethod
    def _map_trace_events_for_binding(
        trace_events: List[FrameEvent],
        binding: DeviceChannelBinding,
    ) -> List[FrameEvent]:
        return ReplayFramePreparer.map_trace_events_for_binding(trace_events, binding)

    def _build_adapters(self, scenario: ScenarioSpec) -> Dict[str, DeviceAdapter]:
        return create_adapters(
            scenario,
            zlg_adapter_cls=ZlgDeviceAdapter,
            tongxing_adapter_cls=TongxingDeviceAdapter,
            mock_adapter_cls=MockDeviceAdapter,
        )

    def _build_diagnostics(
        self,
        scenario: ScenarioSpec,
        adapters: Dict[str, DeviceAdapter],
    ) -> Dict[str, DiagnosticClient]:
        return create_diagnostics(scenario, adapters)
