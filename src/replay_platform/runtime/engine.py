from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from replay_platform.adapters.base import DeviceAdapter, DiagnosticClient
from replay_platform.core import (
    DeviceChannelBinding,
    DiagnosticAction,
    FrameEvent,
    LinkAction,
    LinkActionType,
    ReplayState,
    ReplayStats,
    ScenarioSpec,
    TimelineItem,
    UdsRequest,
)
from replay_platform.errors import AdapterOperationError, ConfigurationError
from replay_platform.services.signal_catalog import SignalOverrideService


class ReplayEngine:
    def __init__(
        self,
        signal_overrides: Optional[SignalOverrideService] = None,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.signal_overrides = signal_overrides or SignalOverrideService()
        self.logger = logger or (lambda _message: None)
        self.state = ReplayState.STOPPED
        self.stats = ReplayStats()
        self._scenario: Optional[ScenarioSpec] = None
        self._timeline: List[TimelineItem] = []
        self._adapters: Dict[str, DeviceAdapter] = {}
        self._diagnostics: Dict[str, DiagnosticClient] = {}
        self._thread: Optional[threading.Thread] = None
        self._condition = threading.Condition()
        self._stop_requested = False
        self._base_perf_ns = 0
        self._pause_started_ns = 0
        self._timeline_index = 0

    def configure(
        self,
        scenario: ScenarioSpec,
        frames: Sequence[FrameEvent],
        adapters: Dict[str, DeviceAdapter],
        diagnostics: Optional[Dict[str, DiagnosticClient]] = None,
    ) -> None:
        self._scenario = scenario
        self._timeline = scenario.timeline_items(frames)
        self._adapters = adapters
        self._diagnostics = diagnostics or {}
        self._timeline_index = 0
        self.stats = ReplayStats()

    def start(self) -> None:
        if self._scenario is None:
            raise ConfigurationError("回放引擎尚未加载场景配置。")
        if self.state == ReplayState.RUNNING:
            return
        self._prepare_channels()
        self._stop_requested = False
        self.state = ReplayState.RUNNING
        self._base_perf_ns = time.perf_counter_ns()
        self._thread = threading.Thread(target=self._run_loop, name="replay-engine", daemon=True)
        self._thread.start()
        self.logger("回放已开始。")

    def pause(self) -> None:
        with self._condition:
            if self.state != ReplayState.RUNNING:
                return
            self.state = ReplayState.PAUSED
            self._pause_started_ns = time.perf_counter_ns()
            self._condition.notify_all()
        self.logger("回放已暂停。")

    def resume(self) -> None:
        with self._condition:
            if self.state != ReplayState.PAUSED:
                return
            now = time.perf_counter_ns()
            self._base_perf_ns += now - self._pause_started_ns
            self.state = ReplayState.RUNNING
            self._condition.notify_all()
        self.logger("回放已继续。")

    def stop(self) -> None:
        with self._condition:
            self._stop_requested = True
            self.state = ReplayState.STOPPED
            self.signal_overrides.clear_all()
            self._condition.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._teardown_adapters()
        self._timeline_index = 0
        self.logger("回放已停止。")

    def seek_to_start(self) -> None:
        if self.state == ReplayState.RUNNING:
            raise ConfigurationError("请先停止或暂停回放，再回到起点。")
        self._timeline_index = 0
        self._base_perf_ns = time.perf_counter_ns()
        self.logger("回放位置已重置。")

    def _prepare_channels(self) -> None:
        assert self._scenario is not None
        seen = set()
        for binding in self._scenario.bindings:
            adapter = self._adapters.get(binding.adapter_id)
            if adapter is None:
                raise ConfigurationError(f"适配器 {binding.adapter_id} 未配置。")
            if binding.adapter_id not in seen:
                adapter.open()
                seen.add(binding.adapter_id)
            adapter.start_channel(binding.physical_channel, binding.channel_config())

    def _run_loop(self) -> None:
        while True:
            with self._condition:
                if self._stop_requested:
                    return
                if self.state == ReplayState.PAUSED:
                    self._condition.wait(timeout=0.1)
                    continue
            if self._timeline_index >= len(self._timeline):
                self.state = ReplayState.STOPPED
                self._teardown_adapters()
                self.logger("回放已完成。")
                return
            item = self._timeline[self._timeline_index]
            target_ns = self._base_perf_ns + item.ts_ns
            now_ns = time.perf_counter_ns()
            if now_ns < target_ns:
                self._sleep_until(target_ns)
                continue
            try:
                self._dispatch(item)
            except Exception as exc:  # pragma: no cover - defensive runtime logging
                self.stats.errors.append(str(exc))
                self.logger(f"回放异常：{exc}")
            finally:
                self._timeline_index += 1

    def _sleep_until(self, target_ns: int) -> None:
        while True:
            with self._condition:
                if self._stop_requested or self.state == ReplayState.PAUSED:
                    return
            now_ns = time.perf_counter_ns()
            if now_ns >= target_ns:
                return
            remaining = max((target_ns - now_ns) / 1_000_000_000, 0.0)
            time.sleep(min(remaining, 0.002))

    def _dispatch(self, item: TimelineItem) -> None:
        if isinstance(item, FrameEvent):
            self._dispatch_frame(item)
            return
        if isinstance(item, DiagnosticAction):
            self._dispatch_diagnostic(item)
            return
        if isinstance(item, LinkAction):
            self._dispatch_link(item)
            return
        raise ConfigurationError(f"不支持的时间轴项目：{item!r}")

    def _dispatch_frame(self, frame: FrameEvent) -> None:
        binding = self._binding_for(frame.channel)
        if binding is None:
            raise ConfigurationError(f"逻辑通道 {frame.channel} 未绑定设备。")
        adapter = self._adapters[binding.adapter_id]
        mapped = self.signal_overrides.apply(frame)
        mapped = mapped.clone(channel=binding.physical_channel)
        sent = adapter.send([mapped])
        if sent == 1:
            self.stats.sent_frames += 1
        else:
            self.stats.skipped_frames += 1

    def _dispatch_diagnostic(self, action: DiagnosticAction) -> None:
        client = self._diagnostics.get(action.target)
        if client is None:
            raise ConfigurationError(f'未找到名为 "{action.target}" 的诊断目标。')
        response = client.request(UdsRequest(action.service_id, action.payload, action.timeout_ms))
        self.stats.diagnostic_actions += 1
        self.logger(
            f"诊断 {action.target} SID=0x{action.service_id:02X} "
            f"{'正响应' if response.positive else '负响应'}"
        )

    def _dispatch_link(self, action: LinkAction) -> None:
        adapter = self._adapters.get(action.adapter_id)
        if adapter is None:
            raise ConfigurationError(f"未找到名为 {action.adapter_id} 的适配器。")
        physical_channel = None
        if action.logical_channel is not None:
            binding = self._binding_for(action.logical_channel)
            physical_channel = binding.physical_channel if binding else None
        if action.action == LinkActionType.DISCONNECT:
            if physical_channel is not None:
                adapter.stop_channel(physical_channel)
            else:
                adapter.close()
        else:
            adapter.reconnect(physical_channel)
            if physical_channel is not None and action.logical_channel is not None:
                binding = self._binding_for(action.logical_channel)
                if binding is not None:
                    adapter.start_channel(binding.physical_channel, binding.channel_config())
        self.stats.link_actions += 1
        action_name = "断开" if action.action == LinkActionType.DISCONNECT else "恢复"
        self.logger(f"链路动作：{action.adapter_id} 已处理{action_name}。")

    def _binding_for(self, logical_channel: int) -> Optional[DeviceChannelBinding]:
        assert self._scenario is not None
        return self._scenario.find_binding(logical_channel)

    def _teardown_adapters(self) -> None:
        for adapter in self._adapters.values():
            try:
                adapter.close()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                self.logger(f"适配器关闭失败：{exc}")
