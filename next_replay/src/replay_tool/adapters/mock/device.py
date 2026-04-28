from __future__ import annotations

from typing import Sequence

from replay_tool.domain import (
    ChannelConfig,
    DeviceCapabilities,
    DeviceConfig,
    DeviceHealth,
    DeviceInfo,
    Frame,
)


class MockDevice:
    def __init__(self, config: DeviceConfig) -> None:
        self.config = config
        self.opened = False
        self.started_channels: dict[int, ChannelConfig] = {}
        self.sent_frames: list[Frame] = []
        self.rx_frames: list[Frame] = []

    def open(self) -> DeviceInfo:
        self.opened = True
        return DeviceInfo(
            id=self.config.id,
            driver="mock",
            name=self.config.metadata.get("name", "MockDevice"),
            channel_count=int(self.config.metadata.get("channel_count", 8)),
        )

    def close(self) -> None:
        self.opened = False
        self.started_channels.clear()

    def enumerate_channels(self) -> Sequence[int]:
        return tuple(range(int(self.config.metadata.get("channel_count", 8))))

    def start_channel(self, physical_channel: int, config: ChannelConfig) -> None:
        self.open()
        self.started_channels[int(physical_channel)] = config

    def stop_channel(self, physical_channel: int) -> None:
        self.started_channels.pop(int(physical_channel), None)

    def send(self, frames: Sequence[Frame]) -> int:
        for frame in frames:
            if int(frame.channel) not in self.started_channels:
                raise RuntimeError(f"Mock channel {frame.channel} is not started.")
            self.sent_frames.append(frame)
        return len(frames)

    def read(self, limit: int = 256, timeout_ms: int = 0) -> list[Frame]:
        _ = timeout_ms
        count = min(max(int(limit), 0), len(self.rx_frames))
        result = self.rx_frames[:count]
        del self.rx_frames[:count]
        return result

    def health(self) -> DeviceHealth:
        return DeviceHealth(
            online=self.opened,
            detail="Mock online." if self.opened else "Mock closed.",
            per_channel={channel: self.opened for channel in sorted(self.started_channels)},
        )

    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(can=True, canfd=True, async_send=True, fifo_read=True)
