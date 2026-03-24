from __future__ import annotations

from typing import List, Optional, Sequence

from replay_platform.adapters.base import DeviceAdapter
from replay_platform.core import (
    AdapterCapabilities,
    AdapterHealth,
    ChannelConfig,
    ChannelDescriptor,
    DeviceDescriptor,
    FrameEvent,
)
from replay_platform.errors import AdapterOperationError


class TongxingDeviceAdapter(DeviceAdapter):
    """Interface placeholder for future Tongxing SDK integration."""

    def __init__(self, adapter_id: str, config: Optional[dict] = None) -> None:
        super().__init__(adapter_id)
        self.config = config or {}

    def _unsupported(self) -> None:
        raise AdapterOperationError(
            "V1 版本尚未实现同星适配器，请在不修改上层架构的前提下补充同星 SDK 绑定。"
        )

    def open(self) -> DeviceDescriptor:
        self._unsupported()
        return DeviceDescriptor(adapter_id=self.adapter_id, driver="tongxing", name="Tongxing")

    def close(self) -> None:
        self._unsupported()

    def enumerate_channels(self) -> Sequence[ChannelDescriptor]:
        self._unsupported()
        return []

    def start_channel(self, physical_channel: int, config: ChannelConfig) -> None:
        self._unsupported()

    def stop_channel(self, physical_channel: int) -> None:
        self._unsupported()

    def send(self, batch: Sequence[FrameEvent]) -> int:
        self._unsupported()
        return 0

    def read(self, limit: int = 256, timeout_ms: int = 0) -> List[FrameEvent]:
        self._unsupported()
        return []

    def health(self) -> AdapterHealth:
        self._unsupported()
        return AdapterHealth(False)

    def reconnect(self, physical_channel: Optional[int] = None) -> None:
        self._unsupported()

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(can=True, canfd=True, j1939=True)
