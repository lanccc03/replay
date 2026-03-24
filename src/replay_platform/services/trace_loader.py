from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from replay_platform.core import BusType, FrameEvent
from replay_platform.errors import DependencyUnavailableError, TraceFormatError


ASC_LINE = re.compile(
    r"^(?P<ts>\d+\.\d+)\s+"
    r"(?P<channel>\d+)\s+"
    r"(?P<msgid>[0-9A-Fa-f]+)(?P<ext>x|X)?\s+"
    r"(?P<direction>Rx|Tx)\s+"
    r"(?P<kind>[dD])\s+"
    r"(?P<dlc>\d+)\s*"
    r"(?P<data>(?:[0-9A-Fa-f]{2}\s*)*)$"
)


@dataclass
class TraceSummary:
    event_count: int
    start_ns: int
    end_ns: int


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

    def _load_asc(self, path: Path) -> List[FrameEvent]:
        events: List[FrameEvent] = []
        for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("//") or line.startswith("date "):
                continue
            match = ASC_LINE.match(line)
            if match is None:
                continue
            payload_hex = match.group("data").strip()
            payload = bytes.fromhex(payload_hex) if payload_hex else b""
            message_id = int(match.group("msgid"), 16)
            if match.group("ext"):
                message_id |= 1 << 31
            event = FrameEvent(
                ts_ns=int(float(match.group("ts")) * 1_000_000_000),
                bus_type=BusType.CANFD if len(payload) > 8 else BusType.CAN,
                channel=max(int(match.group("channel")) - 1, 0),
                message_id=message_id,
                payload=payload,
                dlc=int(match.group("dlc")),
                flags={"direction": match.group("direction")},
                source_file=str(path),
            )
            events.append(event)
        if not events:
            raise TraceFormatError(f"在 {path} 中未找到可识别的 ASC 帧。")
        return sorted(events, key=lambda item: item.ts_ns)

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
                if message.is_extended_id:
                    raw_id = int(message.arbitration_id) | (1 << 31)
                else:
                    raw_id = int(message.arbitration_id)
                events.append(
                    FrameEvent(
                        ts_ns=int(message.timestamp * 1_000_000_000),
                        bus_type=bus_type,
                        channel=int(getattr(message, "channel", 0) or 0),
                        message_id=raw_id,
                        payload=bytes(message.data),
                        dlc=int(message.dlc),
                        flags={
                            "direction": "Tx" if getattr(message, "is_tx", False) else "Rx",
                            "brs": bool(getattr(message, "bitrate_switch", False)),
                        },
                        source_file=str(path),
                    )
                )
        return sorted(events, key=lambda item: item.ts_ns)
