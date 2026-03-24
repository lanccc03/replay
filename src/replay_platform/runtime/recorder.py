from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from replay_platform.adapters.base import DeviceAdapter
from replay_platform.core import BusType, FrameEvent
from replay_platform.errors import DependencyUnavailableError


class RecordingService:
    def __init__(self, adapter: DeviceAdapter) -> None:
        self.adapter = adapter
        self._writer = None
        self._path: Optional[Path] = None

    def start(self, destination: str) -> None:
        path = Path(destination)
        if path.suffix.lower() != ".blf":
            raise ValueError("录制服务仅支持输出 BLF 文件。")
        try:
            import can  # type: ignore
        except ModuleNotFoundError as exc:
            raise DependencyUnavailableError(
                "录制 BLF 文件需要安装 python-can。"
            ) from exc
        self._path = path
        self._writer = can.BLFWriter(str(path))

    def poll_once(self, limit: int = 256) -> int:
        if self._writer is None:
            return 0
        frames = self.adapter.read(limit=limit, timeout_ms=0)
        for frame in frames:
            self._writer.on_message_received(self._to_can_message(frame))
        return len(frames)

    def stop(self) -> None:
        if self._writer is not None:
            self._writer.stop()
            self._writer = None

    def _to_can_message(self, event: FrameEvent):
        import can  # type: ignore

        arbitration_id = event.message_id & 0x1FFFFFFF
        is_extended = bool(event.message_id & (1 << 31))
        return can.Message(
            timestamp=event.ts_ns / 1_000_000_000,
            arbitration_id=arbitration_id,
            is_extended_id=is_extended,
            is_fd=event.bus_type == BusType.CANFD,
            bitrate_switch=bool(event.flags.get("brs", False)),
            is_rx=event.flags.get("direction", "Rx") != "Tx",
            data=event.payload,
            channel=event.channel,
        )
