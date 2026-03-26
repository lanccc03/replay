from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union


class BusType(str, Enum):
    CAN = "CAN"
    CANFD = "CANFD"
    J1939 = "J1939"
    ETH = "ETH"


class ReplayState(str, Enum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


class ReplayLaunchSource(str, Enum):
    SCENARIO_BOUND = "scenario_bound"
    SELECTED_FALLBACK = "selected_fallback"


class ReplayLogLevel(str, Enum):
    WARNING = "warning"
    INFO = "info"
    DEBUG = "debug"


class ReplayFrameLogMode(str, Enum):
    OFF = "off"
    SAMPLED = "sampled"
    ALL = "all"


class LinkActionType(str, Enum):
    DISCONNECT = "DISCONNECT"
    RECONNECT = "RECONNECT"


class TimelineKind(str, Enum):
    FRAME = "frame"
    DIAGNOSTIC = "diagnostic"
    LINK = "link"


class DiagnosticTransport(str, Enum):
    CAN = "CAN"
    DOIP = "DOIP"


@dataclass
class AdapterCapabilities:
    can: bool = False
    canfd: bool = False
    j1939: bool = False
    merge_receive: bool = False
    queue_send: bool = False
    tx_timestamp: bool = False
    bus_usage: bool = False
    can_uds: bool = False
    doip: bool = False


@dataclass
class AdapterHealth:
    online: bool
    detail: str = ""
    per_channel: Dict[int, bool] = field(default_factory=dict)


@dataclass
class DeviceDescriptor:
    adapter_id: str
    driver: str
    name: str
    serial_number: str = ""
    channel_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelDescriptor:
    logical_channel: int
    physical_channel: int
    bus_type: BusType
    label: str


@dataclass
class ChannelConfig:
    bus_type: BusType
    nominal_baud: int = 500000
    data_baud: int = 2000000
    resistance_enabled: bool = True
    listen_only: bool = False
    tx_echo: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayLogConfig:
    level: ReplayLogLevel = ReplayLogLevel.INFO
    frame_mode: ReplayFrameLogMode = ReplayFrameLogMode.OFF
    frame_sample_rate: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.level, ReplayLogLevel):
            self.level = ReplayLogLevel(self.level)
        if not isinstance(self.frame_mode, ReplayFrameLogMode):
            self.frame_mode = ReplayFrameLogMode(self.frame_mode)
        self.frame_sample_rate = max(int(self.frame_sample_rate), 1)


@dataclass
class DeviceChannelBinding:
    adapter_id: str
    driver: str
    logical_channel: int
    physical_channel: int
    bus_type: BusType
    device_type: str
    device_index: int = 0
    sdk_root: str = "zlgcan_python_251211"
    nominal_baud: int = 500000
    data_baud: int = 2000000
    resistance_enabled: bool = True
    listen_only: bool = False
    tx_echo: bool = False
    merge_receive: bool = False
    network: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def channel_config(self) -> ChannelConfig:
        return ChannelConfig(
            bus_type=self.bus_type,
            nominal_baud=self.nominal_baud,
            data_baud=self.data_baud,
            resistance_enabled=self.resistance_enabled,
            listen_only=self.listen_only,
            tx_echo=self.tx_echo,
            extra=dict(self.network),
        )


@dataclass
class DatabaseBinding:
    logical_channel: int
    path: str
    format: str = "dbc"


@dataclass
class DiagnosticTarget:
    name: str
    transport: DiagnosticTransport
    adapter_id: str = ""
    logical_channel: int = 0
    tx_id: int = 0x7E0
    rx_id: int = 0x7E8
    host: str = ""
    port: int = 13400
    source_address: int = 0x0E00
    target_address: int = 0x0001
    activation_type: int = 0x00
    timeout_ms: int = 1000
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(order=True)
class FrameEvent:
    ts_ns: int
    bus_type: BusType
    channel: int
    message_id: int
    payload: bytes
    dlc: int
    flags: Dict[str, Any] = field(default_factory=dict, compare=False)
    source_file: str = field(default="", compare=False)
    metadata: Dict[str, Any] = field(default_factory=dict, compare=False)
    kind: TimelineKind = field(default=TimelineKind.FRAME, compare=False)

    def clone(self, **updates: Any) -> "FrameEvent":
        data = {
            "ts_ns": self.ts_ns,
            "bus_type": self.bus_type,
            "channel": self.channel,
            "message_id": self.message_id,
            "payload": bytes(self.payload),
            "dlc": self.dlc,
            "flags": dict(self.flags),
            "source_file": self.source_file,
            "metadata": dict(self.metadata),
            "kind": self.kind,
        }
        data.update(updates)
        return FrameEvent(**data)


@dataclass(order=True)
class DiagnosticAction:
    ts_ns: int
    target: str
    service_id: int
    payload: bytes = b""
    transport: DiagnosticTransport = DiagnosticTransport.CAN
    timeout_ms: int = 1000
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict, compare=False)
    kind: TimelineKind = field(default=TimelineKind.DIAGNOSTIC, compare=False)


@dataclass(order=True)
class LinkAction:
    ts_ns: int
    adapter_id: str
    action: LinkActionType
    logical_channel: Optional[int] = None
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict, compare=False)
    kind: TimelineKind = field(default=TimelineKind.LINK, compare=False)


TimelineItem = Union[FrameEvent, DiagnosticAction, LinkAction]


@dataclass
class SignalOverride:
    logical_channel: int
    message_id_or_pgn: int
    signal_name: str
    value: Any


@dataclass(frozen=True)
class FrameEnableRule:
    logical_channel: int
    message_id: int
    enabled: bool = True


@dataclass
class UdsRequest:
    service_id: int
    payload: bytes = b""
    timeout_ms: int = 1000


@dataclass
class UdsResponse:
    positive: bool
    service_id: int
    payload: bytes
    raw: bytes = b""
    negative_code: Optional[int] = None


@dataclass
class DtcRecord:
    code: str
    status: int
    status_flags: List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class ReplayStats:
    sent_frames: int = 0
    skipped_frames: int = 0
    diagnostic_actions: int = 0
    link_actions: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class ReplayRuntimeSnapshot:
    state: ReplayState = ReplayState.STOPPED
    current_ts_ns: int = 0
    total_ts_ns: int = 0
    timeline_index: int = 0
    timeline_size: int = 0
    current_item_kind: Optional[TimelineKind] = None
    current_source_file: str = ""
    adapter_health: Dict[str, AdapterHealth] = field(default_factory=dict)
    launch_source: Optional[ReplayLaunchSource] = None


@dataclass
class TraceFileRecord:
    trace_id: str
    name: str
    original_path: str
    library_path: str
    format: str
    imported_at: str
    event_count: int = 0
    start_ns: int = 0
    end_ns: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioSpec:
    scenario_id: str
    name: str
    trace_file_ids: List[str] = field(default_factory=list)
    bindings: List[DeviceChannelBinding] = field(default_factory=list)
    database_bindings: List[DatabaseBinding] = field(default_factory=list)
    signal_overrides: List[SignalOverride] = field(default_factory=list)
    diagnostic_targets: List[DiagnosticTarget] = field(default_factory=list)
    diagnostic_actions: List[DiagnosticAction] = field(default_factory=list)
    link_actions: List[LinkAction] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def timeline_items(self, frames: Sequence[FrameEvent]) -> List[TimelineItem]:
        items: List[TimelineItem] = list(frames)
        items.extend(self.diagnostic_actions)
        items.extend(self.link_actions)
        return sorted(items, key=lambda item: item.ts_ns)

    def find_binding(self, logical_channel: int) -> Optional[DeviceChannelBinding]:
        for binding in self.bindings:
            if binding.logical_channel == logical_channel:
                return binding
        return None

    def to_dict(self) -> Dict[str, Any]:
        return dataclass_to_jsonable(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ScenarioSpec":
        def parse_bus(value: Any) -> BusType:
            return value if isinstance(value, BusType) else BusType(value)

        def parse_transport(value: Any) -> DiagnosticTransport:
            return value if isinstance(value, DiagnosticTransport) else DiagnosticTransport(value)

        def parse_link_action(value: Any) -> LinkActionType:
            return value if isinstance(value, LinkActionType) else LinkActionType(value)

        bindings = [
            DeviceChannelBinding(
                adapter_id=item["adapter_id"],
                driver=item["driver"],
                logical_channel=int(item["logical_channel"]),
                physical_channel=int(item["physical_channel"]),
                bus_type=parse_bus(item["bus_type"]),
                device_type=item["device_type"],
                device_index=int(item.get("device_index", 0)),
                sdk_root=item.get("sdk_root", "zlgcan_python_251211"),
                nominal_baud=int(item.get("nominal_baud", 500000)),
                data_baud=int(item.get("data_baud", 2000000)),
                resistance_enabled=bool(item.get("resistance_enabled", True)),
                listen_only=bool(item.get("listen_only", False)),
                tx_echo=bool(item.get("tx_echo", False)),
                merge_receive=bool(item.get("merge_receive", False)),
                network=dict(item.get("network", {})),
                metadata=dict(item.get("metadata", {})),
            )
            for item in payload.get("bindings", [])
        ]
        database_bindings = [
            DatabaseBinding(
                logical_channel=int(item["logical_channel"]),
                path=item["path"],
                format=item.get("format", "dbc"),
            )
            for item in payload.get("database_bindings", [])
        ]
        signal_overrides = [
            SignalOverride(
                logical_channel=int(item["logical_channel"]),
                message_id_or_pgn=int(item["message_id_or_pgn"]),
                signal_name=item["signal_name"],
                value=item.get("value"),
            )
            for item in payload.get("signal_overrides", [])
        ]
        diagnostic_targets = [
            DiagnosticTarget(
                name=item["name"],
                transport=parse_transport(item["transport"]),
                adapter_id=item.get("adapter_id", ""),
                logical_channel=int(item.get("logical_channel", 0)),
                tx_id=int(item.get("tx_id", 0x7E0)),
                rx_id=int(item.get("rx_id", 0x7E8)),
                host=item.get("host", ""),
                port=int(item.get("port", 13400)),
                source_address=int(item.get("source_address", 0x0E00)),
                target_address=int(item.get("target_address", 0x0001)),
                activation_type=int(item.get("activation_type", 0x00)),
                timeout_ms=int(item.get("timeout_ms", 1000)),
                metadata=dict(item.get("metadata", {})),
            )
            for item in payload.get("diagnostic_targets", [])
        ]
        diagnostic_actions = [
            DiagnosticAction(
                ts_ns=int(item["ts_ns"]),
                target=item["target"],
                service_id=int(item["service_id"]),
                payload=bytes.fromhex(item["payload"]) if isinstance(item.get("payload"), str) else bytes(item.get("payload", b"")),
                transport=parse_transport(item.get("transport", DiagnosticTransport.CAN.value)),
                timeout_ms=int(item.get("timeout_ms", 1000)),
                description=item.get("description", ""),
                metadata=dict(item.get("metadata", {})),
            )
            for item in payload.get("diagnostic_actions", [])
        ]
        link_actions = [
            LinkAction(
                ts_ns=int(item["ts_ns"]),
                adapter_id=item["adapter_id"],
                action=parse_link_action(item["action"]),
                logical_channel=item.get("logical_channel"),
                description=item.get("description", ""),
                metadata=dict(item.get("metadata", {})),
            )
            for item in payload.get("link_actions", [])
        ]
        return cls(
            scenario_id=payload["scenario_id"],
            name=payload["name"],
            trace_file_ids=list(payload.get("trace_file_ids", [])),
            bindings=bindings,
            database_bindings=database_bindings,
            signal_overrides=signal_overrides,
            diagnostic_targets=diagnostic_targets,
            diagnostic_actions=diagnostic_actions,
            link_actions=link_actions,
            metadata=dict(payload.get("metadata", {})),
        )


def dataclass_to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if is_dataclass(value):
        return {key: dataclass_to_jsonable(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): dataclass_to_jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [dataclass_to_jsonable(item) for item in value]
    return value
