from __future__ import annotations

from replay_platform.core import BusType, FrameEvent, canfd_payload_length_to_dlc


EXTENDED_ID_FLAG = 1 << 31
CAN_ID_MASK = 0x1FFFFFFF


def classic_payload(event: FrameEvent) -> bytes:
    return bytes(event.payload[:8])


def canfd_payload(event: FrameEvent) -> bytes:
    return bytes(event.payload[:64])


def canfd_dlc_from_payload(payload: bytes) -> int:
    return canfd_payload_length_to_dlc(len(payload))


def canfd_dlc_from_length(payload_length: int) -> int:
    return canfd_payload_length_to_dlc(max(int(payload_length), 0))


def is_extended_id(event: FrameEvent) -> bool:
    return bool(event.flags.get("extended")) or event.bus_type == BusType.J1939 or int(event.message_id) > 0x7FF


def arbitration_id(event: FrameEvent) -> int:
    return int(event.message_id) & CAN_ID_MASK


def zlg_transmit_can_id(event: FrameEvent) -> int:
    message_id = int(event.message_id)
    if event.bus_type == BusType.J1939 and not (message_id & EXTENDED_ID_FLAG):
        message_id |= EXTENDED_ID_FLAG
    return message_id


def timestamp_us(event: FrameEvent) -> int:
    return max(int(event.ts_ns // 1000), 0)
