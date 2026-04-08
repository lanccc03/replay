from __future__ import annotations

import threading
import unittest
from ctypes import Structure, c_char, c_int32, c_int64, c_uint8
from enum import IntEnum
from unittest.mock import patch

import tests.bootstrap  # noqa: F401

from replay_platform.adapters.tongxing import TongxingDeviceAdapter
from replay_platform.core import BusType, ChannelConfig, DeviceChannelBinding, FrameEvent


_DLC_DATA_BYTE_CNT = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)


def _write_int_pointer(pointer, value: int) -> None:
    try:
        pointer.contents.value = int(value)
    except AttributeError:
        pointer._obj.value = int(value)


class _FakeTLIBHWInfo(Structure):
    _pack_ = 1
    _fields_ = [
        ("FDeviceType", c_int32),
        ("FDeviceIndex", c_int32),
        ("FVendorName", c_char * 32),
        ("FDeviceName", c_char * 32),
        ("FSerialString", c_char * 64),
    ]


class _FakeTLIBCAN(Structure):
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


class _FakeTLIBCANFD(Structure):
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


class _FakeStructModule:
    TLIBHWInfo = _FakeTLIBHWInfo
    TLIBCAN = _FakeTLIBCAN
    TLIBCANFD = _FakeTLIBCANFD
    DLC_DATA_BYTE_CNT = _DLC_DATA_BYTE_CNT


class _FakeApplicationChannelType(IntEnum):
    APP_CAN = 0


class _FakeBusToolDeviceType(IntEnum):
    TS_USB_DEVICE = 3


class _FakeDeviceSubType(IntEnum):
    TS_UNKNOWN_DEVICE = 0
    TC1014 = 8
    TC1016 = 11


class _FakeCANFDControllerType(IntEnum):
    lfdtISOCAN = 1


class _FakeCANFDControllerMode(IntEnum):
    lfdmNormal = 0
    lfdmACKOff = 1


class _FakeEnumModule:
    _TLIBApplicationChannelType = _FakeApplicationChannelType
    _TLIBBusToolDeviceType = _FakeBusToolDeviceType
    _TLIB_TS_Device_Sub_Type = _FakeDeviceSubType
    _TLIBCANFDControllerType = _FakeCANFDControllerType
    _TLIBCANFDControllerMode = _FakeCANFDControllerMode


class _FakeTsApi:
    def __init__(
        self,
        *,
        channel_count: int = 4,
        mapping_error_code: int | None = None,
        can_sync_error_code: int | None = None,
        canfd_sync_error_code: int | None = None,
    ) -> None:
        self.channel_count = channel_count
        self.mapping_error_code = mapping_error_code
        self.can_sync_error_code = can_sync_error_code
        self.canfd_sync_error_code = canfd_sync_error_code
        self.use_project_mapping = False
        self.devices = [
            {
                "device_type": int(_FakeBusToolDeviceType.TS_USB_DEVICE),
                "device_index": 0,
                "vendor_name": b"TOSUN",
                "device_name": b"TC1014",
                "serial_number": b"TX-001",
            }
        ]
        self.set_channel_count_calls: list[int] = []
        self.mappings: list[dict] = []
        self.can_configs: list[dict] = []
        self.canfd_configs: list[dict] = []
        self.sent_can: list[dict] = []
        self.sent_canfd: list[dict] = []
        self.sent_can_async: list[dict] = []
        self.sent_canfd_async: list[dict] = []
        self.can_rx: dict[int, list[_FakeTLIBCAN]] = {}
        self.canfd_rx: dict[int, list[_FakeTLIBCANFD]] = {}
        self.fifo_enabled = False
        self.clear_calls: list[tuple[str, int]] = []

    def tsapp_enumerate_hw_devices(self, count_ptr) -> int:
        _write_int_pointer(count_ptr, len(self.devices))
        return 0

    def tsapp_get_hw_info_by_index(self, index: int, info_ptr) -> int:
        info = info_ptr._obj
        device = self.devices[int(index)]
        info.FDeviceType = int(device["device_type"])
        info.FDeviceIndex = int(device["device_index"])
        info.FVendorName = device["vendor_name"]
        info.FDeviceName = device["device_name"]
        info.FSerialString = device["serial_number"]
        return 0

    def tsapp_get_can_channel_count(self, count_ptr) -> int:
        _write_int_pointer(count_ptr, self.channel_count)
        return 0

    def tsapp_set_can_channel_count(self, count: int) -> int:
        self.channel_count = int(count)
        self.set_channel_count_calls.append(int(count))
        return 0

    def tsapp_set_mapping_verbose(
        self,
        app_name,
        app_channel_type,
        app_channel,
        device_name,
        device_type,
        device_sub_type,
        device_index,
        physical_channel,
        enabled,
    ) -> int:
        self.mappings.append(
            {
                "application": bytes(app_name).decode("utf-8", "ignore"),
                "app_channel_type": int(app_channel_type),
                "app_channel": int(app_channel),
                "device_name": bytes(device_name).decode("utf-8", "ignore"),
                "device_type": int(device_type),
                "device_sub_type": int(device_sub_type),
                "device_index": int(device_index),
                "physical_channel": int(physical_channel),
                "enabled": bool(enabled),
            }
        )
        if self.mapping_error_code is not None and not self.use_project_mapping:
            return int(self.mapping_error_code)
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

    def tsfifo_enable_receive_fifo(self) -> None:
        self.fifo_enabled = True

    def tsfifo_clear_can_receive_buffers(self, channel: int) -> int:
        self.clear_calls.append(("can", int(channel)))
        self.can_rx[int(channel)] = []
        return 0

    def tsfifo_clear_canfd_receive_buffers(self, channel: int) -> int:
        self.clear_calls.append(("canfd", int(channel)))
        self.canfd_rx[int(channel)] = []
        return 0

    def tsapp_transmit_can_async(self, frame_ptr) -> int:
        frame = frame_ptr._obj
        self.sent_can_async.append(
            {
                "channel": int(frame.FIdxChn),
                "properties": int(frame.FProperties),
                "dlc": int(frame.FDLC),
                "identifier": int(frame.FIdentifier),
                "timestamp_us": int(frame.FTimeUs),
                "payload": bytes(frame.FData[: int(frame.FDLC)]),
            }
        )
        return 0

    def tsapp_transmit_canfd_async(self, frame_ptr) -> int:
        frame = frame_ptr._obj
        payload_length = _DLC_DATA_BYTE_CNT[int(frame.FDLC)]
        self.sent_canfd_async.append(
            {
                "channel": int(frame.FIdxChn),
                "properties": int(frame.FProperties),
                "dlc": int(frame.FDLC),
                "fd_properties": int(frame.FFDProperties),
                "identifier": int(frame.FIdentifier),
                "timestamp_us": int(frame.FTimeUs),
                "payload": bytes(frame.FData[:payload_length]),
            }
        )
        return 0

    def tsapp_transmit_can_sync(self, frame_ptr, timeout_ms: int) -> int:
        frame = frame_ptr._obj
        self.sent_can.append(
            {
                "channel": int(frame.FIdxChn),
                "properties": int(frame.FProperties),
                "dlc": int(frame.FDLC),
                "identifier": int(frame.FIdentifier),
                "timestamp_us": int(frame.FTimeUs),
                "payload": bytes(frame.FData[: int(frame.FDLC)]),
                "timeout_ms": int(timeout_ms),
            }
        )
        if self.can_sync_error_code is not None:
            return int(self.can_sync_error_code)
        return 0

    def tsapp_transmit_canfd_sync(self, frame_ptr, timeout_ms: int) -> int:
        frame = frame_ptr._obj
        payload_length = _DLC_DATA_BYTE_CNT[int(frame.FDLC)]
        self.sent_canfd.append(
            {
                "channel": int(frame.FIdxChn),
                "properties": int(frame.FProperties),
                "dlc": int(frame.FDLC),
                "fd_properties": int(frame.FFDProperties),
                "identifier": int(frame.FIdentifier),
                "timestamp_us": int(frame.FTimeUs),
                "payload": bytes(frame.FData[:payload_length]),
                "timeout_ms": int(timeout_ms),
            }
        )
        if self.canfd_sync_error_code is not None:
            return int(self.canfd_sync_error_code)
        return 0

    def tsfifo_receive_can_msgs(self, buffer, size_ptr, channel: int, _include_tx: bool) -> int:
        requested = int(size_ptr._obj.value)
        queued = self.can_rx.setdefault(int(channel), [])
        count = min(len(queued), requested)
        for index in range(count):
            buffer[index] = queued.pop(0)
        _write_int_pointer(size_ptr, count)
        return 0

    def tsfifo_receive_canfd_msgs(self, buffer, size_ptr, channel: int, _include_tx: bool) -> int:
        requested = int(size_ptr._obj.value)
        queued = self.canfd_rx.setdefault(int(channel), [])
        count = min(len(queued), requested)
        for index in range(count):
            buffer[index] = queued.pop(0)
        _write_int_pointer(size_ptr, count)
        return 0

    def queue_can(self, channel: int, message_id: int, payload: bytes, *, ts_us: int, properties: int = 0) -> None:
        frame = _FakeTLIBCAN()
        frame.FIdxChn = int(channel)
        frame.FProperties = int(properties)
        frame.FDLC = min(len(payload), 8)
        frame.FIdentifier = int(message_id)
        frame.FTimeUs = int(ts_us)
        for index, value in enumerate(payload[:8]):
            frame.FData[index] = value
        self.can_rx.setdefault(int(channel), []).append(frame)

    def queue_canfd(
        self,
        channel: int,
        message_id: int,
        payload: bytes,
        *,
        dlc: int,
        ts_us: int,
        properties: int = 0,
        fd_properties: int = 0x01,
    ) -> None:
        frame = _FakeTLIBCANFD()
        frame.FIdxChn = int(channel)
        frame.FProperties = int(properties)
        frame.FDLC = int(dlc)
        frame.FFDProperties = int(fd_properties)
        frame.FIdentifier = int(message_id)
        frame.FTimeUs = int(ts_us)
        for index, value in enumerate(payload[:64]):
            frame.FData[index] = value
        self.canfd_rx.setdefault(int(channel), []).append(frame)


class _FakeRuntime:
    def __init__(self, api: _FakeTsApi | None = None) -> None:
        self.lock = threading.RLock()
        self.connected = False
        self.ts_api = api or _FakeTsApi()
        self.ts_struct = _FakeStructModule
        self.ts_enum = _FakeEnumModule
        self.prepare_calls: list[tuple[str, str]] = []
        self.activate_calls: list[str] = []
        self.ensure_connected_calls = 0
        self.disconnect_calls = 0
        self.reinitialize_calls: list[tuple[str, str, str]] = []
        self.release_calls = 0

    def prepare(self, sdk_root: str, application_name: str) -> None:
        self.prepare_calls.append((sdk_root, application_name))

    def activate_application(self, application_name: str) -> None:
        self.activate_calls.append(application_name)

    def ensure_connected(self) -> None:
        self.connected = True
        self.ensure_connected_calls += 1

    def disconnect(self, ignore_errors: bool = False) -> None:
        _ = ignore_errors
        self.connected = False
        self.disconnect_calls += 1

    def reinitialize_with_project(self, sdk_root: str, application_name: str, project_path: str) -> None:
        self.reinitialize_calls.append((sdk_root, application_name, project_path))
        self.ts_api.use_project_mapping = True
        self.connected = False

    def release(self) -> None:
        self.release_calls += 1
        self.connected = False

    def describe_error(self, code) -> str:
        return f"error {int(code)}"


class TongxingAdapterTests(unittest.TestCase):
    def _make_binding(self, **overrides) -> DeviceChannelBinding:
        defaults = {
            "adapter_id": "tongxing0",
            "driver": "tongxing",
            "logical_channel": 0,
            "physical_channel": 0,
            "bus_type": BusType.CANFD,
            "device_type": "TC1014",
            "device_index": 0,
            "sdk_root": "TSMasterApi",
            "nominal_baud": 500000,
            "data_baud": 2000000,
            "resistance_enabled": True,
            "listen_only": False,
            "tx_echo": False,
            "merge_receive": False,
            "network": {},
            "metadata": {},
        }
        defaults.update(overrides)
        return DeviceChannelBinding(**defaults)

    def _make_adapter(self, binding: DeviceChannelBinding | None = None, api: _FakeTsApi | None = None):
        seed_binding = binding or self._make_binding()
        runtime = _FakeRuntime(api)
        adapter = TongxingDeviceAdapter(seed_binding.adapter_id, seed_binding)
        adapter._runtime = runtime
        return adapter, runtime

    @patch("replay_platform.adapters.tongxing.platform.system", return_value="Windows")
    def test_open_builds_descriptor_and_enumerates_channels(self, _platform_system) -> None:
        binding = self._make_binding(metadata={"ts_application": "BenchApp"})
        adapter, runtime = self._make_adapter(binding)

        descriptor = adapter.open()
        channels = adapter.enumerate_channels()

        self.assertEqual(("TSMasterApi", "BenchApp"), runtime.prepare_calls[0])
        self.assertEqual("tongxing", descriptor.driver)
        self.assertEqual("TC1014", descriptor.name)
        self.assertEqual("TX-001", descriptor.serial_number)
        self.assertEqual(4, descriptor.channel_count)
        self.assertEqual("BenchApp", descriptor.metadata["application_name"])
        self.assertEqual("TS_USB_DEVICE", descriptor.metadata["device_type"])
        self.assertEqual("TC1014", descriptor.metadata["device_sub_type"])
        self.assertEqual([0, 1, 2, 3], [item.physical_channel for item in channels])

    @patch("replay_platform.adapters.tongxing.platform.system", return_value="Windows")
    def test_start_channel_uses_project_fallback_after_mapping_error(self, _platform_system) -> None:
        binding = self._make_binding(
            physical_channel=2,
            metadata={"ts_project_path": "project.tsproj"},
        )
        api = _FakeTsApi(channel_count=1, mapping_error_code=82)
        adapter, runtime = self._make_adapter(binding, api)

        adapter.start_channel(2, binding.channel_config())

        self.assertTrue(adapter._project_fallback_used)
        self.assertEqual(1, len(runtime.reinitialize_calls))
        self.assertTrue(runtime.reinitialize_calls[0][2].endswith("project.tsproj"))
        self.assertEqual([3], api.set_channel_count_calls)
        self.assertEqual(2, api.mappings[-1]["app_channel"])
        self.assertEqual(2, api.canfd_configs[-1]["channel"])
        self.assertIn(2, adapter._started_channels)

    @patch("replay_platform.adapters.tongxing.platform.system", return_value="Windows")
    def test_send_canfd_uses_actual_payload_length_and_brs(self, _platform_system) -> None:
        binding = self._make_binding(bus_type=BusType.CANFD)
        adapter, runtime = self._make_adapter(binding)
        adapter.start_channel(0, binding.channel_config())

        payload = bytes(range(24))
        event = FrameEvent(
            ts_ns=123_000,
            bus_type=BusType.CANFD,
            channel=0,
            message_id=0x18DAF110,
            payload=payload,
            dlc=0xF,
            flags={"brs": True, "extended": True},
        )

        sent = adapter.send([event])

        self.assertEqual(1, sent)
        self.assertGreaterEqual(runtime.ensure_connected_calls, 2)
        self.assertEqual(0xC, runtime.ts_api.sent_canfd[0]["dlc"])
        self.assertEqual(payload, runtime.ts_api.sent_canfd[0]["payload"])
        self.assertEqual(50, runtime.ts_api.sent_canfd[0]["timeout_ms"])
        self.assertTrue(runtime.ts_api.sent_canfd[0]["properties"] & 0x04)
        self.assertTrue(runtime.ts_api.sent_canfd[0]["fd_properties"] & 0x01)
        self.assertTrue(runtime.ts_api.sent_canfd[0]["fd_properties"] & 0x02)
        self.assertEqual([], runtime.ts_api.sent_canfd_async)

    @patch("replay_platform.adapters.tongxing.platform.system", return_value="Windows")
    def test_send_canfd_sync_timeout_raises_error_and_does_not_fall_back_to_async(self, _platform_system) -> None:
        binding = self._make_binding(bus_type=BusType.CANFD)
        api = _FakeTsApi(canfd_sync_error_code=408)
        adapter, runtime = self._make_adapter(binding, api)
        adapter.start_channel(0, binding.channel_config())

        event = FrameEvent(
            ts_ns=456_000,
            bus_type=BusType.CANFD,
            channel=0,
            message_id=0x18DAF110,
            payload=bytes(range(12)),
            dlc=0x9,
            flags={"extended": True},
        )

        with self.assertRaisesRegex(Exception, "transmit frame on channel 0 failed"):
            adapter.send([event])

        self.assertEqual(1, len(runtime.ts_api.sent_canfd))
        self.assertEqual(50, runtime.ts_api.sent_canfd[0]["timeout_ms"])
        self.assertEqual([], runtime.ts_api.sent_canfd_async)

    @patch("replay_platform.adapters.tongxing.platform.system", return_value="Windows")
    def test_read_returns_sorted_frames_across_started_channels(self, _platform_system) -> None:
        binding = self._make_binding(physical_channel=2)
        adapter, runtime = self._make_adapter(binding)
        adapter.start_channel(0, ChannelConfig(bus_type=BusType.CAN))
        adapter.start_channel(1, ChannelConfig(bus_type=BusType.J1939))
        adapter.start_channel(2, ChannelConfig(bus_type=BusType.CANFD))

        runtime.ts_api.queue_can(1, 0x18FEF100, bytes(range(8)), ts_us=100, properties=0x04)
        runtime.ts_api.queue_can(0, 0x123, bytes.fromhex("11223344"), ts_us=200, properties=0x01)
        runtime.ts_api.queue_canfd(2, 0x456, bytes(range(24)), dlc=0xC, ts_us=300, fd_properties=0x03)

        events = adapter.read(limit=10)

        self.assertEqual([100_000, 200_000, 300_000], [event.ts_ns for event in events])
        self.assertEqual(BusType.J1939, events[0].bus_type)
        self.assertTrue(events[0].flags["extended"])
        self.assertEqual(BusType.CAN, events[1].bus_type)
        self.assertEqual("Tx", events[1].flags["direction"])
        self.assertEqual(BusType.CANFD, events[2].bus_type)
        self.assertEqual(0xC, events[2].dlc)
        self.assertEqual(24, len(events[2].payload))

    @patch("replay_platform.adapters.tongxing.platform.system", return_value="Windows")
    def test_health_reconnect_stop_and_close_use_runtime_state(self, _platform_system) -> None:
        binding = self._make_binding(bus_type=BusType.CAN)
        adapter, runtime = self._make_adapter(binding)

        adapter.open()
        offline_health = adapter.health()
        adapter.start_channel(0, binding.channel_config())
        online_health = adapter.health()
        adapter.reconnect()
        adapter.stop_channel(0)
        stopped_health = adapter.health()
        adapter.close()
        closed_health = adapter.health()

        self.assertFalse(offline_health.online)
        self.assertTrue(online_health.online)
        self.assertEqual({0: True}, online_health.per_channel)
        self.assertEqual(1, runtime.disconnect_calls)
        self.assertEqual(2, len(runtime.ts_api.can_configs))
        self.assertEqual({}, stopped_health.per_channel)
        self.assertEqual(1, runtime.release_calls)
        self.assertFalse(closed_health.online)


if __name__ == "__main__":
    unittest.main()
