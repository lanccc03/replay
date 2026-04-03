from __future__ import annotations

from ctypes import Structure, c_ubyte, c_uint
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import tests.bootstrap  # noqa: F401

from replay_platform.adapters.zlg import ZlgDeviceAdapter
from replay_platform.core import BusType, DeviceChannelBinding, FrameEvent


class _FakeCanFdFrame(Structure):
    _pack_ = 1
    _fields_ = [
        ("can_id", c_uint),
        ("len", c_ubyte),
        ("flags", c_ubyte),
        ("_res0", c_ubyte),
        ("_res1", c_ubyte),
        ("data", c_ubyte * 64),
    ]


class _FakeTransmitFdData(Structure):
    _fields_ = [
        ("frame", _FakeCanFdFrame),
        ("transmit_type", c_uint),
    ]


class _FakeSdkModule:
    ZCAN_TransmitFD_Data = _FakeTransmitFdData
    ZCAN_DT_ZCAN_CAN_CANFD_DATA = 1


class _FakeZcan:
    def __init__(self) -> None:
        self.last_batch: list[tuple[int, bytes, int, int, int]] = []

    def TransmitFD(self, _handle, messages, count):
        self.last_batch = [
            (
                int(messages[index].frame.len),
                bytes(messages[index].frame.data[:64]),
                int(messages[index].frame.flags),
                int(messages[index].frame._res0),
                int(messages[index].frame._res1),
            )
            for index in range(count)
        ]
        return count


class ZlgAdapterTests(unittest.TestCase):
    def _make_adapter(self) -> ZlgDeviceAdapter:
        adapter = ZlgDeviceAdapter(
            "zlg0",
            DeviceChannelBinding(
                adapter_id="zlg0",
                driver="zlg",
                logical_channel=0,
                physical_channel=0,
                bus_type=BusType.CANFD,
                device_type="USBCANFD",
            ),
        )
        adapter._sdk_module = _FakeSdkModule()
        return adapter

    def _make_fd_frame(self, message_id: int, payload: bytes, *, flags: int = 0) -> _FakeCanFdFrame:
        frame = _FakeCanFdFrame()
        frame.can_id = message_id
        frame.len = len(payload)
        frame.flags = flags
        for index, value in enumerate(payload):
            frame.data[index] = value
        return frame

    def test_send_fd_uses_actual_payload_length(self):
        adapter = self._make_adapter()
        adapter._zcan = _FakeZcan()
        payloads = [
            bytes.fromhex("79E000E000B633CE2F0021150000280E"),
            bytes(range(24)),
            bytes(range(64)),
        ]

        sent = adapter._send_fd(
            handle=object(),
            events=[
                FrameEvent(
                    ts_ns=0,
                    bus_type=BusType.CANFD,
                    channel=0,
                    message_id=0x13B,
                    payload=payloads[0],
                    dlc=0xA,
                    flags={"brs": True},
                ),
                FrameEvent(
                    ts_ns=0,
                    bus_type=BusType.CANFD,
                    channel=0,
                    message_id=0x146,
                    payload=payloads[1],
                    dlc=0xC,
                ),
                FrameEvent(
                    ts_ns=0,
                    bus_type=BusType.CANFD,
                    channel=0,
                    message_id=0x12E,
                    payload=payloads[2],
                    dlc=0xF,
                ),
            ],
        )

        self.assertEqual(3, sent)
        self.assertEqual([16, 24, 64], [item[0] for item in adapter._zcan.last_batch])
        self.assertEqual(payloads[0], adapter._zcan.last_batch[0][1][:16])
        self.assertEqual(payloads[1], adapter._zcan.last_batch[1][1][:24])
        self.assertEqual(payloads[2], adapter._zcan.last_batch[2][1][:64])
        self.assertTrue(adapter._zcan.last_batch[0][2] & 0x01)

    def test_send_scheduled_fd_encodes_queue_delay_without_losing_brs_or_length(self):
        adapter = self._make_adapter()
        adapter._zcan = _FakeZcan()
        adapter._channel_handles[0] = object()
        frames = [
            FrameEvent(
                ts_ns=450_000,
                bus_type=BusType.CANFD,
                channel=0,
                message_id=0x13B,
                payload=bytes(range(16)),
                dlc=0xA,
                flags={"brs": True},
            ),
            FrameEvent(
                ts_ns=2_000_000,
                bus_type=BusType.CANFD,
                channel=0,
                message_id=0x146,
                payload=bytes(range(24)),
                dlc=0xC,
            ),
        ]

        with patch("replay_platform.adapters.zlg.time.perf_counter_ns", return_value=1_000_000_000):
            sent = adapter.send_scheduled(frames, 1_000_000_000)

        self.assertEqual(2, sent)
        self.assertEqual(16, adapter._zcan.last_batch[0][0])
        self.assertEqual(bytes(range(16)), adapter._zcan.last_batch[0][1][:16])
        self.assertEqual(0xC1, adapter._zcan.last_batch[0][2])
        self.assertEqual(5, adapter._zcan.last_batch[0][3])
        self.assertEqual(0, adapter._zcan.last_batch[0][4])
        self.assertEqual(24, adapter._zcan.last_batch[1][0])
        self.assertEqual(bytes(range(24)), adapter._zcan.last_batch[1][1][:24])
        self.assertEqual(0x80, adapter._zcan.last_batch[1][2])
        self.assertEqual(2, adapter._zcan.last_batch[1][3])
        self.assertEqual(0, adapter._zcan.last_batch[1][4])

    def test_convert_fd_maps_payload_length_back_to_canfd_dlc(self):
        adapter = self._make_adapter()
        payloads = [
            bytes(range(16)),
            bytes(range(24)),
            bytes(range(64)),
        ]

        events = adapter._convert_fd(
            physical_channel=0,
            messages=[
                SimpleNamespace(frame=self._make_fd_frame(0x44D, payloads[0], flags=0x21), timestamp=123),
                SimpleNamespace(frame=self._make_fd_frame(0x146, payloads[1], flags=0x01), timestamp=456),
                SimpleNamespace(frame=self._make_fd_frame(0x12E, payloads[2]), timestamp=789),
            ],
        )

        self.assertEqual([0xA, 0xC, 0xF], [event.dlc for event in events])
        self.assertEqual([16, 24, 64], [len(event.payload) for event in events])
        self.assertEqual("Tx", events[0].flags["direction"])
        self.assertEqual("Rx", events[1].flags["direction"])
        self.assertTrue(events[0].flags["brs"])
        self.assertTrue(events[1].flags["brs"])
        self.assertFalse(events[2].flags["brs"])

    def test_convert_merge_maps_canfd_payload_length_back_to_dlc(self):
        adapter = self._make_adapter()
        payload = bytes(range(64))
        frame = self._make_fd_frame(0x12E, payload, flags=0x01)

        events = adapter._convert_merge(
            [
                SimpleNamespace(
                    dataType=_FakeSdkModule.ZCAN_DT_ZCAN_CAN_CANFD_DATA,
                    chnl=1,
                    data=SimpleNamespace(
                        zcanfddata=SimpleNamespace(
                            timestamp=321,
                            flag=SimpleNamespace(frameType=1, txEchoed=0),
                            frame=frame,
                        )
                    ),
                )
            ]
        )

        self.assertEqual(1, len(events))
        self.assertEqual(BusType.CANFD, events[0].bus_type)
        self.assertEqual(0xF, events[0].dlc)
        self.assertEqual(payload, events[0].payload)
        self.assertEqual("Rx", events[0].flags["direction"])
        self.assertTrue(events[0].flags["brs"])


if __name__ == "__main__":
    unittest.main()
