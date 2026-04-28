from __future__ import annotations

from ctypes import Structure, c_char, c_int32, c_int64, c_uint8
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace
import unittest

import tests.bootstrap  # noqa: F401

from replay_tool.adapters.tongxing import TongxingApiLoader, TongxingDevice
from replay_tool.domain import BusType, ChannelConfig, DeviceConfig, Frame


DLC_DATA_BYTE_CNT = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)


class FakeTLIBHWInfo(Structure):
    _pack_ = 1
    _fields_ = [
        ("FDeviceType", c_int32),
        ("FDeviceIndex", c_int32),
        ("FVendorName", c_char * 32),
        ("FDeviceName", c_char * 32),
        ("FSerialString", c_char * 64),
    ]


class FakeTLIBCAN(Structure):
    _pack_ = 1
    _fields_ = [
        ("FIdxChn", c_uint8),
        ("FProperties", c_uint8),
        ("FDLC", c_uint8),
        ("FReserved", c_uint8),
        ("FIdentifier", c_int32),
        ("FTimeUs", c_int64),
        ("FData", c_uint8 * 8),
    ]


class FakeTLIBCANFD(Structure):
    _pack_ = 1
    _fields_ = [
        ("FIdxChn", c_uint8),
        ("FProperties", c_uint8),
        ("FDLC", c_uint8),
        ("FFDProperties", c_uint8),
        ("FIdentifier", c_int32),
        ("FTimeUs", c_int64),
        ("FData", c_uint8 * 64),
    ]


class AppChannelType(IntEnum):
    APP_CAN = 0


class BusToolDeviceType(IntEnum):
    TS_USB_DEVICE = 3


class DeviceSubType(IntEnum):
    TC1014 = 8
    TC1016 = 11


class CANFDControllerType(IntEnum):
    lfdtISOCAN = 1


class CANFDControllerMode(IntEnum):
    lfdmNormal = 0
    lfdmACKOff = 1


class FakeEnums:
    _TLIBApplicationChannelType = AppChannelType
    _TLIBBusToolDeviceType = BusToolDeviceType
    _TLIB_TS_Device_Sub_Type = DeviceSubType
    _TLIBCANFDControllerType = CANFDControllerType
    _TLIBCANFDControllerMode = CANFDControllerMode


class FakeTSMasterAPI:
    def __init__(self, *, mapping_error_once: bool = False) -> None:
        self.dll = SimpleNamespace(
            TLIBHWInfo=FakeTLIBHWInfo,
            TLIBCAN=FakeTLIBCAN,
            TLIBCANFD=FakeTLIBCANFD,
            DLC_DATA_BYTE_CNT=DLC_DATA_BYTE_CNT,
        )
        self.mapping_error_once = mapping_error_once
        self.channel_count = 0
        self.initialized: list[tuple[str, str]] = []
        self.connected = False
        self.finalized = 0
        self.mappings: list[dict] = []
        self.can_configs: list[dict] = []
        self.canfd_configs: list[dict] = []
        self.sent_can: list[dict] = []
        self.sent_canfd: list[dict] = []
        self.can_rx: dict[int, list[FakeTLIBCAN]] = {}
        self.canfd_rx: dict[int, list[FakeTLIBCANFD]] = {}
        self.clear_calls: list[tuple[str, int]] = []

    def initialize_lib_tsmaster(self, app: bytes) -> int:
        self.initialized.append(("plain", app.decode("utf-8")))
        return 0

    def initialize_lib_tsmaster_with_project(self, app: bytes, project: bytes) -> int:
        self.initialized.append(("project", project.decode("utf-8")))
        self.mapping_error_once = False
        return 0

    def finalize_lib_tsmaster(self) -> None:
        self.finalized += 1

    def tsapp_enumerate_hw_devices(self, count: c_int32) -> int:
        count.value = 1
        return 0

    def tsapp_get_hw_info_by_index(self, index: int, info: FakeTLIBHWInfo) -> int:
        self.assert_index = int(index)
        info.FDeviceType = int(BusToolDeviceType.TS_USB_DEVICE)
        info.FDeviceIndex = 0
        info.FVendorName = b"TOSUN"
        info.FDeviceName = b"TC1014"
        info.FSerialString = b"TX-001"
        return 0

    def tsapp_get_can_channel_count(self, count: c_int32) -> int:
        count.value = self.channel_count
        return 0

    def tsapp_set_can_channel_count(self, count: int) -> int:
        self.channel_count = int(count)
        return 0

    def tsapp_set_mapping_verbose(
        self,
        app: bytes,
        app_type: int,
        app_channel: int,
        device_name: bytes,
        device_type: int,
        device_sub_type: int,
        device_index: int,
        physical_channel: int,
        enabled: bool,
    ) -> int:
        self.mappings.append(
            {
                "app": app.decode("utf-8"),
                "app_type": int(app_type),
                "app_channel": int(app_channel),
                "device_name": device_name.decode("utf-8"),
                "device_type": int(device_type),
                "device_sub_type": int(device_sub_type),
                "device_index": int(device_index),
                "physical_channel": int(physical_channel),
                "enabled": bool(enabled),
            }
        )
        if self.mapping_error_once:
            self.mapping_error_once = False
            return 82
        return 0

    def tsapp_configure_baudrate_can(self, channel, nominal_kbps, listen_only, resistance_enabled) -> int:
        self.can_configs.append(
            {
                "channel": int(channel),
                "nominal_kbps": float(nominal_kbps),
                "listen_only": bool(listen_only),
                "resistance_enabled": bool(resistance_enabled),
            }
        )
        return 0

    def tsapp_configure_baudrate_canfd(
        self,
        channel,
        nominal_kbps,
        data_kbps,
        controller_type,
        controller_mode,
        resistance_enabled,
    ) -> int:
        self.canfd_configs.append(
            {
                "channel": int(channel),
                "nominal_kbps": float(nominal_kbps),
                "data_kbps": float(data_kbps),
                "controller_type": int(controller_type),
                "controller_mode": int(controller_mode),
                "resistance_enabled": bool(resistance_enabled),
            }
        )
        return 0

    def tsapp_connect(self) -> int:
        self.connected = True
        return 0

    def tsapp_disconnect(self) -> int:
        self.connected = False
        return 0

    def tsfifo_enable_receive_fifo(self) -> None:
        return None

    def tsfifo_clear_can_receive_buffers(self, channel: int) -> int:
        self.clear_calls.append(("can", int(channel)))
        return 0

    def tsfifo_clear_canfd_receive_buffers(self, channel: int) -> int:
        self.clear_calls.append(("canfd", int(channel)))
        return 0

    def tsapp_transmit_can_async(self, frame: FakeTLIBCAN) -> int:
        self.sent_can.append(
            {
                "channel": int(frame.FIdxChn),
                "identifier": int(frame.FIdentifier),
                "properties": int(frame.FProperties),
                "payload": bytes(frame.FData[: int(frame.FDLC)]),
            }
        )
        return 0

    def tsapp_transmit_canfd_async(self, frame: FakeTLIBCANFD) -> int:
        length = DLC_DATA_BYTE_CNT[int(frame.FDLC)]
        self.sent_canfd.append(
            {
                "channel": int(frame.FIdxChn),
                "identifier": int(frame.FIdentifier),
                "properties": int(frame.FProperties),
                "fd_properties": int(frame.FFDProperties),
                "dlc": int(frame.FDLC),
                "payload": bytes(frame.FData[:length]),
            }
        )
        return 0

    def tsfifo_receive_can_msgs(self, buffer, size: c_int32, channel: int, include_tx: bool) -> int:
        queued = self.can_rx.setdefault(int(channel), [])
        count = min(int(size.value), len(queued))
        for index in range(count):
            buffer[index] = queued.pop(0)
        size.value = count
        return 0

    def tsfifo_receive_canfd_msgs(self, buffer, size: c_int32, channel: int, include_tx: bool) -> int:
        queued = self.canfd_rx.setdefault(int(channel), [])
        count = min(int(size.value), len(queued))
        for index in range(count):
            buffer[index] = queued.pop(0)
        size.value = count
        return 0

    def tsfifo_read_can_tx_buffer_frame_count(self, channel: int, count: c_int32) -> int:
        count.value = 0
        return 0

    def tsfifo_read_canfd_tx_buffer_frame_count(self, channel: int, count: c_int32) -> int:
        count.value = 0
        return 0

    def tsapp_get_error_description(self, code: int) -> str:
        return f"error {int(code)}"

    def queue_canfd(self, channel: int, payload: bytes) -> None:
        frame = FakeTLIBCANFD()
        frame.FIdxChn = int(channel)
        frame.FProperties = 0
        frame.FDLC = 0xC
        frame.FFDProperties = 0x03
        frame.FIdentifier = 0x456
        frame.FTimeUs = 123
        for index, value in enumerate(payload):
            frame.FData[index] = value
        self.canfd_rx.setdefault(int(channel), []).append(frame)


class TongxingAdapterTests(unittest.TestCase):
    def _make_device(self, api: FakeTSMasterAPI | None = None, **overrides):
        config = DeviceConfig(
            id="tx0",
            driver="tongxing",
            application="ReplayTool",
            sdk_root="../TSMaster/Windows",
            device_type="TC1014",
            device_index=0,
            project_path="",
        )
        if overrides:
            config = DeviceConfig(**{**config.__dict__, **overrides})
        fake_api = api or FakeTSMasterAPI()
        return TongxingDevice(config, api_module=fake_api, enum_module=FakeEnums), fake_api

    def test_loader_resolves_tsmaster_windows_and_api_package_roots(self) -> None:
        loader = TongxingApiLoader()
        windows_root = Path("..") / "TSMaster" / "Windows"
        api_root = windows_root / "TSMasterApi"

        self.assertEqual((Path.cwd() / windows_root).resolve(), loader._package_parent(str(windows_root)))
        self.assertEqual((Path.cwd() / windows_root).resolve(), loader._package_parent(str(api_root)))

    def test_open_start_send_and_read_canfd_use_wrapper_objects(self) -> None:
        device, api = self._make_device()

        info = device.open()
        device.start_channel(0, ChannelConfig(bus=BusType.CANFD))
        sent = device.send(
            [
                Frame(
                    ts_ns=1000,
                    bus=BusType.CANFD,
                    channel=0,
                    message_id=0x18DAF110,
                    payload=bytes(range(24)),
                    dlc=0xC,
                    extended=True,
                    brs=True,
                )
            ]
        )
        api.queue_canfd(0, bytes(range(24)))
        received = device.read(limit=10)
        device.close()

        self.assertEqual("TC1014", info.name)
        self.assertEqual(1, sent)
        self.assertEqual(1, len(api.sent_canfd))
        self.assertEqual(0xC, api.sent_canfd[0]["dlc"])
        self.assertEqual(bytes(range(24)), api.sent_canfd[0]["payload"])
        self.assertTrue(api.sent_canfd[0]["fd_properties"] & 0x02)
        self.assertEqual(1, len(received))
        self.assertEqual(BusType.CANFD, received[0].bus)
        self.assertTrue(api.finalized)

    def test_project_fallback_reinitializes_after_mapping_error(self) -> None:
        api = FakeTSMasterAPI(mapping_error_once=True)
        device, api = self._make_device(api, project_path="project.tsproj")

        device.start_channel(0, ChannelConfig(bus=BusType.CANFD))

        self.assertEqual("plain", api.initialized[0][0])
        self.assertEqual("project", api.initialized[1][0])
        self.assertEqual(2, len(api.mappings))
        self.assertEqual(1, len(api.canfd_configs))


if __name__ == "__main__":
    unittest.main()
