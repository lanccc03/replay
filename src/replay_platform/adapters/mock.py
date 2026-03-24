from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional, Sequence

from replay_platform.adapters.base import DeviceAdapter
from replay_platform.core import (
    AdapterCapabilities,
    AdapterHealth,
    BusType,
    ChannelConfig,
    ChannelDescriptor,
    DeviceDescriptor,
    FrameEvent,
)


class MockDeviceAdapter(DeviceAdapter):
    def __init__(self, adapter_id: str = "mock", channel_count: int = 4) -> None:
        super().__init__(adapter_id)
        self._descriptor = DeviceDescriptor(
            adapter_id=adapter_id,
            driver="mock",
            name="Mock Device Adapter",
            channel_count=channel_count,
        )
        self._rx_queue: Deque[FrameEvent] = deque()
        self._started_channels: Dict[int, ChannelConfig] = {}
        self.sent_frames: List[FrameEvent] = []
        self.reconnect_count = 0
        self.open_count = 0

    def open(self) -> DeviceDescriptor:
        self.open_count += 1
        return self._descriptor

    def close(self) -> None:
        self._started_channels.clear()

    def enumerate_channels(self) -> Sequence[ChannelDescriptor]:
        return [
            ChannelDescriptor(
                logical_channel=index,
                physical_channel=index,
                bus_type=BusType.CANFD,
                label=f"Mock CH{index}",
            )
            for index in range(self._descriptor.channel_count)
        ]

    def start_channel(self, physical_channel: int, config: ChannelConfig) -> None:
        self._started_channels[physical_channel] = config

    def stop_channel(self, physical_channel: int) -> None:
        self._started_channels.pop(physical_channel, None)

    def send(self, batch: Sequence[FrameEvent]) -> int:
        self.sent_frames.extend(batch)
        return len(batch)

    def read(self, limit: int = 256, timeout_ms: int = 0) -> List[FrameEvent]:
        items: List[FrameEvent] = []
        while self._rx_queue and len(items) < limit:
            items.append(self._rx_queue.popleft())
        return items

    def enqueue_rx(self, *events: FrameEvent) -> None:
        self._rx_queue.extend(events)

    def health(self) -> AdapterHealth:
        return AdapterHealth(
            online=True,
            detail="mock-ok",
            per_channel={channel: True for channel in self._started_channels},
        )

    def reconnect(self, physical_channel: Optional[int] = None) -> None:
        self.reconnect_count += 1

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            can=True,
            canfd=True,
            j1939=True,
            merge_receive=True,
            queue_send=True,
            tx_timestamp=True,
            bus_usage=True,
            can_uds=True,
        )

