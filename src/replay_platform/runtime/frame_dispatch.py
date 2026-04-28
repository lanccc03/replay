from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from replay_platform.core import DeviceChannelBinding, FrameEvent, TimelineItem
from replay_platform.errors import ConfigurationError
from replay_platform.services.frame_enable import FrameEnableService
from replay_platform.services.signal_catalog import SignalOverrideService


@dataclass(frozen=True)
class PreparedFrame:
    adapter_id: str
    logical_channel: int
    physical_channel: int
    frame: FrameEvent


class FrameDispatchPreparer:
    def __init__(
        self,
        *,
        signal_overrides: SignalOverrideService,
        frame_enables: FrameEnableService,
        binding_for: Callable[[int], Optional[DeviceChannelBinding]],
        add_skipped_frames: Callable[[int], None],
    ) -> None:
        self.signal_overrides = signal_overrides
        self.frame_enables = frame_enables
        self.binding_for = binding_for
        self.add_skipped_frames = add_skipped_frames

    def enabled_frames(self, frames: Sequence[FrameEvent]) -> List[FrameEvent]:
        return [
            frame
            for frame in frames
            if self.frame_enables.is_enabled(frame.channel, frame.message_id)
        ]

    def prepare_enabled_frame(self, frame: FrameEvent) -> PreparedFrame:
        binding = self.binding_for(frame.channel)
        if binding is None:
            raise ConfigurationError(f"逻辑通道 {frame.channel} 未绑定设备。")
        mapped = self.signal_overrides.apply(frame)
        mapped = mapped.clone(channel=binding.physical_channel)
        return PreparedFrame(
            adapter_id=binding.adapter_id,
            logical_channel=frame.channel,
            physical_channel=binding.physical_channel,
            frame=mapped,
        )

    def prepare_frame_groups(self, frames: Sequence[FrameEvent]) -> Dict[str, List[PreparedFrame]]:
        frames_by_adapter: Dict[str, List[PreparedFrame]] = {}
        for frame in frames:
            if not self.frame_enables.is_enabled(frame.channel, frame.message_id):
                self.add_skipped_frames(1)
                continue
            prepared = self.prepare_enabled_frame(frame)
            frames_by_adapter.setdefault(prepared.adapter_id, []).append(prepared)
        return frames_by_adapter


def frame_batch_at(
    timeline: Sequence[TimelineItem],
    start_index: int,
    window_ns: int,
) -> List[FrameEvent]:
    if start_index >= len(timeline):
        return []
    first_item = timeline[start_index]
    if not isinstance(first_item, FrameEvent):
        return []
    batch = [first_item]
    window_end_ns = first_item.ts_ns + window_ns
    next_index = start_index + 1
    while next_index < len(timeline):
        item = timeline[next_index]
        if not isinstance(item, FrameEvent):
            break
        if item.ts_ns >= window_end_ns:
            break
        batch.append(item)
        next_index += 1
    return batch


__all__ = ("FrameDispatchPreparer", "PreparedFrame", "frame_batch_at")
