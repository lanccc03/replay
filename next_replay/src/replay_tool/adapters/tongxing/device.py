from __future__ import annotations

from ctypes import c_int32
import importlib
import platform
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Optional, Sequence

from replay_tool.domain import (
    BusType,
    ChannelConfig,
    DeviceCapabilities,
    DeviceConfig,
    DeviceHealth,
    DeviceInfo,
    Frame,
    canfd_payload_length_from_dlc,
    canfd_payload_length_to_dlc,
)


TX_DRAIN_TIMEOUT_MS = 50
TX_DRAIN_POLL_INTERVAL_S = 0.001
DEFAULT_CHANNEL_COUNT = 4


class TongxingApiLoader:
    def load(self, sdk_root: str) -> tuple[ModuleType, ModuleType | None]:
        package_parent = self._package_parent(sdk_root)
        if str(package_parent) not in sys.path:
            sys.path.insert(0, str(package_parent))
        api = importlib.import_module("TSMasterApi.TSMasterAPI")
        try:
            enums = importlib.import_module("TSMasterApi.TSEnum")
        except ModuleNotFoundError:
            enums = None
        return api, enums

    def _package_parent(self, sdk_root: str) -> Path:
        root = Path(sdk_root)
        if not root.is_absolute():
            root = Path.cwd() / root
        root = root.resolve()
        if root.name.lower() == "tsmasterapi":
            return root.parent
        return root


class TongxingDevice:
    def __init__(
        self,
        config: DeviceConfig,
        *,
        loader: TongxingApiLoader | None = None,
        api_module: ModuleType | Any | None = None,
        enum_module: ModuleType | Any | None = None,
    ) -> None:
        self.config = config
        self.loader = loader or TongxingApiLoader()
        self._api = api_module
        self._enums = enum_module
        self._lock = threading.RLock()
        self._opened = False
        self._connected = False
        self._fifo_enabled = False
        self._device_info: dict[str, Any] | None = None
        self._channel_configs: dict[int, ChannelConfig] = {}
        self._descriptor: DeviceInfo | None = None
        self._project_fallback_used = False

    def open(self) -> DeviceInfo:
        with self._lock:
            if self._descriptor is not None:
                return self._descriptor
            if platform.system() != "Windows" and self._api is None:
                raise RuntimeError("Tongxing adapter requires Windows for real hardware access.")
            self._ensure_api_loaded()
            self._initialize()
            self._device_info = self._find_device()
            channel_count = max(self._query_can_channel_count(), DEFAULT_CHANNEL_COUNT)
            self._descriptor = DeviceInfo(
                id=self.config.id,
                driver="tongxing",
                name=str(self._device_info["device_name"]),
                serial_number=str(self._device_info.get("serial_number", "")),
                channel_count=channel_count,
                metadata={
                    "application": self.config.application,
                    "sdk_root": self.config.sdk_root,
                    "device_type": self.config.device_type,
                    "device_index": self.config.device_index,
                },
            )
            return self._descriptor

    def close(self) -> None:
        with self._lock:
            self._drain_pending_tx()
            for channel in list(self._channel_configs):
                self._clear_receive_buffers(channel)
            self._channel_configs.clear()
            if self._api is not None and self._connected:
                self._check(self._api.tsapp_disconnect(), "disconnect TSMaster")
                self._connected = False
            if self._api is not None and self._opened:
                self._api.finalize_lib_tsmaster()
            self._opened = False
            self._fifo_enabled = False
            self._descriptor = None
            self._device_info = None
            self._project_fallback_used = False

    def enumerate_channels(self) -> Sequence[int]:
        info = self.open()
        return tuple(range(max(int(info.channel_count), DEFAULT_CHANNEL_COUNT)))

    def start_channel(self, physical_channel: int, config: ChannelConfig) -> None:
        self.open()
        with self._lock:
            try:
                self._start_channel_once(int(physical_channel), config)
            except RuntimeError as exc:
                if not self.config.project_path or self._project_fallback_used:
                    raise
                message = str(exc).lower()
                if "mapping" not in message and "channel count" not in message:
                    raise
                self._project_fallback_used = True
                self._initialize(project_path=self.config.project_path)
                self._start_channel_once(int(physical_channel), config)
            self._channel_configs[int(physical_channel)] = config

    def stop_channel(self, physical_channel: int) -> None:
        with self._lock:
            self._channel_configs.pop(int(physical_channel), None)
            self._clear_receive_buffers(int(physical_channel))

    def send(self, frames: Sequence[Frame]) -> int:
        if not frames:
            return 0
        self.open()
        sent = 0
        with self._lock:
            self._ensure_connected()
            for frame in frames:
                self._ensure_channel_started(frame.channel)
                code = self._transmit_async(frame)
                if code in (0, None):
                    sent += 1
                    continue
                if sent:
                    return sent
                self._check(code, f"transmit frame on channel {frame.channel}")
        return sent

    def read(self, limit: int = 256, timeout_ms: int = 0) -> list[Frame]:
        if limit <= 0:
            return []
        self.open()
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
        with self._lock:
            self._ensure_connected()
            while True:
                frames = self._poll_frames(limit)
                if frames or timeout_ms <= 0 or time.monotonic() >= deadline:
                    return sorted(frames, key=lambda item: item.ts_ns)
                time.sleep(0.001)

    def health(self) -> DeviceHealth:
        return DeviceHealth(
            online=self._connected,
            detail="TSMaster connected." if self._connected else "TSMaster disconnected.",
            per_channel={channel: self._connected for channel in sorted(self._channel_configs)},
        )

    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(can=True, canfd=True, async_send=True, fifo_read=True)

    def _ensure_api_loaded(self) -> None:
        if self._api is not None:
            return
        self._api, self._enums = self.loader.load(self.config.sdk_root)

    def _initialize(self, project_path: str = "") -> None:
        assert self._api is not None
        app = self.config.application.encode("utf-8")
        if self._opened:
            if self._connected:
                self._api.tsapp_disconnect()
                self._connected = False
            self._api.finalize_lib_tsmaster()
        if project_path:
            code = self._api.initialize_lib_tsmaster_with_project(app, str(Path(project_path)).encode("utf-8"))
        else:
            code = self._api.initialize_lib_tsmaster(app)
        self._check(code, "initialize TSMaster")
        self._opened = True

    def _start_channel_once(self, physical_channel: int, config: ChannelConfig) -> None:
        self._ensure_can_channel_count(physical_channel + 1)
        self._apply_mapping(physical_channel)
        self._configure_channel(physical_channel, config)
        self._ensure_connected()
        self._enable_fifo()
        self._clear_receive_buffers(physical_channel)

    def _find_device(self) -> dict[str, Any]:
        devices = self._enumerate_devices()
        requested_sub_type = self._enum_value("_TLIB_TS_Device_Sub_Type", self.config.device_type, fallback=8)
        for item in devices:
            if int(item["device_index"]) != int(self.config.device_index):
                continue
            device_name = str(item["device_name"]).lower()
            if self.config.device_type and self.config.device_type.lower() not in device_name:
                if item.get("device_sub_type") != requested_sub_type:
                    continue
            return item
        discovered = ", ".join(f"{item['device_name']}#{item['device_index']}" for item in devices) or "none"
        raise RuntimeError(
            f"Tongxing device {self.config.device_type} index {self.config.device_index} was not found. "
            f"Discovered devices: {discovered}"
        )

    def _enumerate_devices(self) -> list[dict[str, Any]]:
        assert self._api is not None
        count = c_int32(0)
        self._check(self._api.tsapp_enumerate_hw_devices(count), "enumerate Tongxing devices")
        devices: list[dict[str, Any]] = []
        for index in range(max(int(count.value), 0)):
            info = self._api.dll.TLIBHWInfo()
            self._check(self._api.tsapp_get_hw_info_by_index(index, info), f"read Tongxing device {index}")
            name = _decode_c_string(info.FDeviceName)
            devices.append(
                {
                    "device_index": int(info.FDeviceIndex),
                    "device_type": int(info.FDeviceType),
                    "device_sub_type": self._enum_value("_TLIB_TS_Device_Sub_Type", name, fallback=-1),
                    "device_name": name,
                    "serial_number": _decode_c_string(info.FSerialString),
                    "vendor_name": _decode_c_string(info.FVendorName),
                }
            )
        return devices

    def _query_can_channel_count(self) -> int:
        assert self._api is not None
        getter = getattr(self._api, "tsapp_get_can_channel_count", None)
        if getter is None:
            return 0
        count = c_int32(0)
        code = getter(count)
        if code not in (0, None):
            return 0
        return max(int(count.value), 0)

    def _ensure_can_channel_count(self, required_count: int) -> None:
        assert self._api is not None
        if self._query_can_channel_count() >= required_count:
            return
        self._check(self._api.tsapp_set_can_channel_count(int(required_count)), "set CAN channel count")

    def _apply_mapping(self, physical_channel: int) -> None:
        assert self._api is not None
        if self._device_info is None:
            self._device_info = self._find_device()
        code = self._api.tsapp_set_mapping_verbose(
            self.config.application.encode("utf-8"),
            self._enum_value("_TLIBApplicationChannelType", "APP_CAN", fallback=0),
            int(physical_channel),
            str(self._device_info["device_name"]).encode("utf-8"),
            self._enum_value("_TLIBBusToolDeviceType", "TS_USB_DEVICE", fallback=3),
            self._enum_value("_TLIB_TS_Device_Sub_Type", self.config.device_type, fallback=8),
            int(self.config.device_index),
            int(physical_channel),
            True,
        )
        self._check(code, "set TSMaster channel mapping")

    def _configure_channel(self, physical_channel: int, config: ChannelConfig) -> None:
        assert self._api is not None
        nominal_kbps = float(config.nominal_baud) / 1000.0
        if config.bus == BusType.CANFD:
            mode = "lfdmACKOff" if config.listen_only else "lfdmNormal"
            code = self._api.tsapp_configure_baudrate_canfd(
                int(physical_channel),
                nominal_kbps,
                float(config.data_baud) / 1000.0,
                self._enum_value("_TLIBCANFDControllerType", "lfdtISOCAN", fallback=1),
                self._enum_value("_TLIBCANFDControllerMode", mode, fallback=1 if config.listen_only else 0),
                bool(config.resistance_enabled),
            )
            self._check(code, "configure CANFD channel")
            return
        code = self._api.tsapp_configure_baudrate_can(
            int(physical_channel),
            nominal_kbps,
            bool(config.listen_only),
            bool(config.resistance_enabled),
        )
        self._check(code, "configure CAN channel")

    def _ensure_connected(self) -> None:
        assert self._api is not None
        if self._connected:
            return
        self._check(self._api.tsapp_connect(), "connect TSMaster")
        self._connected = True

    def _enable_fifo(self) -> None:
        assert self._api is not None
        if self._fifo_enabled:
            return
        self._api.tsfifo_enable_receive_fifo()
        self._fifo_enabled = True

    def _transmit_async(self, frame: Frame) -> Any:
        assert self._api is not None
        if frame.bus == BusType.CANFD:
            return self._api.tsapp_transmit_canfd_async(self._build_canfd_frame(frame))
        return self._api.tsapp_transmit_can_async(self._build_can_frame(frame))

    def _build_can_frame(self, frame: Frame) -> Any:
        assert self._api is not None
        raw = self._api.dll.TLIBCAN()
        payload = bytes(frame.payload[:8])
        raw.FIdxChn = int(frame.channel)
        raw.FProperties = self._frame_properties(frame)
        raw.FDLC = len(payload)
        raw.FIdentifier = int(frame.message_id) & 0x1FFFFFFF
        raw.FTimeUs = max(int(frame.ts_ns // 1000), 0)
        for index, value in enumerate(payload):
            raw.FData[index] = value
        return raw

    def _build_canfd_frame(self, frame: Frame) -> Any:
        assert self._api is not None
        raw = self._api.dll.TLIBCANFD()
        payload = bytes(frame.payload[:64])
        raw.FIdxChn = int(frame.channel)
        raw.FProperties = self._frame_properties(frame)
        raw.FDLC = canfd_payload_length_to_dlc(len(payload))
        raw.FFDProperties = 0x01 | (0x02 if frame.brs else 0) | (0x04 if frame.esi else 0)
        raw.FIdentifier = int(frame.message_id) & 0x1FFFFFFF
        raw.FTimeUs = max(int(frame.ts_ns // 1000), 0)
        for index, value in enumerate(payload):
            raw.FData[index] = value
        return raw

    def _frame_properties(self, frame: Frame) -> int:
        properties = 0x01
        if frame.extended or int(frame.message_id) > 0x7FF:
            properties |= 0x04
        if frame.remote:
            properties |= 0x02
        return properties

    def _poll_frames(self, limit: int) -> list[Frame]:
        frames: list[Frame] = []
        include_tx = any(config.tx_echo for config in self._channel_configs.values())
        for channel, config in sorted(self._channel_configs.items()):
            remaining = max(min(limit - len(frames), 256), 0)
            if remaining <= 0:
                break
            if config.bus == BusType.CANFD:
                frames.extend(self._receive_canfd(channel, remaining, include_tx))
            remaining = max(min(limit - len(frames), 256), 0)
            if remaining <= 0:
                break
            frames.extend(self._receive_can(channel, remaining, include_tx))
        return frames

    def _receive_can(self, channel: int, requested: int, include_tx: bool) -> list[Frame]:
        assert self._api is not None
        buffer = (self._api.dll.TLIBCAN * requested)()
        size = c_int32(requested)
        self._check(self._api.tsfifo_receive_can_msgs(buffer, size, int(channel), bool(include_tx)), "receive CAN FIFO")
        return [self._convert_can(buffer[index]) for index in range(max(int(size.value), 0))]

    def _receive_canfd(self, channel: int, requested: int, include_tx: bool) -> list[Frame]:
        assert self._api is not None
        buffer = (self._api.dll.TLIBCANFD * requested)()
        size = c_int32(requested)
        self._check(self._api.tsfifo_receive_canfd_msgs(buffer, size, int(channel), bool(include_tx)), "receive CANFD FIFO")
        return [self._convert_canfd(buffer[index]) for index in range(max(int(size.value), 0))]

    def _convert_can(self, frame: Any) -> Frame:
        dlc = min(int(frame.FDLC), 8)
        properties = int(frame.FProperties)
        return Frame(
            ts_ns=int(frame.FTimeUs) * 1000,
            bus=BusType.CAN,
            channel=int(frame.FIdxChn),
            message_id=int(frame.FIdentifier),
            payload=bytes(frame.FData[:dlc]),
            dlc=dlc,
            extended=bool(properties & 0x04),
            remote=bool(properties & 0x02),
            direction="Tx" if properties & 0x01 else "Rx",
        )

    def _convert_canfd(self, frame: Any) -> Frame:
        dlc = int(frame.FDLC)
        length = canfd_payload_length_from_dlc(dlc)
        properties = int(frame.FProperties)
        fd_properties = int(frame.FFDProperties)
        return Frame(
            ts_ns=int(frame.FTimeUs) * 1000,
            bus=BusType.CANFD,
            channel=int(frame.FIdxChn),
            message_id=int(frame.FIdentifier),
            payload=bytes(frame.FData[:length]),
            dlc=dlc,
            extended=bool(properties & 0x04),
            remote=bool(properties & 0x02),
            brs=bool(fd_properties & 0x02),
            esi=bool(fd_properties & 0x04),
            direction="Tx" if properties & 0x01 else "Rx",
        )

    def _clear_receive_buffers(self, physical_channel: int) -> None:
        assert self._api is not None
        for name in ("tsfifo_clear_can_receive_buffers", "tsfifo_clear_canfd_receive_buffers"):
            function = getattr(self._api, name, None)
            if function is None:
                continue
            code = function(int(physical_channel))
            if code not in (0, None):
                continue

    def _drain_pending_tx(self) -> None:
        if self._api is None or not self._connected:
            return
        deadline = time.monotonic() + TX_DRAIN_TIMEOUT_MS / 1000.0
        while self._channel_configs:
            pending = 0
            for channel, config in self._channel_configs.items():
                pending += self._tx_count(channel, config.bus)
            if pending <= 0 or time.monotonic() >= deadline:
                return
            time.sleep(TX_DRAIN_POLL_INTERVAL_S)

    def _tx_count(self, channel: int, bus: BusType) -> int:
        assert self._api is not None
        name = "tsfifo_read_canfd_tx_buffer_frame_count" if bus == BusType.CANFD else "tsfifo_read_can_tx_buffer_frame_count"
        function = getattr(self._api, name, None)
        if function is None:
            return 0
        count = c_int32(0)
        code = function(int(channel), count)
        if code not in (0, None):
            return 0
        return max(int(count.value), 0)

    def _ensure_channel_started(self, physical_channel: int) -> None:
        if int(physical_channel) not in self._channel_configs:
            raise RuntimeError(f"Tongxing channel {physical_channel} is not started.")

    def _enum_value(self, enum_class_name: str, member_name: str, *, fallback: int) -> int:
        enum_class = getattr(self._enums, enum_class_name, None) if self._enums is not None else None
        if enum_class is None:
            return int(fallback)
        if hasattr(enum_class, member_name):
            return int(getattr(enum_class, member_name))
        for member in enum_class:
            if member.name.lower() == str(member_name).lower():
                return int(member)
        return int(fallback)

    def _check(self, code: Any, action: str) -> None:
        if code in (0, None):
            return
        detail = self._describe_error(code)
        raise RuntimeError(f"{action} failed (code {int(code)}): {detail}")

    def _describe_error(self, code: Any) -> str:
        if self._api is None:
            return str(code)
        describe = getattr(self._api, "tsapp_get_error_description", None)
        if describe is None:
            return str(code)
        try:
            return str(describe(int(code)))
        except Exception:
            return str(code)


def _decode_c_string(value: Any) -> str:
    raw = bytes(value)
    return raw.split(b"\x00", 1)[0].decode("utf-8", "ignore")
