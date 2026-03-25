from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence

from replay_platform.adapters.base import DeviceAdapter, DiagnosticClient
from replay_platform.core import (
    BusType,
    ChannelConfig,
    ChannelDescriptor,
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
from replay_platform.errors import ConfigurationError
from replay_platform.services.signal_catalog import SignalOverrideService


FRAME_SLICE_WINDOW_NS = 2_000_000
FRAME_LOG_BUS_TYPES = frozenset({BusType.CAN, BusType.CANFD, BusType.J1939})


@dataclass(frozen=True)
class PreparedFrame:
    adapter_id: str
    logical_channel: int
    physical_channel: int
    frame: FrameEvent


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
        bindings_by_adapter: Dict[str, List[DeviceChannelBinding]] = {}
        for binding in self._scenario.bindings:
            bindings_by_adapter.setdefault(binding.adapter_id, []).append(binding)
        for adapter_id, bindings in bindings_by_adapter.items():
            adapter = self._adapters.get(adapter_id)
            if adapter is None:
                raise ConfigurationError(f"适配器 {adapter_id} 未配置。")
            adapter.open()
            for physical_channel, config in self._channel_start_plan(bindings, adapter.enumerate_channels()).items():
                adapter.start_channel(physical_channel, config)

    def _channel_start_plan(
        self,
        bindings: Sequence[DeviceChannelBinding],
        descriptors: Sequence[ChannelDescriptor],
    ) -> Dict[int, ChannelConfig]:
        if not bindings:
            return {}
        binding_configs = {
            binding.physical_channel: binding.channel_config()
            for binding in bindings
        }
        descriptor_bus_types = {
            descriptor.physical_channel: descriptor.bus_type
            for descriptor in descriptors
        }
        fallback_binding = bindings[0]
        physical_channels = set(binding_configs)
        physical_channels.update(descriptor_bus_types)
        start_plan: Dict[int, ChannelConfig] = {}
        for physical_channel in sorted(physical_channels):
            config = binding_configs.get(physical_channel)
            if config is not None:
                start_plan[physical_channel] = config
                continue
            start_plan[physical_channel] = self._fallback_channel_config(
                fallback_binding,
                descriptor_bus_types.get(physical_channel),
            )
        return start_plan

    @staticmethod
    def _fallback_channel_config(
        binding: DeviceChannelBinding,
        bus_type: Optional[BusType],
    ) -> ChannelConfig:
        config = binding.channel_config()
        if bus_type is None or bus_type == config.bus_type:
            return config
        return ChannelConfig(
            bus_type=bus_type,
            nominal_baud=config.nominal_baud,
            data_baud=config.data_baud,
            resistance_enabled=config.resistance_enabled,
            listen_only=config.listen_only,
            tx_echo=config.tx_echo,
            extra=dict(config.extra),
        )

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
            frame_batch = self._frame_batch_at(self._timeline_index)
            item = frame_batch[0] if frame_batch else self._timeline[self._timeline_index]
            advance_count = len(frame_batch) if frame_batch else 1
            target_ns = self._base_perf_ns + item.ts_ns
            now_ns = time.perf_counter_ns()
            if now_ns < target_ns:
                self._sleep_until(target_ns)
                continue
            try:
                if frame_batch:
                    self._dispatch_frame_batch(frame_batch)
                else:
                    self._dispatch(item)
            except Exception as exc:  # pragma: no cover - defensive runtime logging
                self.stats.errors.append(str(exc))
                self.logger(f"回放异常：{exc}")
            finally:
                self._timeline_index += advance_count

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
            self._dispatch_frame_batch([item])
            return
        if isinstance(item, DiagnosticAction):
            self._dispatch_diagnostic(item)
            return
        if isinstance(item, LinkAction):
            self._dispatch_link(item)
            return
        raise ConfigurationError(f"不支持的时间轴项目：{item!r}")

    def _frame_batch_at(self, start_index: int) -> List[FrameEvent]:
        if start_index >= len(self._timeline):
            return []
        first_item = self._timeline[start_index]
        if not isinstance(first_item, FrameEvent):
            return []
        batch = [first_item]
        window_end_ns = first_item.ts_ns + FRAME_SLICE_WINDOW_NS
        next_index = start_index + 1
        while next_index < len(self._timeline):
            item = self._timeline[next_index]
            if not isinstance(item, FrameEvent):
                break
            if item.ts_ns >= window_end_ns:
                break
            batch.append(item)
            next_index += 1
        return batch

    def _dispatch_frame_batch(self, frames: Sequence[FrameEvent]) -> None:
        frames_by_adapter: Dict[str, List[PreparedFrame]] = {}
        for frame in frames:
            binding = self._binding_for(frame.channel)
            if binding is None:
                raise ConfigurationError(f"逻辑通道 {frame.channel} 未绑定设备。")
            mapped = self.signal_overrides.apply(frame)
            mapped = mapped.clone(channel=binding.physical_channel)
            frames_by_adapter.setdefault(binding.adapter_id, []).append(
                PreparedFrame(
                    adapter_id=binding.adapter_id,
                    logical_channel=frame.channel,
                    physical_channel=binding.physical_channel,
                    frame=mapped,
                )
            )
        for adapter_id, adapter_frames in frames_by_adapter.items():
            adapter = self._adapters.get(adapter_id)
            if adapter is None:
                raise ConfigurationError(f"适配器 {adapter_id} 未配置。")
            sent = int(adapter.send([item.frame for item in adapter_frames]) or 0)
            sent_count = max(0, min(sent, len(adapter_frames)))
            self.stats.sent_frames += sent_count
            self.stats.skipped_frames += len(adapter_frames) - sent_count
            for item in adapter_frames[:sent_count]:
                self._log_sent_frame(item)
            if sent_count < len(adapter_frames):
                self.logger(
                    f"回放帧发送未完成：适配器={adapter_id} 已发 {sent_count}/{len(adapter_frames)}"
                )

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

    def _log_sent_frame(self, item: PreparedFrame) -> None:
        if item.frame.bus_type not in FRAME_LOG_BUS_TYPES:
            return
        self.logger(
            "回放帧 [{bus}] t={ts_ms:.3f}ms 适配器={adapter} 逻辑通道={logical} "
            "物理通道={physical} ID=0x{id:X} DLC={dlc} DATA={data}".format(
                bus=item.frame.bus_type.value,
                ts_ms=item.frame.ts_ns / 1_000_000,
                adapter=item.adapter_id,
                logical=item.logical_channel,
                physical=item.physical_channel,
                id=item.frame.message_id,
                dlc=item.frame.dlc,
                data=item.frame.payload.hex().upper(),
            )
        )

    def _teardown_adapters(self) -> None:
        for adapter in self._adapters.values():
            try:
                adapter.close()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                self.logger(f"适配器关闭失败：{exc}")
