from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from replay_platform.core import BusType, FrameEvent, canfd_payload_length_to_dlc
from replay_platform.errors import DependencyUnavailableError, TraceFormatError


ASC_DIRECTIONS = frozenset({"rx", "tx"})
BINARY_CACHE_FORMAT = "binary-v1"
BINARY_CACHE_SUFFIX = ".rplbin"
BINARY_CACHE_MAGIC = b"RPLBIN1\0"
BINARY_CACHE_VERSION = 1
_BINARY_FILE_HEADER = struct.Struct("<8sHI")
_BINARY_RECORD_LENGTH = struct.Struct("<I")
_BINARY_RECORD_HEADER = struct.Struct("<qBIIHIIII")
_BUS_TYPE_TO_CODE = {
    BusType.CAN: 1,
    BusType.CANFD: 2,
    BusType.J1939: 3,
    BusType.ETH: 4,
}
_BUS_TYPE_FROM_CODE = {value: key for key, value in _BUS_TYPE_TO_CODE.items()}


@dataclass
class TraceSummary:
    event_count: int
    start_ns: int
    end_ns: int


def build_trace_source_summaries(events: Sequence[FrameEvent]) -> list[dict[str, Any]]:
    counts: Dict[tuple[int, BusType], int] = {}
    for event in events:
        key = (int(event.channel), event.bus_type)
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "source_channel": source_channel,
            "bus_type": bus_type.value,
            "frame_count": frame_count,
            "label": f"CH{source_channel} | {bus_type.value} | {frame_count}\u5e27",
        }
        for (source_channel, bus_type), frame_count in sorted(counts.items(), key=lambda item: (item[0][0], item[0][1].value))
    ]


def build_trace_message_id_summaries(events: Sequence[FrameEvent]) -> list[dict[str, Any]]:
    grouped: Dict[tuple[int, BusType], dict[str, Any]] = {}
    for event in events:
        key = (int(event.channel), event.bus_type)
        summary = grouped.setdefault(
            key,
            {
                "source_channel": int(event.channel),
                "bus_type": event.bus_type.value,
                "frame_count": 0,
                "message_ids": set(),
            },
        )
        summary["frame_count"] += 1
        summary["message_ids"].add(int(event.message_id))
    return [
        {
            "source_channel": int(summary["source_channel"]),
            "bus_type": str(summary["bus_type"]),
            "frame_count": int(summary["frame_count"]),
            "message_ids": sorted(int(message_id) for message_id in summary["message_ids"]),
        }
        for _key, summary in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1].value))
    ]


class TraceLoader:
    def load(self, path: str) -> List[FrameEvent]:
        trace_path = Path(path)
        suffix = trace_path.suffix.lower()
        if suffix == ".asc":
            return self._load_asc(trace_path)
        if suffix == ".blf":
            return self._load_blf(trace_path)
        raise TraceFormatError(f"不支持的回放文件格式：{trace_path.suffix}")

    def summarize(self, events: Sequence[FrameEvent]) -> TraceSummary:
        if not events:
            return TraceSummary(event_count=0, start_ns=0, end_ns=0)
        return TraceSummary(
            event_count=len(events),
            start_ns=events[0].ts_ns,
            end_ns=events[-1].ts_ns,
        )

    def write_cache(self, path: Path, events: Sequence[FrameEvent]) -> None:
        payload = [
            {
                "ts_ns": item.ts_ns,
                "bus_type": item.bus_type.value,
                "channel": item.channel,
                "message_id": item.message_id,
                "payload": item.payload.hex(),
                "dlc": item.dlc,
                "flags": dict(item.flags),
                "source_file": item.source_file,
                "metadata": dict(item.metadata),
            }
            for item in events
        ]
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load_cache(self, path: Path) -> List[FrameEvent]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [
            FrameEvent(
                ts_ns=int(item["ts_ns"]),
                bus_type=BusType(item["bus_type"]),
                channel=int(item["channel"]),
                message_id=int(item["message_id"]),
                payload=bytes.fromhex(item["payload"]),
                dlc=int(item["dlc"]),
                flags=dict(item.get("flags", {})),
                source_file=item.get("source_file", ""),
                metadata=dict(item.get("metadata", {})),
            )
            for item in payload
        ]

    def write_binary_cache(self, path: Path, events: Sequence[FrameEvent]) -> None:
        with path.open("wb") as handle:
            handle.write(
                _BINARY_FILE_HEADER.pack(
                    BINARY_CACHE_MAGIC,
                    BINARY_CACHE_VERSION,
                    len(events),
                )
            )
            for item in events:
                payload = bytes(item.payload)
                flags = (
                    json.dumps(item.flags, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                    if item.flags
                    else b""
                )
                source = item.source_file.encode("utf-8") if item.source_file else b""
                metadata = (
                    json.dumps(
                        item.metadata,
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    if item.metadata
                    else b""
                )
                record = _BINARY_RECORD_HEADER.pack(
                    int(item.ts_ns),
                    _BUS_TYPE_TO_CODE[item.bus_type],
                    int(item.channel),
                    int(item.message_id) & 0xFFFFFFFF,
                    int(item.dlc),
                    len(payload),
                    len(flags),
                    len(source),
                    len(metadata),
                )
                handle.write(_BINARY_RECORD_LENGTH.pack(len(record) + len(payload) + len(flags) + len(source) + len(metadata)))
                handle.write(record)
                handle.write(payload)
                handle.write(flags)
                handle.write(source)
                handle.write(metadata)

    def load_binary_cache(
        self,
        path: Path,
        *,
        source_filters: Optional[set[tuple[int, BusType]]] = None,
    ) -> List[FrameEvent]:
        return list(self.iter_binary_cache(path, source_filters=source_filters))

    def iter_binary_cache(
        self,
        path: str | Path,
        *,
        source_filters: Optional[set[tuple[int, BusType]]] = None,
    ) -> Iterator[FrameEvent]:
        cache_path = Path(path)
        with cache_path.open("rb") as handle:
            header = handle.read(_BINARY_FILE_HEADER.size)
            if len(header) < _BINARY_FILE_HEADER.size:
                raise TraceFormatError("二进制缓存文件已损坏或内容不完整。")
            magic, version, record_count = _BINARY_FILE_HEADER.unpack(header)
            if magic != BINARY_CACHE_MAGIC or version != BINARY_CACHE_VERSION:
                raise TraceFormatError(f"不支持的二进制缓存格式：{cache_path}")
            for _index in range(record_count):
                length_payload = handle.read(_BINARY_RECORD_LENGTH.size)
                if len(length_payload) < _BINARY_RECORD_LENGTH.size:
                    raise TraceFormatError("二进制缓存文件已损坏或内容不完整。")
                record_length = _BINARY_RECORD_LENGTH.unpack(length_payload)[0]
                record = handle.read(record_length)
                if len(record) < record_length:
                    raise TraceFormatError("二进制缓存文件已损坏或内容不完整。")
                (
                    ts_ns,
                    bus_type_code,
                    channel,
                    message_id,
                    dlc,
                    payload_len,
                    flags_len,
                    source_len,
                    metadata_len,
                ) = _BINARY_RECORD_HEADER.unpack_from(record, 0)
                bus_type = _BUS_TYPE_FROM_CODE.get(bus_type_code)
                if bus_type is None:
                    raise TraceFormatError(f"未知的总线类型编码：{bus_type_code}")
                if source_filters is not None and (int(channel), bus_type) not in source_filters:
                    continue
                data_offset = _BINARY_RECORD_HEADER.size
                frame_payload = record[data_offset : data_offset + payload_len]
                data_offset += payload_len
                flags_payload = record[data_offset : data_offset + flags_len]
                data_offset += flags_len
                source_payload = record[data_offset : data_offset + source_len]
                data_offset += source_len
                metadata_payload = record[data_offset : data_offset + metadata_len]
                yield FrameEvent(
                    ts_ns=int(ts_ns),
                    bus_type=bus_type,
                    channel=int(channel),
                    message_id=int(message_id),
                    payload=bytes(frame_payload),
                    dlc=int(dlc),
                    flags=json.loads(flags_payload.decode("utf-8")) if flags_len else {},
                    source_file=source_payload.decode("utf-8"),
                    metadata=json.loads(metadata_payload.decode("utf-8")) if metadata_len else {},
                )

    def iter_asc(self, path: str | Path) -> Iterator[FrameEvent]:
        trace_path = Path(path)
        with trace_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if self._should_skip_asc_line(line):
                    continue
                event = self._parse_asc_event(line, trace_path)
                if event is None:
                    continue
                yield event

    def _load_asc(self, path: Path) -> List[FrameEvent]:
        events: List[FrameEvent] = []
        previous_ts_ns: Optional[int] = None
        needs_sort = False
        for event in self.iter_asc(path):
            if previous_ts_ns is not None and event.ts_ns < previous_ts_ns:
                needs_sort = True
            events.append(event)
            previous_ts_ns = event.ts_ns
        if not events:
            raise TraceFormatError(f"在 {path} 中未找到可识别的 ASC 帧。")
        if needs_sort:
            events.sort(key=lambda item: item.ts_ns)
        return events

    def _should_skip_asc_line(self, line: str) -> bool:
        lower = line.lower()
        return (
            not line
            or line.startswith("//")
            or lower.startswith("date ")
            or lower.startswith("base ")
            or lower.endswith("internal events logged")
            or lower.startswith("begin triggerblock")
            or lower.startswith("end triggerblock")
        )

    def _parse_asc_event(self, line: str, path: Path) -> Optional[FrameEvent]:
        tokens = line.split()
        if len(tokens) < 5:
            return None
        try:
            ts_ns = int(float(tokens[0]) * 1_000_000_000)
        except ValueError:
            return None
        if tokens[1].upper() == "CANFD":
            return self._parse_asc_canfd_data_frame(tokens, ts_ns, path)
        if tokens[1].isdigit():
            return self._parse_asc_can_data_frame(tokens, ts_ns, path)
        return None

    def _parse_asc_can_data_frame(
        self, tokens: Sequence[str], ts_ns: int, path: Path
    ) -> Optional[FrameEvent]:
        direction_index = self._find_direction_index(tokens, start=3)
        if direction_index is None or direction_index + 2 >= len(tokens):
            return None
        if tokens[direction_index + 1].lower() != "d":
            return None
        try:
            channel = max(int(tokens[1]) - 1, 0)
            direction = self._normalize_direction(tokens[direction_index])
            message_id = self._parse_message_id(tokens[2])
            dlc = self._parse_number(tokens[direction_index + 2], base=16)
            data_start = direction_index + 3
            data_end = data_start + dlc
            if dlc < 0 or data_end > len(tokens):
                return None
            payload = bytes(
                self._parse_number(token, base=16) for token in tokens[data_start:data_end]
            )
        except ValueError:
            return None
        flags = {"direction": direction}
        metadata = {}
        symbolic_name = " ".join(tokens[3:direction_index]).strip()
        if symbolic_name:
            metadata["symbolic_name"] = symbolic_name
        return FrameEvent(
            ts_ns=ts_ns,
            bus_type=BusType.CAN,
            channel=channel,
            message_id=message_id,
            payload=payload,
            dlc=dlc,
            flags=flags,
            source_file=str(path),
            metadata=metadata,
        )

    def _parse_asc_canfd_data_frame(
        self, tokens: Sequence[str], ts_ns: int, path: Path
    ) -> Optional[FrameEvent]:
        if len(tokens) < 10:
            return None
        control_index = self._find_canfd_control_index(tokens, start=5)
        if control_index is None or control_index + 3 >= len(tokens):
            return None
        try:
            channel = max(int(tokens[2]) - 1, 0)
            direction = self._normalize_direction(tokens[3])
            message_id = self._parse_message_id(tokens[4])
            brs = self._parse_binary_flag(tokens[control_index])
            esi = self._parse_binary_flag(tokens[control_index + 1])
            dlc = self._parse_number(tokens[control_index + 2], base=16)
            data_length = int(tokens[control_index + 3], 10)
            data_start = control_index + 4
            data_end = data_start + data_length
            if data_length < 0 or data_end > len(tokens):
                return None
            payload = bytes(
                self._parse_number(token, base=16) for token in tokens[data_start:data_end]
            )
        except ValueError:
            return None
        metadata = {}
        symbolic_name = " ".join(tokens[5:control_index]).strip()
        if symbolic_name:
            metadata["symbolic_name"] = symbolic_name
        return FrameEvent(
            ts_ns=ts_ns,
            bus_type=BusType.CANFD,
            channel=channel,
            message_id=message_id,
            payload=payload,
            dlc=dlc,
            flags={"direction": direction, "brs": brs, "esi": esi},
            source_file=str(path),
            metadata=metadata,
        )

    def _find_direction_index(
        self, tokens: Sequence[str], start: int
    ) -> Optional[int]:
        for index in range(start, len(tokens)):
            if tokens[index].lower() in ASC_DIRECTIONS:
                return index
        return None

    def _find_canfd_control_index(
        self, tokens: Sequence[str], start: int
    ) -> Optional[int]:
        for index in range(start, len(tokens) - 3):
            if not self._is_binary_flag(tokens[index]) or not self._is_binary_flag(tokens[index + 1]):
                continue
            if not self._is_number(tokens[index + 2], base=16):
                continue
            if not self._is_number(tokens[index + 3], base=10):
                continue
            return index
        return None

    def _normalize_direction(self, token: str) -> str:
        lower = token.lower()
        if lower == "rx":
            return "Rx"
        if lower == "tx":
            return "Tx"
        raise ValueError(token)

    def _parse_binary_flag(self, token: str) -> bool:
        if token == "0":
            return False
        if token == "1":
            return True
        raise ValueError(token)

    def _is_binary_flag(self, token: str) -> bool:
        return token in {"0", "1"}

    def _parse_message_id(self, token: str) -> int:
        extended = token.endswith(("x", "X"))
        raw_token = token[:-1] if extended else token
        message_id = self._parse_number(raw_token, base=16)
        if extended:
            message_id |= 1 << 31
        return message_id

    def _parse_number(self, token: str, base: int) -> int:
        if token.lower().startswith("0x"):
            return int(token, 16)
        return int(token, base)

    def _is_number(self, token: str, base: int) -> bool:
        try:
            self._parse_number(token, base=base)
        except ValueError:
            return False
        return True

    def _load_blf(self, path: Path) -> List[FrameEvent]:
        try:
            import can  # type: ignore
        except ModuleNotFoundError as exc:
            raise DependencyUnavailableError(
                "加载 BLF 文件需要安装 python-can。"
            ) from exc
        events: List[FrameEvent] = []
        with can.BLFReader(str(path)) as reader:
            for message in reader:
                if message.is_error_frame:
                    continue
                bus_type = BusType.CANFD if getattr(message, "is_fd", False) else BusType.CAN
                payload = bytes(message.data)
                if message.is_extended_id:
                    raw_id = int(message.arbitration_id) | (1 << 31)
                else:
                    raw_id = int(message.arbitration_id)
                dlc = int(message.dlc)
                if bus_type == BusType.CANFD:
                    dlc = canfd_payload_length_to_dlc(len(payload))
                events.append(
                    FrameEvent(
                        ts_ns=int(message.timestamp * 1_000_000_000),
                        bus_type=bus_type,
                        channel=int(getattr(message, "channel", 0) or 0),
                        message_id=raw_id,
                        payload=payload,
                        dlc=dlc,
                        flags={
                            "direction": "Tx" if getattr(message, "is_tx", False) else "Rx",
                            "brs": bool(getattr(message, "bitrate_switch", False)),
                        },
                        source_file=str(path),
                    )
                )
        return sorted(events, key=lambda item: item.ts_ns)
