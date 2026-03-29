from __future__ import annotations

from ctypes import Structure, c_ubyte, c_uint
import unittest

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


class _FakeZcan:
    def __init__(self) -> None:
        self.last_batch: list[tuple[int, bytes, int]] = []

    def TransmitFD(self, _handle, messages, count):
        self.last_batch = [
            (
                int(messages[index].frame.len),
                bytes(messages[index].frame.data[:64]),
                int(messages[index].frame.flags),
            )
            for index in range(count)
        ]
        return count


class ZlgAdapterTests(unittest.TestCase):
    def test_send_fd_uses_payload_length_to_encode_dlc(self):
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
        adapter._zcan = _FakeZcan()

        sent = adapter._send_fd(
            handle=object(),
            events=[
                FrameEvent(
                    ts_ns=0,
                    bus_type=BusType.CANFD,
                    channel=0,
                    message_id=0x13B,
                    payload=bytes.fromhex("79E000E000B633CE2F0021150000280E"),
                    dlc=16,
                    flags={"brs": True},
                )
            ],
        )

        self.assertEqual(1, sent)
        self.assertEqual(0xA, adapter._zcan.last_batch[0][0])
        self.assertEqual(bytes.fromhex("79E000E000B633CE2F0021150000280E"), adapter._zcan.last_batch[0][1][:16])
        self.assertTrue(adapter._zcan.last_batch[0][2] & 0x01)


if __name__ == "__main__":
    unittest.main()
