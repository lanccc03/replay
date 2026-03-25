from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Dict, List

from replay_platform.adapters.base import DiagnosticClient, DeviceAdapter
from replay_platform.adapters.mock import MockDeviceAdapter
from replay_platform.adapters.tongxing import TongxingDeviceAdapter
from replay_platform.adapters.zlg import ZlgDeviceAdapter
from replay_platform.core import DiagnosticTransport, ScenarioSpec
from replay_platform.diagnostics.can_uds import CanUdsClient, IsoTpConfig
from replay_platform.diagnostics.doip import DoipDiagnosticClient, DoipLinkAdapter
from replay_platform.paths import AppPaths
from replay_platform.runtime.engine import ReplayEngine
from replay_platform.services.library import FileLibraryService
from replay_platform.services.signal_catalog import SignalOverrideService
from replay_platform.services.trace_loader import TraceLoader


LOG_BUFFER_LIMIT = 2000


class ReplayApplication:
    def __init__(self, workspace: Path) -> None:
        self.paths = AppPaths(root=workspace)
        self.trace_loader = TraceLoader()
        self.library = FileLibraryService(self.paths, self.trace_loader)
        self.signal_overrides = SignalOverrideService()
        self.engine = ReplayEngine(signal_overrides=self.signal_overrides, logger=self.log)
        self._logs: List[str] = []
        self._log_base_index = 0
        self._log_lock = threading.Lock()

    def log(self, message: str) -> None:
        with self._log_lock:
            self._logs.append(message)
            overflow = len(self._logs) - LOG_BUFFER_LIMIT
            if overflow > 0:
                del self._logs[:overflow]
                self._log_base_index += overflow

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

    def new_scenario(self, name: str) -> ScenarioSpec:
        return ScenarioSpec(scenario_id=uuid.uuid4().hex, name=name)

    def import_trace(self, path: str):
        return self.library.import_trace(path)

    def list_traces(self):
        return self.library.list_trace_files()

    def list_scenarios(self):
        return self.library.list_scenarios()

    def save_scenario(self, scenario: ScenarioSpec) -> None:
        self.library.save_scenario(scenario)

    def start_replay(self, scenario: ScenarioSpec) -> None:
        frames = []
        for trace_id in scenario.trace_file_ids:
            frames.extend(self.library.load_trace_events(trace_id))
        frames.sort(key=lambda item: item.ts_ns)
        for binding in scenario.database_bindings:
            self.signal_overrides.load_database(binding.logical_channel, binding.path)
        for override in scenario.signal_overrides:
            self.signal_overrides.set_override(override)
        adapters = self._build_adapters(scenario)
        diagnostics = self._build_diagnostics(scenario, adapters)
        self.engine.configure(scenario, frames, adapters, diagnostics)
        self.engine.start()

    def stop_replay(self) -> None:
        self.engine.stop()

    def pause_replay(self) -> None:
        self.engine.pause()

    def resume_replay(self) -> None:
        self.engine.resume()

    def _build_adapters(self, scenario: ScenarioSpec) -> Dict[str, DeviceAdapter]:
        adapters: Dict[str, DeviceAdapter] = {}
        for binding in scenario.bindings:
            if binding.adapter_id in adapters:
                continue
            driver = binding.driver.lower()
            if driver == "zlg":
                adapters[binding.adapter_id] = ZlgDeviceAdapter(binding.adapter_id, binding)
            elif driver == "tongxing":
                adapters[binding.adapter_id] = TongxingDeviceAdapter(binding.adapter_id)
            elif driver == "mock":
                adapters[binding.adapter_id] = MockDeviceAdapter(binding.adapter_id)
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
