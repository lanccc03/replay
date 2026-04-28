from __future__ import annotations

from typing import Protocol, Sequence

from replay_tool.domain import (
    ChannelConfig,
    DeviceCapabilities,
    DeviceHealth,
    DeviceInfo,
    Frame,
)


class BusDevice(Protocol):
    def open(self) -> DeviceInfo:
        ...

    def close(self) -> None:
        ...

    def enumerate_channels(self) -> Sequence[int]:
        ...

    def start_channel(self, physical_channel: int, config: ChannelConfig) -> None:
        ...

    def stop_channel(self, physical_channel: int) -> None:
        ...

    def send(self, frames: Sequence[Frame]) -> int:
        ...

    def read(self, limit: int = 256, timeout_ms: int = 0) -> list[Frame]:
        ...

    def health(self) -> DeviceHealth:
        ...

    def capabilities(self) -> DeviceCapabilities:
        ...
