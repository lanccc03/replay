from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Optional

from replay_platform.core import DtcRecord, UdsRequest
from replay_platform.errors import DiagnosticError


STATUS_FLAGS = {
    0x01: "test_failed",
    0x02: "test_failed_this_operation_cycle",
    0x04: "pending_dtc",
    0x08: "confirmed_dtc",
    0x10: "test_not_completed_since_clear",
    0x20: "test_failed_since_clear",
    0x40: "test_not_completed_this_operation_cycle",
    0x80: "warning_indicator_requested",
}


class DtcDictionary:
    def __init__(self, entries: Optional[Dict[str, str]] = None) -> None:
        self.entries = entries or {}

    @classmethod
    def load(cls, path: str) -> "DtcDictionary":
        source = Path(path)
        if source.suffix.lower() == ".json":
            return cls(json.loads(source.read_text(encoding="utf-8")))
        if source.suffix.lower() == ".csv":
            entries: Dict[str, str] = {}
            with source.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    code = row.get("code")
                    description = row.get("description")
                    if code and description:
                        entries[code.upper()] = description
            return cls(entries)
        raise DiagnosticError(f"不支持的 DTC 字典格式：{source.suffix}")

    def describe(self, code: str) -> str:
        return self.entries.get(code.upper(), "")


class DtcParser:
    @staticmethod
    def build_read_request(status_mask: int = 0xFF) -> UdsRequest:
        return UdsRequest(service_id=0x19, payload=bytes([0x02, status_mask]))

    @staticmethod
    def build_clear_request(group: int = 0xFFFFFF) -> UdsRequest:
        return UdsRequest(
            service_id=0x14,
            payload=bytes([(group >> 16) & 0xFF, (group >> 8) & 0xFF, group & 0xFF]),
        )

    @staticmethod
    def parse_read_response(payload: bytes, dictionary: Optional[DtcDictionary] = None) -> List[DtcRecord]:
        if len(payload) < 3 or payload[0] != 0x59:
            raise DiagnosticError("ReadDTCInformation 响应无效。")
        if payload[1] != 0x02:
            raise DiagnosticError(f"不支持的 ReadDTCInformation 子功能：0x{payload[1]:02X}")
        records: List[DtcRecord] = []
        index = 3
        while index + 3 < len(payload):
            code = payload[index : index + 3].hex().upper()
            status = payload[index + 3]
            flags = [
                flag_name
                for bit, flag_name in STATUS_FLAGS.items()
                if status & bit
            ]
            description = dictionary.describe(code) if dictionary else ""
            records.append(DtcRecord(code=code, status=status, status_flags=flags, description=description))
            index += 4
        return records
