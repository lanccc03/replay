from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class BusType(str, Enum):
    CAN = "CAN"
    CANFD = "CANFD"


class ReplayState(str, Enum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


CANFD_PAYLOAD_LENGTHS_BY_DLC = {
    0x0: 0,
    0x1: 1,
    0x2: 2,
    0x3: 3,
    0x4: 4,
    0x5: 5,
    0x6: 6,
    0x7: 7,
    0x8: 8,
    0x9: 12,
    0xA: 16,
    0xB: 20,
    0xC: 24,
    0xD: 32,
    0xE: 48,
    0xF: 64,
}


def canfd_payload_length_to_dlc(payload_length: int) -> int:
    length = int(payload_length)
    if length < 0:
        raise ValueError("CANFD payload length cannot be negative.")
    if length <= 8:
        return length
    for dlc, allowed_length in CANFD_PAYLOAD_LENGTHS_BY_DLC.items():
        if allowed_length >= length:
            return dlc
    raise ValueError(f"CANFD payload length exceeds 64 bytes: {length}")


def canfd_payload_length_from_dlc(dlc: int) -> int:
    return CANFD_PAYLOAD_LENGTHS_BY_DLC[int(dlc)]


@dataclass(frozen=True)
class Frame:
    ts_ns: int
    bus: BusType
    channel: int
    message_id: int
    payload: bytes
    dlc: int
    extended: bool = False
    remote: bool = False
    brs: bool = False
    esi: bool = False
    direction: str = "Rx"
    source_file: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "ts_ns", int(self.ts_ns))
        object.__setattr__(self, "channel", int(self.channel))
        object.__setattr__(self, "message_id", int(self.message_id))
        object.__setattr__(self, "payload", bytes(self.payload))
        object.__setattr__(self, "dlc", int(self.dlc))
        if not isinstance(self.bus, BusType):
            object.__setattr__(self, "bus", BusType(self.bus))

    def clone(self, **updates: Any) -> "Frame":
        return replace(self, **updates)


@dataclass(frozen=True)
class ChannelConfig:
    bus: BusType
    nominal_baud: int = 500000
    data_baud: int = 2000000
    resistance_enabled: bool = True
    listen_only: bool = False
    tx_echo: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.bus, BusType):
            object.__setattr__(self, "bus", BusType(self.bus))
        object.__setattr__(self, "nominal_baud", int(self.nominal_baud))
        object.__setattr__(self, "data_baud", int(self.data_baud))


@dataclass(frozen=True)
class TraceConfig:
    id: str
    path: str


@dataclass(frozen=True)
class DeviceConfig:
    id: str
    driver: str
    application: str = "ReplayTool"
    sdk_root: str = "../TSMaster/Windows"
    device_type: str = "TC1014"
    device_index: int = 0
    project_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelBinding:
    logical_channel: int
    trace_id: str
    source_channel: int
    device_id: str
    physical_channel: int
    config: ChannelConfig


@dataclass(frozen=True)
class ReplayOptions:
    loop: bool = False


@dataclass(frozen=True)
class ReplayScenario:
    schema_version: int
    name: str
    traces: tuple[TraceConfig, ...]
    devices: tuple[DeviceConfig, ...]
    channels: tuple[ChannelBinding, ...]
    replay: ReplayOptions = ReplayOptions()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReplayScenario":
        if int(payload.get("schema_version", 0)) != 1:
            raise ValueError("Only scenario schema_version=1 is supported.")
        traces = tuple(
            TraceConfig(id=str(item["id"]), path=str(item["path"]))
            for item in payload.get("traces", [])
        )
        devices = tuple(
            DeviceConfig(
                id=str(item["id"]),
                driver=str(item["driver"]).lower(),
                application=str(item.get("application", "ReplayTool")),
                sdk_root=str(item.get("sdk_root", "../TSMaster/Windows")),
                device_type=str(item.get("device_type", "TC1014")),
                device_index=int(item.get("device_index", 0)),
                project_path=str(item.get("project_path", "")),
                metadata=dict(item.get("metadata", {})),
            )
            for item in payload.get("devices", [])
        )
        channels = tuple(
            ChannelBinding(
                logical_channel=int(item["logical_channel"]),
                trace_id=str(item["trace_id"]),
                source_channel=int(item["source_channel"]),
                device_id=str(item["device_id"]),
                physical_channel=int(item["physical_channel"]),
                config=ChannelConfig(
                    bus=BusType(item["bus"]),
                    nominal_baud=int(item.get("nominal_baud", 500000)),
                    data_baud=int(item.get("data_baud", 2000000)),
                    resistance_enabled=bool(item.get("resistance_enabled", True)),
                    listen_only=bool(item.get("listen_only", False)),
                    tx_echo=bool(item.get("tx_echo", False)),
                ),
            )
            for item in payload.get("channels", [])
        )
        scenario = cls(
            schema_version=1,
            name=str(payload["name"]),
            traces=traces,
            devices=devices,
            channels=channels,
            replay=ReplayOptions(loop=bool(payload.get("replay", {}).get("loop", False))),
        )
        scenario.validate()
        return scenario

    def validate(self) -> None:
        if not self.traces:
            raise ValueError("Scenario must define at least one trace.")
        if not self.devices:
            raise ValueError("Scenario must define at least one device.")
        if not self.channels:
            raise ValueError("Scenario must define at least one channel.")
        trace_ids = {item.id for item in self.traces}
        device_ids = {item.id for item in self.devices}
        for channel in self.channels:
            if channel.trace_id not in trace_ids:
                raise ValueError(f"Channel references unknown trace: {channel.trace_id}")
            if channel.device_id not in device_ids:
                raise ValueError(f"Channel references unknown device: {channel.device_id}")


@dataclass(frozen=True)
class DeviceCapabilities:
    can: bool = False
    canfd: bool = False
    async_send: bool = False
    fifo_read: bool = False


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    driver: str
    name: str
    serial_number: str = ""
    channel_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceHealth:
    online: bool
    detail: str = ""
    per_channel: dict[int, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplaySnapshot:
    state: ReplayState = ReplayState.STOPPED
    current_ts_ns: int = 0
    total_ts_ns: int = 0
    sent_frames: int = 0
    skipped_frames: int = 0
    errors: tuple[str, ...] = ()
    completed_loops: int = 0
