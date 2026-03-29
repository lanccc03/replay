from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
import threading
import time
from typing import Callable, Deque, Dict, List, Optional, Sequence

from replay_platform.adapters.base import DeviceAdapter, DiagnosticClient
from replay_platform.core import (
    AdapterHealth,
    BusType,
    ChannelConfig,
    ChannelDescriptor,
    DeviceChannelBinding,
    DiagnosticAction,
    FrameEvent,
    LinkAction,
    LinkActionType,
    ReplayFrameLogMode,
    ReplayLaunchSource,
    ReplayLogConfig,
    ReplayLogLevel,
    ReplayRuntimeSnapshot,
    ReplayState,
    ReplayStats,
    ScenarioSpec,
    TimelineItem,
    TimelineKind,
    UdsRequest,
)
from replay_platform.errors import ConfigurationError
from replay_platform.services.frame_enable import FrameEnableService
from replay_platform.services.signal_catalog import SignalOverrideService


FRAME_SLICE_WINDOW_NS = 2_000_000
FRAME_QUEUE_LOOKAHEAD_NS = 20_000_000
FRAME_LOG_BUS_TYPES = frozenset({BusType.CAN, BusType.CANFD, BusType.J1939})
SCHEDULED_FRAME_BUS_TYPES = frozenset({BusType.CAN, BusType.J1939})


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
        frame_enables: Optional[FrameEnableService] = None,
        logger: Optional[Callable[[str], None]] = None,
        log_config: Optional[ReplayLogConfig] = None,
    ) -> None:
        self.signal_overrides = signal_overrides or SignalOverrideService()
        self.frame_enables = frame_enables or FrameEnableService()
        self.logger = logger or (lambda _message: None)
        self.log_config = log_config or ReplayLogConfig()
        self.state = ReplayState.STOPPED
        self.stats = ReplayStats()
        self._scenario: Optional[ScenarioSpec] = None
        self._timeline: List[TimelineItem] = []
        self._adapters: Dict[str, DeviceAdapter] = {}
        self._diagnostics: Dict[str, DiagnosticClient] = {}
        self._thread: Optional[threading.Thread] = None
        self._diagnostic_thread: Optional[threading.Thread] = None
        self._condition = threading.Condition()
        self._diagnostic_condition = threading.Condition()
        self._diagnostic_queue: Deque[DiagnosticAction] = deque()
        self._stop_requested = False
        self._diagnostic_stop_requested = False
        self._diagnostic_active = False
        self._base_perf_ns = 0
        self._pause_started_ns = 0
        self._timeline_index = 0
        self._stats_lock = threading.Lock()
        self._snapshot_lock = threading.Lock()
        self._frame_log_counts: Dict[str, int] = {}
        self._runtime_snapshot = ReplayRuntimeSnapshot()

    def configure(
        self,
        scenario: ScenarioSpec,
        frames: Sequence[FrameEvent],
        adapters: Dict[str, DeviceAdapter],
        diagnostics: Optional[Dict[str, DiagnosticClient]] = None,
        *,
        launch_source: Optional[ReplayLaunchSource] = None,
    ) -> None:
        self._scenario = scenario
        self._timeline = scenario.timeline_items(frames)
        self._adapters = adapters
        self._diagnostics = diagnostics or {}
        self._timeline_index = 0
        self.stats = ReplayStats()
        self._frame_log_counts.clear()
        with self._diagnostic_condition:
            self._diagnostic_queue.clear()
            self._diagnostic_stop_requested = False
            self._diagnostic_active = False
        self._runtime_snapshot = ReplayRuntimeSnapshot(
            state=self.state,
            total_ts_ns=self._timeline[-1].ts_ns if self._timeline else 0,
            timeline_index=0,
            timeline_size=len(self._timeline),
            adapter_health=self._copy_adapter_health_map(self._safe_adapter_health_snapshot()),
            launch_source=launch_source,
        )

    def start(self) -> None:
        if self._scenario is None:
            raise ConfigurationError("回放引擎尚未加载场景配置。")
        if self.state == ReplayState.RUNNING:
            return
        self._prepare_channels()
        self._stop_requested = False
        self._start_diagnostic_worker()
        self.state = ReplayState.RUNNING
        self._base_perf_ns = time.perf_counter_ns()
        self._update_runtime_snapshot(
            state=self.state,
            adapter_health=self._copy_adapter_health_map(self._safe_adapter_health_snapshot()),
        )
        self._thread = threading.Thread(target=self._run_loop, name="replay-engine", daemon=True)
        self._thread.start()
        self._log_info("回放已开始。")

    def pause(self) -> None:
        with self._condition:
            if self.state != ReplayState.RUNNING:
                return
            self.state = ReplayState.PAUSED
            self._pause_started_ns = time.perf_counter_ns()
            self._condition.notify_all()
        self._update_runtime_snapshot(
            state=self.state,
            adapter_health=self._copy_adapter_health_map(self._safe_adapter_health_snapshot()),
        )
        self._log_info("回放已暂停。")

    def resume(self) -> None:
        with self._condition:
            if self.state != ReplayState.PAUSED:
                return
            now = time.perf_counter_ns()
            self._base_perf_ns += now - self._pause_started_ns
            self.state = ReplayState.RUNNING
            self._condition.notify_all()
        self._update_runtime_snapshot(
            state=self.state,
            adapter_health=self._copy_adapter_health_map(self._safe_adapter_health_snapshot()),
        )
        self._log_info("回放已继续。")

    def stop(self) -> None:
        with self._condition:
            self._stop_requested = True
            self.state = ReplayState.STOPPED
            self.signal_overrides.clear_all()
            self._condition.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._stop_diagnostic_worker()
        self._teardown_adapters()
        self._timeline_index = 0
        self._frame_log_counts.clear()
        self._update_runtime_snapshot(
            state=self.state,
            timeline_index=0,
            current_item_kind=None,
            current_source_file="",
            adapter_health=self._copy_adapter_health_map(self._safe_adapter_health_snapshot()),
        )
        self._log_info("回放已停止。")

    def snapshot(self) -> ReplayRuntimeSnapshot:
        with self._snapshot_lock:
            snapshot = self._runtime_snapshot
            return ReplayRuntimeSnapshot(
                state=snapshot.state,
                current_ts_ns=snapshot.current_ts_ns,
                total_ts_ns=snapshot.total_ts_ns,
                timeline_index=snapshot.timeline_index,
                timeline_size=snapshot.timeline_size,
                current_item_kind=snapshot.current_item_kind,
                current_source_file=snapshot.current_source_file,
                adapter_health=self._copy_adapter_health_map(snapshot.adapter_health),
                launch_source=snapshot.launch_source,
            )

    def seek_to_start(self) -> None:
        if self.state == ReplayState.RUNNING:
            raise ConfigurationError("请先停止或暂停回放，再回到起点。")
        self._timeline_index = 0
        self._base_perf_ns = time.perf_counter_ns()
        self._log_info("回放位置已重置。")

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
        available_channels = {descriptor.physical_channel for descriptor in descriptors}
        start_plan: Dict[int, ChannelConfig] = {}
        for binding in bindings:
            if available_channels and binding.physical_channel not in available_channels:
                raise ConfigurationError(
                    f"适配器通道 {binding.physical_channel} 不存在，无法绑定逻辑通道 {binding.logical_channel}。"
                )
            config = binding.channel_config()
            existing = start_plan.get(binding.physical_channel)
            if existing is None:
                start_plan[binding.physical_channel] = config
                continue
            if existing != config:
                raise ConfigurationError(
                    f"物理通道 {binding.physical_channel} 存在冲突的启动配置。"
                )
        return dict(sorted(start_plan.items()))

    def _run_loop(self) -> None:
        while True:
            with self._condition:
                if self._stop_requested:
                    return
                if self.state == ReplayState.PAUSED:
                    self._condition.wait(timeout=0.1)
                    continue
            if self._timeline_index >= len(self._timeline):
                self._wait_for_diagnostics_idle()
                if self._stop_requested:
                    return
                self.state = ReplayState.STOPPED
                self._teardown_adapters()
                self._update_runtime_snapshot(
                    state=self.state,
                    current_ts_ns=self._timeline[-1].ts_ns if self._timeline else 0,
                    timeline_index=len(self._timeline),
                    adapter_health=self._copy_adapter_health_map(self._safe_adapter_health_snapshot()),
                )
                self._log_info("回放已完成。")
                return
            frame_batch = self._frame_batch_at(self._timeline_index)
            item = frame_batch[0] if frame_batch else self._timeline[self._timeline_index]
            advance_count = len(frame_batch) if frame_batch else 1
            self._update_runtime_snapshot_for_item(item, self._timeline_index)
            target_ns = self._base_perf_ns + item.ts_ns
            now_ns = time.perf_counter_ns()
            if frame_batch and self._should_schedule_frame_batch(frame_batch, target_ns, now_ns):
                try:
                    self._dispatch_frame_batch(frame_batch, scheduled=True)
                except Exception as exc:  # pragma: no cover - defensive runtime logging
                    self._record_error(str(exc))
                    self._log_warning(f"回放异常：{exc}")
                finally:
                    self._timeline_index += advance_count
                continue
            if now_ns < target_ns:
                self._sleep_until(target_ns)
                continue
            try:
                if frame_batch:
                    self._dispatch_frame_batch(frame_batch)
                else:
                    self._dispatch(item)
            except Exception as exc:  # pragma: no cover - defensive runtime logging
                self._record_error(str(exc))
                self._log_warning(f"回放异常：{exc}")
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
            self._enqueue_diagnostic(item)
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

    def _should_schedule_frame_batch(
        self,
        frames: Sequence[FrameEvent],
        target_ns: int,
        now_ns: int,
    ) -> bool:
        enabled_frames = self._enabled_frames(frames)
        if not enabled_frames or target_ns <= now_ns or target_ns - now_ns > FRAME_QUEUE_LOOKAHEAD_NS:
            return False
        checked_adapters: set[str] = set()
        for frame in enabled_frames:
            if frame.bus_type not in SCHEDULED_FRAME_BUS_TYPES:
                return False
            binding = self._binding_for(frame.channel)
            if binding is None:
                raise ConfigurationError(f"逻辑通道 {frame.channel} 未绑定设备。")
            if binding.adapter_id in checked_adapters:
                continue
            adapter = self._adapters.get(binding.adapter_id)
            if adapter is None:
                raise ConfigurationError(f"适配器 {binding.adapter_id} 未配置。")
            if not adapter.capabilities().queue_send:
                return False
            checked_adapters.add(binding.adapter_id)
        return True

    def _dispatch_frame_batch(self, frames: Sequence[FrameEvent], scheduled: bool = False) -> None:
        self._send_prepared_frames(self._prepare_frame_groups(frames), scheduled=scheduled)

    def _enabled_frames(self, frames: Sequence[FrameEvent]) -> List[FrameEvent]:
        return [
            frame
            for frame in frames
            if self.frame_enables.is_enabled(frame.channel, frame.message_id)
        ]

    def _prepare_frame_groups(self, frames: Sequence[FrameEvent]) -> Dict[str, List[PreparedFrame]]:
        frames_by_adapter: Dict[str, List[PreparedFrame]] = {}
        for frame in frames:
            if not self.frame_enables.is_enabled(frame.channel, frame.message_id):
                self._add_skipped_frames(1)
                continue
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
        return frames_by_adapter

    def _send_prepared_frames(
        self,
        frames_by_adapter: Dict[str, List[PreparedFrame]],
        scheduled: bool = False,
    ) -> None:
        for adapter_id, adapter_frames in frames_by_adapter.items():
            adapter = self._adapters.get(adapter_id)
            if adapter is None:
                raise ConfigurationError(f"适配器 {adapter_id} 未配置。")
            batch = [item.frame for item in adapter_frames]
            if scheduled:
                sent = int(adapter.send_scheduled(batch, self._base_perf_ns) or 0)
            else:
                sent = int(adapter.send(batch) or 0)
            sent_count = max(0, min(sent, len(adapter_frames)))
            self._add_sent_frames(sent_count)
            self._add_skipped_frames(len(adapter_frames) - sent_count)
            for item in adapter_frames[:sent_count]:
                self._log_sent_frame(item)
            if sent_count < len(adapter_frames):
                self._log_warning(
                    f"回放帧发送未完成：适配器={adapter_id} 已发 {sent_count}/{len(adapter_frames)}"
                )

    def _enqueue_diagnostic(self, action: DiagnosticAction) -> None:
        with self._diagnostic_condition:
            self._diagnostic_queue.append(action)
            self._diagnostic_condition.notify_all()

    def _dispatch_diagnostic(self, action: DiagnosticAction) -> None:
        client = self._diagnostics.get(action.target)
        if client is None:
            raise ConfigurationError(f'未找到名为 "{action.target}" 的诊断目标。')
        response = client.request(UdsRequest(action.service_id, action.payload, action.timeout_ms))
        self._increment_diagnostic_actions()
        self._log_info(
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
        self._increment_link_actions()
        action_name = "断开" if action.action == LinkActionType.DISCONNECT else "恢复"
        self._log_info(f"链路动作：{action.adapter_id} 已处理{action_name}。")

    def _start_diagnostic_worker(self) -> None:
        with self._diagnostic_condition:
            self._diagnostic_queue.clear()
            self._diagnostic_stop_requested = False
            self._diagnostic_active = False
        if not self._diagnostics:
            self._diagnostic_thread = None
            return
        self._diagnostic_thread = threading.Thread(
            target=self._diagnostic_loop,
            name="replay-diagnostics",
            daemon=True,
        )
        self._diagnostic_thread.start()

    def _stop_diagnostic_worker(self) -> None:
        pending = 0
        with self._diagnostic_condition:
            pending = len(self._diagnostic_queue)
            self._diagnostic_queue.clear()
            self._diagnostic_stop_requested = True
            self._diagnostic_condition.notify_all()
        if pending:
            self._log_warning(f"诊断队列已取消 {pending} 条未执行动作。")
        if self._diagnostic_thread and self._diagnostic_thread.is_alive():
            self._diagnostic_thread.join(timeout=2.0)
        self._diagnostic_thread = None

    def _diagnostic_loop(self) -> None:
        while True:
            with self._diagnostic_condition:
                while not self._diagnostic_queue and not self._diagnostic_stop_requested:
                    self._diagnostic_condition.wait(timeout=0.1)
                if self._diagnostic_stop_requested and not self._diagnostic_queue:
                    return
                action = self._diagnostic_queue.popleft()
                self._diagnostic_active = True
            try:
                self._dispatch_diagnostic(action)
            except Exception as exc:  # pragma: no cover - defensive runtime logging
                self._record_error(str(exc))
                self._log_warning(f"诊断异常：{exc}")
            finally:
                with self._diagnostic_condition:
                    self._diagnostic_active = False
                    self._diagnostic_condition.notify_all()

    def _wait_for_diagnostics_idle(self) -> None:
        while True:
            with self._diagnostic_condition:
                if (not self._diagnostic_queue and not self._diagnostic_active) or self._stop_requested:
                    return
            time.sleep(0.01)

    def _binding_for(self, logical_channel: int) -> Optional[DeviceChannelBinding]:
        assert self._scenario is not None
        return self._scenario.find_binding(logical_channel)

    def _log_sent_frame(self, item: PreparedFrame) -> None:
        if not self._should_log_frame(item):
            return
        self._log_debug(self._format_sent_frame_log(item))

    def _should_log_frame(self, item: PreparedFrame) -> bool:
        if item.frame.bus_type not in FRAME_LOG_BUS_TYPES:
            return False
        if not self._should_emit(ReplayLogLevel.DEBUG):
            return False
        if self.log_config.frame_mode == ReplayFrameLogMode.OFF:
            return False
        count = self._frame_log_counts.get(item.adapter_id, 0) + 1
        self._frame_log_counts[item.adapter_id] = count
        if self.log_config.frame_mode == ReplayFrameLogMode.ALL:
            return True
        return count % self.log_config.frame_sample_rate == 0

    def _format_sent_frame_log(self, item: PreparedFrame) -> str:
        return (
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
                self._log_warning(f"适配器关闭失败：{exc}")

    def _should_emit(self, level: ReplayLogLevel) -> bool:
        return self.log_config.allows(level)

    def _log_warning(self, message: str) -> None:
        if self._should_emit(ReplayLogLevel.WARNING):
            self.logger(message)

    def _log_info(self, message: str) -> None:
        if self._should_emit(ReplayLogLevel.INFO):
            self.logger(message)

    def _log_debug(self, message: str) -> None:
        if self._should_emit(ReplayLogLevel.DEBUG):
            self.logger(message)

    def _add_sent_frames(self, count: int) -> None:
        with self._stats_lock:
            self.stats.sent_frames += count

    def _add_skipped_frames(self, count: int) -> None:
        with self._stats_lock:
            self.stats.skipped_frames += count

    def _increment_diagnostic_actions(self) -> None:
        with self._stats_lock:
            self.stats.diagnostic_actions += 1

    def _increment_link_actions(self) -> None:
        with self._stats_lock:
            self.stats.link_actions += 1

    def _record_error(self, message: str) -> None:
        with self._stats_lock:
            self.stats.errors.append(message)

    def _update_runtime_snapshot(self, **updates) -> None:
        with self._snapshot_lock:
            self._runtime_snapshot = replace(self._runtime_snapshot, **updates)

    def _update_runtime_snapshot_for_item(self, item: TimelineItem, timeline_index: int) -> None:
        current_source = ""
        if isinstance(item, FrameEvent):
            current_source = Path(item.source_file).name if item.source_file else ""
        elif isinstance(item, DiagnosticAction):
            current_source = "诊断动作"
        elif isinstance(item, LinkAction):
            current_source = "链路动作"
        self._update_runtime_snapshot(
            state=self.state,
            current_ts_ns=item.ts_ns,
            timeline_index=timeline_index,
            current_item_kind=item.kind,
            current_source_file=current_source,
            adapter_health=self._copy_adapter_health_map(self._safe_adapter_health_snapshot()),
        )

    def _safe_adapter_health_snapshot(self) -> Dict[str, AdapterHealth]:
        health_map: Dict[str, AdapterHealth] = {}
        for adapter_id, adapter in self._adapters.items():
            try:
                health = adapter.health()
            except Exception as exc:  # pragma: no cover - defensive snapshot collection
                health = AdapterHealth(online=False, detail=f"健康检查失败：{exc}")
            health_map[adapter_id] = health
        return health_map

    @staticmethod
    def _copy_adapter_health_map(health_map: Dict[str, AdapterHealth]) -> Dict[str, AdapterHealth]:
        return {
            adapter_id: AdapterHealth(
                online=health.online,
                detail=health.detail,
                per_channel=dict(health.per_channel),
            )
            for adapter_id, health in health_map.items()
        }
