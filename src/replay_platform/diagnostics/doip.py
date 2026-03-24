from __future__ import annotations

import socket
import struct
from typing import List, Optional

from replay_platform.adapters.base import DiagnosticClient
from replay_platform.core import UdsRequest, UdsResponse
from replay_platform.diagnostics.dtc import DtcDictionary, DtcParser
from replay_platform.errors import DiagnosticError


DOIP_VERSION = 0x02
DOIP_INVERSE_VERSION = 0xFD

PT_ROUTING_ACTIVATION_REQ = 0x0005
PT_ROUTING_ACTIVATION_RES = 0x0006
PT_ALIVE_CHECK_REQ = 0x0007
PT_ALIVE_CHECK_RES = 0x0008
PT_DIAGNOSTIC_MESSAGE = 0x8001
PT_DIAGNOSTIC_MESSAGE_ACK = 0x8002
PT_DIAGNOSTIC_MESSAGE_NACK = 0x8003


class DoipLinkAdapter:
    def __init__(
        self,
        host: str,
        port: int = 13400,
        source_address: int = 0x0E00,
        target_address: int = 0x0001,
        activation_type: int = 0x00,
        timeout_ms: int = 1000,
    ) -> None:
        self.host = host
        self.port = port
        self.source_address = source_address
        self.target_address = target_address
        self.activation_type = activation_type
        self.timeout_ms = timeout_ms
        self._socket: Optional[socket.socket] = None

    def connect(self) -> None:
        self.disconnect()
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout_ms / 1000.0)
        sock.settimeout(self.timeout_ms / 1000.0)
        self._socket = sock
        self._routing_activation()

    def disconnect(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def reconnect(self) -> None:
        self.connect()

    def alive_check(self) -> None:
        self._send(PT_ALIVE_CHECK_REQ, b"")
        payload_type, _payload = self._recv()
        if payload_type != PT_ALIVE_CHECK_RES:
            raise DiagnosticError(f"收到异常的 DoIP 保活响应：0x{payload_type:04X}")

    def request(self, uds_payload: bytes) -> bytes:
        body = struct.pack(">HH", self.source_address, self.target_address) + uds_payload
        self._send(PT_DIAGNOSTIC_MESSAGE, body)
        while True:
            payload_type, payload = self._recv()
            if payload_type == PT_DIAGNOSTIC_MESSAGE_ACK:
                continue
            if payload_type == PT_DIAGNOSTIC_MESSAGE_NACK:
                raise DiagnosticError("DoIP 诊断报文返回负确认。")
            if payload_type == PT_DIAGNOSTIC_MESSAGE:
                if len(payload) < 4:
                    raise DiagnosticError("DoIP 诊断负载格式错误。")
                return payload[4:]
            raise DiagnosticError(f"收到异常的 DoIP 负载类型：0x{payload_type:04X}")

    def _routing_activation(self) -> None:
        payload = struct.pack(">HB", self.source_address, self.activation_type) + b"\x00" * 5
        self._send(PT_ROUTING_ACTIVATION_REQ, payload)
        payload_type, payload = self._recv()
        if payload_type != PT_ROUTING_ACTIVATION_RES:
            raise DiagnosticError(f"收到异常的路由激活响应：0x{payload_type:04X}")
        if len(payload) < 5 or payload[4] not in (0x10, 0x11):
            code = payload[4] if len(payload) >= 5 else -1
            raise DiagnosticError(f"DoIP 路由激活失败，返回码 0x{code:02X}")

    def _send(self, payload_type: int, payload: bytes) -> None:
        if self._socket is None:
            raise DiagnosticError("DoIP 套接字尚未连接。")
        header = struct.pack(">BBHI", DOIP_VERSION, DOIP_INVERSE_VERSION, payload_type, len(payload))
        self._socket.sendall(header + payload)

    def _recv(self) -> tuple:
        if self._socket is None:
            raise DiagnosticError("DoIP 套接字尚未连接。")
        header = self._recv_exact(8)
        version, inverse, payload_type, payload_length = struct.unpack(">BBHI", header)
        if version != DOIP_VERSION or inverse != DOIP_INVERSE_VERSION:
            raise DiagnosticError("DoIP 协议版本无效。")
        payload = self._recv_exact(payload_length)
        return payload_type, payload

    def _recv_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            piece = self._socket.recv(size - len(chunks))  # type: ignore[union-attr]
            if not piece:
                raise DiagnosticError("DoIP 套接字已异常关闭。")
            chunks.extend(piece)
        return bytes(chunks)


class DoipDiagnosticClient(DiagnosticClient):
    def __init__(self, link: DoipLinkAdapter, dtc_dictionary: Optional[DtcDictionary] = None) -> None:
        self.link = link
        self.dtc_dictionary = dtc_dictionary

    def connect(self) -> None:
        self.link.connect()

    def request(self, request: UdsRequest) -> UdsResponse:
        payload = bytes([request.service_id]) + request.payload
        raw = self.link.request(payload)
        if not raw:
            raise DiagnosticError("DoIP UDS 响应为空。")
        if raw[0] == 0x7F:
            return UdsResponse(
                positive=False,
                service_id=raw[1] if len(raw) > 1 else request.service_id,
                payload=raw[3:] if len(raw) > 3 else b"",
                raw=raw,
                negative_code=raw[2] if len(raw) > 2 else None,
            )
        return UdsResponse(
            positive=True,
            service_id=raw[0],
            payload=raw[1:],
            raw=raw,
        )

    def read_dtc(self) -> List[object]:
        response = self.request(DtcParser.build_read_request())
        if not response.positive:
            raise DiagnosticError(f"读取 DTC 失败：0x{response.negative_code:02X}")
        return DtcParser.parse_read_response(response.raw, self.dtc_dictionary)

    def clear_dtc(self, group: int = 0xFFFFFF) -> UdsResponse:
        return self.request(DtcParser.build_clear_request(group))

    def disconnect(self) -> None:
        self.link.disconnect()

    def reconnect(self) -> None:
        self.link.reconnect()
