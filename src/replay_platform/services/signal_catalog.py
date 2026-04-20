from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from replay_platform.core import BusType, FrameEvent, SignalOverride, canfd_payload_length_to_dlc
from replay_platform.errors import DependencyUnavailableError


ALIAS_KEYWORDS = {
    "vehicle_speed": ["vehicle_speed", "veh_speed", "speed", "spd", "v_vehicle"],
    "gear": ["gear", "gear_position", "shifter", "shift"],
    "steering_angle": ["steering_angle", "steer_angle", "steeringwheelangle", "sw_angle"],
    "brake": ["brake", "brk", "pedal_brake", "brake_pedal"],
    "light": ["light", "lamp", "turn", "beam", "hazard"],
}


class MessageCodec(Protocol):
    def message_ids(self) -> List[int]:
        ...

    def supports(self, message_id: int) -> bool:
        ...

    def decode(self, message_id: int, payload: bytes) -> Dict[str, Any]:
        ...

    def encode(self, message_id: int, values: Dict[str, Any], base_payload: bytes) -> bytes:
        ...

    def signal_names(self, message_id: int) -> List[str]:
        ...

    def message_name(self, message_id: int) -> str:
        ...

    def messages(self) -> List["MessageCatalogEntry"]:
        ...

    def signals(self, message_id: int) -> List["SignalCatalogEntry"]:
        ...


@dataclass
class StaticMessageDefinition:
    name: str
    signal_bytes: Dict[str, int]
    signal_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class MessageCatalogEntry:
    message_id: int
    message_name: str


@dataclass(frozen=True)
class SignalCatalogEntry:
    message_id: int
    signal_name: str
    unit: str = ""
    minimum: Any = None
    maximum: Any = None
    choices: Dict[int, str] = field(default_factory=dict)


class StaticMessageCodec:
    """Simple byte-indexed codec used by tests and demos without cantools."""

    def __init__(self, definitions: Dict[int, StaticMessageDefinition]) -> None:
        self.definitions = definitions

    def supports(self, message_id: int) -> bool:
        return message_id in self.definitions

    def message_ids(self) -> List[int]:
        return sorted(self.definitions)

    def decode(self, message_id: int, payload: bytes) -> Dict[str, Any]:
        definition = self.definitions[message_id]
        return {
            signal_name: payload[byte_index]
            for signal_name, byte_index in definition.signal_bytes.items()
            if byte_index < len(payload)
        }

    def encode(self, message_id: int, values: Dict[str, Any], base_payload: bytes) -> bytes:
        definition = self.definitions[message_id]
        data = bytearray(base_payload)
        for signal_name, value in values.items():
            if signal_name in definition.signal_bytes:
                index = definition.signal_bytes[signal_name]
                if index >= len(data):
                    data.extend(b"\x00" * (index - len(data) + 1))
                data[index] = int(value) & 0xFF
        return bytes(data)

    def signal_names(self, message_id: int) -> List[str]:
        return list(self.definitions[message_id].signal_bytes)

    def message_name(self, message_id: int) -> str:
        return self.definitions[message_id].name

    def messages(self) -> List[MessageCatalogEntry]:
        return [
            MessageCatalogEntry(message_id=message_id, message_name=self.definitions[message_id].name)
            for message_id in self.message_ids()
        ]

    def signals(self, message_id: int) -> List[SignalCatalogEntry]:
        definition = self.definitions[message_id]
        entries: List[SignalCatalogEntry] = []
        for signal_name in definition.signal_bytes:
            metadata = dict(definition.signal_metadata.get(signal_name, {}))
            entries.append(
                SignalCatalogEntry(
                    message_id=message_id,
                    signal_name=signal_name,
                    unit=str(metadata.get("unit", "") or ""),
                    minimum=metadata.get("minimum"),
                    maximum=metadata.get("maximum"),
                    choices=_normalize_choices(metadata.get("choices")),
                )
            )
        return entries


class CantoolsSignalCatalog:
    def __init__(self, database: Any) -> None:
        self.database = database
        self._messages = {message.frame_id: message for message in database.messages}

    @classmethod
    def from_file(cls, path: str, *, format: str = "dbc") -> "CantoolsSignalCatalog":
        normalized_format = _normalize_database_format(format)
        try:
            import cantools  # type: ignore
        except ModuleNotFoundError as exc:
            raise DependencyUnavailableError(
                "加载 DBC/J1939 数据库需要安装 cantools。"
            ) from exc
        database = cantools.database.load_file(path, database_format=normalized_format)
        return cls(database)

    def supports(self, message_id: int) -> bool:
        return message_id in self._messages

    def message_ids(self) -> List[int]:
        return sorted(self._messages)

    def decode(self, message_id: int, payload: bytes) -> Dict[str, Any]:
        message = self._messages[message_id]
        return dict(message.decode(payload, decode_choices=False, scaling=True))

    def encode(self, message_id: int, values: Dict[str, Any], base_payload: bytes) -> bytes:
        message = self._messages[message_id]
        existing = dict(message.decode(base_payload, decode_choices=False, scaling=True))
        existing.update(values)
        return bytes(message.encode(existing, scaling=True, padding=True))

    def signal_names(self, message_id: int) -> List[str]:
        return [signal.name for signal in self._messages[message_id].signals]

    def message_name(self, message_id: int) -> str:
        return self._messages[message_id].name

    def messages(self) -> List[MessageCatalogEntry]:
        return [
            MessageCatalogEntry(message_id=message_id, message_name=self._messages[message_id].name)
            for message_id in self.message_ids()
        ]

    def signals(self, message_id: int) -> List[SignalCatalogEntry]:
        message = self._messages[message_id]
        return [
            SignalCatalogEntry(
                message_id=message_id,
                signal_name=signal.name,
                unit=str(getattr(signal, "unit", "") or ""),
                minimum=getattr(signal, "minimum", None),
                maximum=getattr(signal, "maximum", None),
                choices=_normalize_choices(getattr(signal, "choices", None)),
            )
            for signal in message.signals
        ]


class SignalOverrideService:
    def __init__(self) -> None:
        self._codecs_by_channel: Dict[int, MessageCodec] = {}
        self._overrides: Dict[tuple, SignalOverride] = {}
        self._overrides_by_message: Dict[tuple[int, int], Dict[str, SignalOverride]] = {}

    def bind_codec(self, logical_channel: int, codec: MessageCodec) -> None:
        self._codecs_by_channel[logical_channel] = codec

    def clear_codec(self, logical_channel: int) -> None:
        self._codecs_by_channel.pop(logical_channel, None)

    def clear_codecs(self) -> None:
        self._codecs_by_channel.clear()

    def load_database(self, logical_channel: int, path: str, *, format: str = "dbc") -> None:
        normalized_format = _normalize_database_format(format)
        self.bind_codec(logical_channel, CantoolsSignalCatalog.from_file(path, format=normalized_format))

    def set_override(self, override: SignalOverride) -> None:
        key = (override.logical_channel, override.message_id_or_pgn, override.signal_name)
        self._overrides[key] = override
        bucket_key = (override.logical_channel, override.message_id_or_pgn)
        self._overrides_by_message.setdefault(bucket_key, {})[override.signal_name] = override

    def clear_override(self, logical_channel: int, message_id_or_pgn: int, signal_name: str) -> None:
        self._overrides.pop((logical_channel, message_id_or_pgn, signal_name), None)
        bucket_key = (logical_channel, message_id_or_pgn)
        bucket = self._overrides_by_message.get(bucket_key)
        if bucket is None:
            return
        bucket.pop(signal_name, None)
        if not bucket:
            self._overrides_by_message.pop(bucket_key, None)

    def clear_overrides(self) -> None:
        self._overrides.clear()
        self._overrides_by_message.clear()

    def clear_all(self) -> None:
        self.clear_overrides()

    def list_overrides(self) -> List[SignalOverride]:
        return sorted(
            self._overrides.values(),
            key=lambda item: (item.logical_channel, item.message_id_or_pgn, item.signal_name),
        )

    def list_messages(self, logical_channel: int) -> List[MessageCatalogEntry]:
        codec = self._codecs_by_channel.get(logical_channel)
        if codec is None:
            return []
        return codec.messages()

    def list_signals(self, logical_channel: int, message_id: int) -> List[SignalCatalogEntry]:
        codec = self._codecs_by_channel.get(logical_channel)
        if codec is None or not codec.supports(message_id):
            return []
        return codec.signals(message_id)

    def available_aliases(self, logical_channel: int) -> Dict[str, str]:
        codec = self._codecs_by_channel.get(logical_channel)
        if not codec:
            return {}
        aliases: Dict[str, str] = {}
        for message_id in codec.message_ids():
            for signal_name in codec.signal_names(message_id):
                normalized = _normalize(signal_name)
                for alias, candidates in ALIAS_KEYWORDS.items():
                    if any(_normalize(candidate) in normalized for candidate in candidates):
                        aliases.setdefault(alias, signal_name)
        return aliases

    def list_message_ids(self, logical_channel: int) -> List[int]:
        codec = self._codecs_by_channel.get(logical_channel)
        if codec is None:
            return []
        return codec.message_ids()

    def list_signal_names(self, logical_channel: int, message_id: int) -> List[str]:
        codec = self._codecs_by_channel.get(logical_channel)
        if codec is None or not codec.supports(message_id):
            return []
        return codec.signal_names(message_id)

    def message_name(self, logical_channel: int, message_id: int) -> Optional[str]:
        codec = self._codecs_by_channel.get(logical_channel)
        if codec is None or not codec.supports(message_id):
            return None
        return codec.message_name(message_id)

    def apply(self, event: FrameEvent) -> FrameEvent:
        codec = self._codecs_by_channel.get(event.channel)
        if codec is None or not codec.supports(event.message_id):
            return event
        bucket = self._overrides_by_message.get((event.channel, event.message_id))
        if not bucket:
            return event
        changes = {
            signal_name: override.value
            for signal_name, override in bucket.items()
        }
        if not changes:
            return event
        payload = codec.encode(event.message_id, changes, event.payload)
        dlc = len(payload)
        if event.bus_type == BusType.CANFD:
            dlc = canfd_payload_length_to_dlc(len(payload))
        return event.clone(payload=payload, dlc=dlc)


def _normalize(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _normalize_database_format(format_name: str) -> str:
    normalized = str(format_name or "dbc").strip().lower()
    if normalized != "dbc":
        raise ValueError(f"当前仅支持 dbc 数据库格式：{format_name}")
    return normalized


def _normalize_choices(raw_choices: Any) -> Dict[int, str]:
    if not raw_choices or not isinstance(raw_choices, dict):
        return {}
    choices: Dict[int, str] = {}
    for raw_key, raw_value in raw_choices.items():
        try:
            key = int(raw_key)
        except (TypeError, ValueError):
            continue
        label = getattr(raw_value, "name", raw_value)
        choices[key] = str(label)
    return dict(sorted(choices.items()))
