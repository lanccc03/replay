from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from replay_tool.domain import BusType, Frame, canfd_payload_length_to_dlc


ASC_DIRECTIONS = frozenset({"rx", "tx"})


class AscTraceReader:
    def read(self, path: str) -> list[Frame]:
        trace_path = Path(path)
        events: list[Frame] = []
        previous_ts_ns: Optional[int] = None
        needs_sort = False
        with trace_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                event = self._parse_line(raw_line.strip(), trace_path)
                if event is None:
                    continue
                if previous_ts_ns is not None and event.ts_ns < previous_ts_ns:
                    needs_sort = True
                events.append(event)
                previous_ts_ns = event.ts_ns
        if not events:
            raise ValueError(f"No ASC frames found in {trace_path}.")
        if needs_sort:
            events.sort(key=lambda item: item.ts_ns)
        return events

    def _parse_line(self, line: str, path: Path) -> Optional[Frame]:
        if self._should_skip(line):
            return None
        tokens = line.split()
        if len(tokens) < 5:
            return None
        try:
            ts_ns = int(float(tokens[0]) * 1_000_000_000)
        except ValueError:
            return None
        if tokens[1].upper() == "CANFD":
            return self._parse_canfd(tokens, ts_ns, path)
        if tokens[1].isdigit():
            return self._parse_can(tokens, ts_ns, path)
        return None

    def _parse_can(self, tokens: Sequence[str], ts_ns: int, path: Path) -> Optional[Frame]:
        direction_index = self._find_direction_index(tokens, start=3)
        if direction_index is None or direction_index + 2 >= len(tokens):
            return None
        if tokens[direction_index + 1].lower() != "d":
            return None
        try:
            channel = max(int(tokens[1]) - 1, 0)
            direction = self._normalize_direction(tokens[direction_index])
            message_id, extended = self._parse_message_id(tokens[2])
            dlc = self._parse_number(tokens[direction_index + 2], 16)
            payload_tokens = tokens[direction_index + 3 : direction_index + 3 + dlc]
            if len(payload_tokens) != dlc:
                return None
            payload = bytes(self._parse_number(item, 16) for item in payload_tokens)
        except ValueError:
            return None
        return Frame(
            ts_ns=ts_ns,
            bus=BusType.CAN,
            channel=channel,
            message_id=message_id,
            payload=payload,
            dlc=dlc,
            extended=extended,
            direction=direction,
            source_file=str(path),
        )

    def _parse_canfd(self, tokens: Sequence[str], ts_ns: int, path: Path) -> Optional[Frame]:
        control_index = self._find_canfd_control_index(tokens, start=5)
        if control_index is None or control_index + 3 >= len(tokens):
            return None
        try:
            channel = max(int(tokens[2]) - 1, 0)
            direction = self._normalize_direction(tokens[3])
            message_id, extended = self._parse_message_id(tokens[4])
            brs = self._parse_binary(tokens[control_index])
            esi = self._parse_binary(tokens[control_index + 1])
            dlc = self._parse_number(tokens[control_index + 2], 16)
            data_length = int(tokens[control_index + 3], 10)
            payload_tokens = tokens[control_index + 4 : control_index + 4 + data_length]
            if len(payload_tokens) != data_length:
                return None
            payload = bytes(self._parse_number(item, 16) for item in payload_tokens)
        except ValueError:
            return None
        return Frame(
            ts_ns=ts_ns,
            bus=BusType.CANFD,
            channel=channel,
            message_id=message_id,
            payload=payload,
            dlc=canfd_payload_length_to_dlc(len(payload)) if payload else dlc,
            extended=extended,
            brs=brs,
            esi=esi,
            direction=direction,
            source_file=str(path),
        )

    def _should_skip(self, line: str) -> bool:
        lower = line.lower()
        return (
            not line
            or line.startswith("//")
            or lower.startswith("date ")
            or lower.startswith("base ")
            or lower.startswith("begin triggerblock")
            or lower.startswith("end triggerblock")
            or lower.endswith("internal events logged")
        )

    def _find_direction_index(self, tokens: Sequence[str], start: int) -> Optional[int]:
        for index in range(start, len(tokens)):
            if tokens[index].lower() in ASC_DIRECTIONS:
                return index
        return None

    def _find_canfd_control_index(self, tokens: Sequence[str], start: int) -> Optional[int]:
        for index in range(start, len(tokens) - 3):
            if not self._is_binary(tokens[index]) or not self._is_binary(tokens[index + 1]):
                continue
            if not self._is_number(tokens[index + 2], 16):
                continue
            if not self._is_number(tokens[index + 3], 10):
                continue
            return index
        return None

    def _parse_message_id(self, token: str) -> tuple[int, bool]:
        extended = token.endswith(("x", "X"))
        raw_token = token[:-1] if extended else token
        value = self._parse_number(raw_token, 16)
        return value, extended or value > 0x7FF

    def _normalize_direction(self, token: str) -> str:
        lower = token.lower()
        if lower == "rx":
            return "Rx"
        if lower == "tx":
            return "Tx"
        raise ValueError(token)

    def _parse_binary(self, token: str) -> bool:
        if token == "0":
            return False
        if token == "1":
            return True
        raise ValueError(token)

    def _is_binary(self, token: str) -> bool:
        return token in {"0", "1"}

    def _parse_number(self, token: str, base: int) -> int:
        if token.lower().startswith("0x"):
            return int(token, 16)
        return int(token, base)

    def _is_number(self, token: str, base: int) -> bool:
        try:
            self._parse_number(token, base)
        except ValueError:
            return False
        return True
