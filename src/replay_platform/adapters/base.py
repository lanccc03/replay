from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

from replay_platform.core import (
    AdapterCapabilities,
    AdapterHealth,
    ChannelConfig,
    ChannelDescriptor,
    DeviceDescriptor,
    FrameEvent,
    UdsRequest,
    UdsResponse,
)


class DeviceAdapter(ABC):
    def __init__(self, adapter_id: str) -> None:
        self.adapter_id = adapter_id

    @abstractmethod
    def open(self) -> DeviceDescriptor:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def enumerate_channels(self) -> Sequence[ChannelDescriptor]:
        raise NotImplementedError

    @abstractmethod
    def start_channel(self, physical_channel: int, config: ChannelConfig) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop_channel(self, physical_channel: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def send(self, batch: Sequence[FrameEvent]) -> int:
        raise NotImplementedError

    def send_scheduled(self, batch: Sequence[FrameEvent], enqueue_base_ns: int) -> int:
        return self.send(batch)

    @abstractmethod
    def read(self, limit: int = 256, timeout_ms: int = 0) -> List[FrameEvent]:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> AdapterHealth:
        raise NotImplementedError

    @abstractmethod
    def reconnect(self, physical_channel: Optional[int] = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def capabilities(self) -> AdapterCapabilities:
        raise NotImplementedError


class DiagnosticClient(ABC):
    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def request(self, request: UdsRequest) -> UdsResponse:
        raise NotImplementedError

    @abstractmethod
    def read_dtc(self) -> List[object]:
        raise NotImplementedError

    @abstractmethod
    def clear_dtc(self, group: int = 0xFFFFFF) -> UdsResponse:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def reconnect(self) -> None:
        raise NotImplementedError
