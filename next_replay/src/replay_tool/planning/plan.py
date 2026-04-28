from __future__ import annotations

import heapq
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from replay_tool.domain import ChannelBinding, DeviceConfig, Frame, ReplayScenario
from replay_tool.ports.trace import TraceReader


@dataclass(frozen=True)
class PlannedChannel:
    logical_channel: int
    device_id: str
    physical_channel: int
    binding: ChannelBinding


@dataclass(frozen=True)
class ReplayPlan:
    name: str
    frames: tuple[Frame, ...]
    devices: tuple[DeviceConfig, ...]
    channels: tuple[PlannedChannel, ...]
    loop: bool = False

    @property
    def total_ts_ns(self) -> int:
        return self.frames[-1].ts_ns if self.frames else 0

    def channel_for_logical(self, logical_channel: int) -> PlannedChannel:
        for channel in self.channels:
            if channel.logical_channel == logical_channel:
                return channel
        raise KeyError(logical_channel)


class ReplayPlanner:
    def __init__(self, trace_reader: TraceReader) -> None:
        self.trace_reader = trace_reader

    def compile(self, scenario: ReplayScenario, *, base_dir: str | Path = ".") -> ReplayPlan:
        base_path = Path(base_dir)
        traces_by_id = {item.id: item for item in scenario.traces}
        bindings_by_trace: dict[str, list[ChannelBinding]] = {}
        for binding in scenario.channels:
            bindings_by_trace.setdefault(binding.trace_id, []).append(binding)

        frame_groups: list[Sequence[Frame]] = []
        for trace_id, bindings in bindings_by_trace.items():
            trace = traces_by_id[trace_id]
            trace_path = Path(trace.path)
            if not trace_path.is_absolute():
                trace_path = base_path / trace_path
            trace_frames = self.trace_reader.read(str(trace_path))
            mapped_frames: list[Frame] = []
            for binding in bindings:
                mapped_frames.extend(self._map_frames(trace_frames, binding))
            mapped_frames.sort(key=lambda item: item.ts_ns)
            frame_groups.append(mapped_frames)

        frames = tuple(heapq.merge(*frame_groups, key=lambda item: item.ts_ns)) if frame_groups else ()
        channels = tuple(
            PlannedChannel(
                logical_channel=item.logical_channel,
                device_id=item.device_id,
                physical_channel=item.physical_channel,
                binding=item,
            )
            for item in scenario.channels
        )
        return ReplayPlan(
            name=scenario.name,
            frames=frames,
            devices=scenario.devices,
            channels=channels,
            loop=scenario.replay.loop,
        )

    def _map_frames(self, frames: Sequence[Frame], binding: ChannelBinding) -> list[Frame]:
        return [
            frame.clone(channel=binding.logical_channel)
            for frame in frames
            if frame.channel == binding.source_channel and frame.bus == binding.config.bus
        ]
