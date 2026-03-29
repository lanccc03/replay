from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class StaticMessageDefinition:
    name: str
    signal_bytes: Dict[str, int]


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


class CantoolsSignalCatalog:
    def __init__(self, database: Any) -> None:
        self.database = database
        self._messages = {message.frame_id: message for message in database.messages}

    @classmethod
    def from_file(cls, path: str) -> "CantoolsSignalCatalog":
        try:
            import cantools  # type: ignore
        except ModuleNotFoundError as exc:
            raise DependencyUnavailableError(
                "加载 DBC/J1939 数据库需要安装 cantools。"
            ) from exc
        database = cantools.database.load_file(path)
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


class SignalOverrideService:
    def __init__(self) -> None:
        self._codecs_by_channel: Dict[int, MessageCodec] = {}
        self._overrides: Dict[tuple, SignalOverride] = {}
        self._overrides_by_message: Dict[tuple[int, int], Dict[str, SignalOverride]] = {}

    def bind_codec(self, logical_channel: int, codec: MessageCodec) -> None:
        self._codecs_by_channel[logical_channel] = codec

    def load_database(self, logical_channel: int, path: str) -> None:
        self.bind_codec(logical_channel, CantoolsSignalCatalog.from_file(path))

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

    def clear_all(self) -> None:
        self._overrides.clear()
        self._overrides_by_message.clear()

    def list_overrides(self) -> List[SignalOverride]:
        return sorted(
            self._overrides.values(),
            key=lambda item: (item.logical_channel, item.message_id_or_pgn, item.signal_name),
        )

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
