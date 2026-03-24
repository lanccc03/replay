from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from replay_platform.adapters.base import DeviceAdapter, DiagnosticClient
from replay_platform.core import BusType, FrameEvent, UdsRequest, UdsResponse
from replay_platform.diagnostics.dtc import DtcDictionary, DtcParser
from replay_platform.errors import DiagnosticError


@dataclass
class IsoTpConfig:
    channel: int
    tx_id: int
    rx_id: int
    bus_type: BusType = BusType.CAN
    extended: bool = False
    timeout_ms: int = 1000
    pad_byte: int = 0x00


class CanUdsClient(DiagnosticClient):
    """
    Adapter-level ISO-TP/UDS client.

    This keeps CAN diagnostics usable even before raw ZLG UDS export signatures are hardware-verified.
    """

    def __init__(
        self,
        adapter: DeviceAdapter,
        config: IsoTpConfig,
        dtc_dictionary: Optional[DtcDictionary] = None,
    ) -> None:
        self.adapter = adapter
        self.config = config
        self.dtc_dictionary = dtc_dictionary

    def connect(self) -> None:
        return None

    def request(self, request: UdsRequest) -> UdsResponse:
        payload = bytes([request.service_id]) + request.payload
        self._transmit(payload)
        raw = self._receive_payload(request.timeout_ms or self.config.timeout_ms)
        if raw[0] == 0x7F:
            return UdsResponse(
                positive=False,
                service_id=raw[1] if len(raw) > 1 else request.service_id,
                payload=raw[3:] if len(raw) > 3 else b"",
                raw=raw,
                negative_code=raw[2] if len(raw) > 2 else None,
            )
        return UdsResponse(positive=True, service_id=raw[0], payload=raw[1:], raw=raw)

    def read_dtc(self) -> List[object]:
        response = self.request(DtcParser.build_read_request())
        if not response.positive:
            raise DiagnosticError(f"读取 DTC 失败：0x{response.negative_code:02X}")
        return DtcParser.parse_read_response(response.raw, self.dtc_dictionary)

    def clear_dtc(self, group: int = 0xFFFFFF) -> UdsResponse:
        return self.request(DtcParser.build_clear_request(group))

    def disconnect(self) -> None:
        return None

    def reconnect(self) -> None:
        return None

    def _transmit(self, payload: bytes) -> None:
        if len(payload) <= 7:
            frame = bytes([len(payload)]) + payload
            self._send_frame(frame.ljust(8, bytes([self.config.pad_byte])))
            return
        total_length = len(payload)
        first = bytes([0x10 | ((total_length >> 8) & 0x0F), total_length & 0xFF]) + payload[:6]
        self._send_frame(first.ljust(8, bytes([self.config.pad_byte])))
        fc = self._receive_frame(self.config.timeout_ms)
        if (fc[0] >> 4) != 0x3:
            raise DiagnosticError("未收到 ISO-TP 流控帧。")
        index = 6
        sequence = 1
        while index < total_length:
            chunk = payload[index : index + 7]
            cf = bytes([0x20 | (sequence & 0x0F)]) + chunk
            self._send_frame(cf.ljust(8, bytes([self.config.pad_byte])))
            index += len(chunk)
            sequence = (sequence + 1) & 0x0F

    def _receive_payload(self, timeout_ms: int) -> bytes:
        frame = self._receive_frame(timeout_ms)
        pci = frame[0] >> 4
        if pci == 0x0:
            size = frame[0] & 0x0F
            return frame[1 : 1 + size]
        if pci != 0x1:
            raise DiagnosticError("不支持的 ISO-TP 首帧响应。")
        total_length = ((frame[0] & 0x0F) << 8) | frame[1]
        payload = bytearray(frame[2:])
        flow_control = bytes([0x30, 0x00, 0x00]).ljust(8, b"\x00")
        self._send_frame(flow_control)
        expected_seq = 1
        while len(payload) < total_length:
            chunk = self._receive_frame(timeout_ms)
            if (chunk[0] >> 4) != 0x2:
                raise DiagnosticError("收到异常的 ISO-TP 连续帧。")
            if (chunk[0] & 0x0F) != (expected_seq & 0x0F):
                raise DiagnosticError("ISO-TP 序号错误。")
            payload.extend(chunk[1:])
            expected_seq += 1
        return bytes(payload[:total_length])

    def _receive_frame(self, timeout_ms: int) -> bytes:
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            frames = self.adapter.read(limit=64, timeout_ms=10)
            for frame in frames:
                if frame.channel != self.config.channel:
                    continue
                arbitration_id = frame.message_id & 0x1FFFFFFF
                if arbitration_id != self.config.rx_id:
                    continue
                return frame.payload[:8].ljust(8, b"\x00")
            time.sleep(0.001)
        raise DiagnosticError("ISO-TP 响应超时。")

    def _send_frame(self, payload: bytes) -> None:
        raw_id = self.config.tx_id | ((1 << 31) if self.config.extended else 0)
        event = FrameEvent(
            ts_ns=0,
            bus_type=self.config.bus_type,
            channel=self.config.channel,
            message_id=raw_id,
            payload=payload[:8],
            dlc=8,
            flags={"tx": True},
            source_file="diagnostic",
        )
        sent = self.adapter.send([event])
        if sent != 1:
            raise DiagnosticError("发送 ISO-TP 帧失败。")
