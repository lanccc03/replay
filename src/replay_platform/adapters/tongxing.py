from __future__ import annotations


from typing import List, Optional, Sequence

from replay_platform.adapters.base import DeviceAdapter
from replay_platform.core import (
    AdapterCapabilities,
    AdapterHealth,
    ChannelConfig,
    ChannelDescriptor,
    DeviceDescriptor,
    FrameEvent,
)
from replay_platform.errors import AdapterOperationError


import importlib
import os
import platform
import sys
import threading
import time
from ctypes import byref, c_char_p, c_int32
from pathlib import Path
from typing import Any, Dict, Union

from replay_platform.core import BusType, DeviceChannelBinding, canfd_payload_length_to_dlc
from replay_platform.errors import ConfigurationError


_DEFAULT_SDK_ROOT = "TSMasterApi"
_DEFAULT_FALLBACK_CHANNEL_COUNT = 8
_MAX_FIFO_READ = 256


class _TSMasterRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._owners = 0
        self._initialized = False
        self._connected = False
        self._ts_api = None
        self._ts_struct = None
        self._ts_enum = None
        self._dll_search_handle = None
        self._sdk_bin_dir: Optional[Path] = None

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def ts_api(self):
        if self._ts_api is None:
            raise AdapterOperationError("TSMaster API is not loaded.")
        return self._ts_api

    @property
    def ts_struct(self):
        if self._ts_struct is None:
            raise AdapterOperationError("TSMaster structs are not loaded.")
        return self._ts_struct

    @property
    def ts_enum(self):
        if self._ts_enum is None:
            raise AdapterOperationError("TSMaster enums are not loaded.")
        return self._ts_enum

    def prepare(self, sdk_root: str, application_name: str) -> None:
        with self._lock:
            if not self._initialized:
                self._initialize_library(sdk_root, application_name)
            else:
                self.activate_application(application_name)
            self._owners += 1

    def activate_application(self, application_name: str) -> None:
        app_bytes = application_name.encode("utf-8")
        code = self.ts_api.tsapp_set_current_application(app_bytes)
        if code in (0, None):
            return
        add_code = self.ts_api.tsapp_add_application(app_bytes)
        if add_code not in (0, None):
            code = self.ts_api.tsapp_set_current_application(app_bytes)
        else:
            code = self.ts_api.tsapp_set_current_application(app_bytes)
        self._check_code(code, f"select TSMaster application {application_name}")

    def ensure_connected(self) -> None:
        if self._connected:
            return
        code = self.ts_api.tsapp_connect()
        self._check_code(code, "connect TSMaster")
        self._connected = True

    def disconnect(self, ignore_errors: bool = False) -> None:
        if not self._initialized or not self._connected:
            self._connected = False
            return
        code = self.ts_api.tsapp_disconnect()
        if not ignore_errors:
            self._check_code(code, "disconnect TSMaster")
        self._connected = False

    def reinitialize_with_project(self, sdk_root: str, application_name: str, project_path: str) -> None:
        with self._lock:
            self._initialize_library(sdk_root, application_name, project_path=project_path)

    def release(self) -> None:
        with self._lock:
            if self._owners > 0:
                self._owners -= 1
            if self._owners > 0 or not self._initialized:
                return
            self.disconnect(ignore_errors=True)
            try:
                self.ts_api.finalize_lib_tsmaster()
            except Exception:
                pass
            self._initialized = False

    def describe_error(self, code: Any) -> str:
        if code in (None, 0):
            return "OK"
        if self._ts_api is None:
            return f"TSMaster error {int(code)}"
        try:
            description = c_char_p()
            result = self.ts_api.tsapp_get_error_description(int(code), byref(description))
            if result == 0 and description.value:
                return description.value.decode("utf-8", "ignore")
        except Exception:
            pass
        return f"TSMaster error {int(code)}"

    def _initialize_library(
        self,
        sdk_root: str,
        application_name: str,
        *,
        project_path: Optional[str] = None,
    ) -> None:
        self._ensure_modules_loaded(sdk_root)
        if self._initialized:
            self.disconnect(ignore_errors=True)
            try:
                self.ts_api.finalize_lib_tsmaster()
            except Exception:
                pass
        self._set_library_location(sdk_root)
        app_bytes = application_name.encode("utf-8")
        if project_path:
            project = str(_normalize_path(project_path)).encode("utf-8")
            code = self.ts_api.initialize_lib_tsmaster_with_project(app_bytes, project)
            self._check_code(code, f"initialize TSMaster with project {project_path}")
        else:
            code = self.ts_api.initialize_lib_tsmaster(app_bytes)
            self._check_code(code, f"initialize TSMaster application {application_name}")
        self._initialized = True
        self._connected = False
        self.activate_application(application_name)

    def _ensure_modules_loaded(self, sdk_root: str) -> None:
        if self._ts_api is not None and self._ts_struct is not None and self._ts_enum is not None:
            return
        package_root = self._resolve_python_package_root(sdk_root)
        if package_root is not None and str(package_root) not in sys.path:
            sys.path.insert(0, str(package_root))
        self._sdk_bin_dir = self._resolve_sdk_bin_dir(sdk_root)
        self._configure_dll_search(self._sdk_bin_dir)
        self._ts_api = importlib.import_module("TSMasterApi.TSAPI")
        self._ts_struct = importlib.import_module("TSMasterApi.TSStruct")
        self._ts_enum = importlib.import_module("TSMasterApi.TSEnum")

    def _resolve_python_package_root(self, sdk_root: str) -> Optional[Path]:
        for candidate in _candidate_sdk_paths(sdk_root):
            if (candidate / "__init__.py").exists() and (candidate / "TSAPI.py").exists():
                return candidate.parent
        return None

    def _resolve_sdk_bin_dir(self, sdk_root: str) -> Optional[Path]:
        for candidate in _candidate_sdk_paths(sdk_root):
            for folder in (
                candidate,
                candidate / "bin64",
                candidate / "bin",
                candidate / "windows" / "bin64",
                candidate / "windows" / "bin",
            ):
                if _contains_tsmaster_dll(folder):
                    return folder.resolve()
        registry_path = _registry_sdk_bin_dir()
        if registry_path is not None:
            return registry_path
        return None

    def _configure_dll_search(self, sdk_bin_dir: Optional[Path]) -> None:
        if sdk_bin_dir is None or not sdk_bin_dir.exists():
            return
        try:
            if hasattr(os, "add_dll_directory"):
                self._dll_search_handle = os.add_dll_directory(str(sdk_bin_dir))
                return
        except (FileNotFoundError, OSError):
            pass
        try:
            import ctypes

            ctypes.windll.kernel32.SetDllDirectoryW(str(sdk_bin_dir))
        except Exception:
            return

    def _set_library_location(self, sdk_root: str) -> None:
        if self._ts_api is None:
            return
        location = self._sdk_bin_dir or self._resolve_sdk_bin_dir(sdk_root)
        if location is None:
            return
        setter = getattr(self._ts_api, "set_libtsmaster_location", None)
        if setter is None:
            return
        try:
            setter(str(location).encode("utf-8"))
        except Exception:
            return

    def _check_code(self, code: Any, action: str) -> None:
        if code in (0, None):
            return
        raise AdapterOperationError(f"{action} failed: {self.describe_error(code)}")


_RUNTIME = _TSMasterRuntime()


class TongxingDeviceAdapter(DeviceAdapter):
    """Tongxing/TSMaster adapter using the official TSMaster DLL exports."""

    def __init__(self, adapter_id: str, seed_binding: DeviceChannelBinding) -> None:
        super().__init__(adapter_id)
        self.seed_binding = seed_binding
        self._runtime = _RUNTIME
        self._descriptor: Optional[DeviceDescriptor] = None
        self._channel_configs: Dict[int, ChannelConfig] = {}
        self._started_channels: set[int] = set()
        self._opened = False
        self._fifo_enabled = False
        self._project_fallback_used = False
        self._device_info: Optional[dict[str, Any]] = None
        self._application_name = str(seed_binding.metadata.get("ts_application") or adapter_id)
        self._project_path = _normalize_optional_path(seed_binding.metadata.get("ts_project_path"))
        self._fallback_channel_count = max(
            int(seed_binding.metadata.get("channel_count", 0) or 0),
            int(seed_binding.metadata.get("fallback_channel_count", 0) or 0),
            int(seed_binding.physical_channel) + 1,
        )

    def open(self) -> DeviceDescriptor:
        if self._descriptor is not None:
            return self._descriptor
        if platform.system() != "Windows":
            raise AdapterOperationError("Tongxing adapter only supports Windows.")
        with self._runtime.lock:
            if not self._opened:
                self._runtime.prepare(self.seed_binding.sdk_root, self._application_name)
                self._opened = True
            self._activate_application_locked()
            self._device_info = self._find_device_locked()
            channel_count = max(self._query_can_channel_count_locked(), self._fallback_channel_count)
            display_name = str(self.seed_binding.metadata.get("hw_device_name") or self._device_info["device_name"])
            display_device_type = str(self.seed_binding.metadata.get("hw_device_type") or self._device_info["device_type_name"])
            display_device_sub_type = str(
                self.seed_binding.metadata.get("hw_device_sub_type")
                or self._device_info["device_sub_type_name"]
                or self.seed_binding.device_type
            )
            self._descriptor = DeviceDescriptor(
                adapter_id=self.adapter_id,
                driver="tongxing",
                name=display_name,
                serial_number=self._device_info["serial_number"],
                channel_count=channel_count,
                metadata={
                    "application_name": self._application_name,
                    "device_index": self._device_info["device_index"],
                    "device_type": display_device_type,
                    "device_sub_type": display_device_sub_type,
                    "hw_device_name": display_name,
                    "hw_device_type": display_device_type,
                    "hw_device_sub_type": display_device_sub_type,
                    "sdk_root": self.seed_binding.sdk_root,
                    "ts_project_path": self._project_path or "",
                },
            )
            return self._descriptor

    def close(self) -> None:
        with self._runtime.lock:
            for physical_channel in list(self._started_channels):
                self._clear_receive_buffers_locked(physical_channel)
            self._channel_configs.clear()
            self._started_channels.clear()
            self._fifo_enabled = False
            self._project_fallback_used = False
            self._device_info = None
            self._descriptor = None
            if self._opened:
                self._runtime.release()
                self._opened = False

    def enumerate_channels(self) -> Sequence[ChannelDescriptor]:
        descriptor = self.open()
        return [
            ChannelDescriptor(
                logical_channel=index,
                physical_channel=index,
                bus_type=self.seed_binding.bus_type,
                label=f"TSMaster CH{index}",
            )
            for index in range(max(int(descriptor.channel_count), self._fallback_channel_count))
        ]

    def start_channel(self, physical_channel: int, config: ChannelConfig) -> None:
        self.open()
        if config.bus_type == BusType.ETH:
            raise AdapterOperationError("Tongxing adapter does not support raw ETH replay.")
        with self._runtime.lock:
            self._activate_application_locked()
            self._runtime.ensure_connected()
            try:
                self._start_channel_locked(physical_channel, config)
            except AdapterOperationError as exc:
                if not self._should_retry_with_project(exc):
                    raise
                self._runtime.reinitialize_with_project(
                    self.seed_binding.sdk_root,
                    self._application_name,
                    self._project_path,
                )
                self._project_fallback_used = True
                self._activate_application_locked()
                self._runtime.ensure_connected()
                try:
                    self._start_channel_locked(physical_channel, config)
                except AdapterOperationError as retry_exc:
                    raise AdapterOperationError(
                        f"{retry_exc} Consider checking metadata.ts_project_path or the TSMaster application mapping."
                    ) from retry_exc
            self._channel_configs[physical_channel] = config
            self._started_channels.add(physical_channel)

    def stop_channel(self, physical_channel: int) -> None:
        with self._runtime.lock:
            self._channel_configs.pop(physical_channel, None)
            self._started_channels.discard(physical_channel)
            if self._opened:
                self._activate_application_locked()
                self._clear_receive_buffers_locked(physical_channel)

    def send(self, batch: Sequence[FrameEvent]) -> int:
        if not batch:
            return 0
        self.open()
        sent = 0
        with self._runtime.lock:
            self._activate_application_locked()
            self._runtime.ensure_connected()
            for event in batch:
                self._ensure_channel_started(event.channel)
                code = self._transmit_event_locked(event)
                if code in (0, None):
                    sent += 1
                    continue
                if sent > 0:
                    return sent
                raise self._build_operation_error(code, f"transmit frame on channel {event.channel}")
        return sent

    def read(self, limit: int = 256, timeout_ms: int = 0) -> List[FrameEvent]:
        if limit <= 0 or not self._started_channels:
            return []
        self.open()
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
        with self._runtime.lock:
            self._activate_application_locked()
            self._runtime.ensure_connected()
            while True:
                events = self._poll_events_locked(limit)
                if events or timeout_ms <= 0 or time.monotonic() >= deadline:
                    return sorted(events, key=lambda item: item.ts_ns)
                time.sleep(0.001)

    def health(self) -> AdapterHealth:
        with self._runtime.lock:
            if not self._opened:
                return AdapterHealth(online=False, detail="TSMaster not initialized.")
            online = self._runtime.connected
            detail = "TSMaster connected." if online else "TSMaster not connected."
            return AdapterHealth(
                online=online,
                detail=detail,
                per_channel={channel: online for channel in sorted(self._started_channels)},
            )

    def reconnect(self, physical_channel: Optional[int] = None) -> None:
        self.open()
        with self._runtime.lock:
            self._activate_application_locked()
            self._runtime.disconnect(ignore_errors=True)
            self._runtime.ensure_connected()
            if physical_channel is None:
                items = sorted(self._channel_configs.items())
            else:
                config = self._channel_configs.get(physical_channel)
                if config is None:
                    raise ConfigurationError(f"Channel {physical_channel} has no stored Tongxing config.")
                items = [(physical_channel, config)]
            for channel, config in items:
                self._start_channel_locked(channel, config)

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            can=True,
            canfd=True,
            j1939=True,
            can_uds=True,
        )

    def _activate_application_locked(self) -> None:
        self._runtime.activate_application(self._application_name)

    def _find_device_locked(self) -> dict[str, Any]:
        devices = self._enumerate_devices_locked()
        requested_device_type = self._resolve_bus_tool_device_type(self.seed_binding.metadata.get("hw_device_type"))
        requested_sub_type = self._resolve_device_sub_type(
            self.seed_binding.metadata.get("hw_device_sub_type", self.seed_binding.device_type)
        )
        requested_name = str(self.seed_binding.metadata.get("hw_device_name") or "").strip()
        for device in devices:
            if int(device["device_index"]) != int(self.seed_binding.device_index):
                continue
            if requested_device_type is not None and device["device_type"] != requested_device_type:
                continue
            if requested_name and device["device_name"].lower() != requested_name.lower():
                continue
            if requested_sub_type is not None and device["device_sub_type"] != requested_sub_type:
                continue
            return device
        requested_label = requested_name or str(self.seed_binding.device_type)
        discovered = ", ".join(
            f"{item['device_name']}#{item['device_index']}"
            for item in devices
        ) or "none"
        raise AdapterOperationError(
            f"Tongxing device {requested_label} index {self.seed_binding.device_index} was not found. "
            f"Discovered devices: {discovered}"
        )

    def _enumerate_devices_locked(self) -> List[dict[str, Any]]:
        count = c_int32(0)
        code = self._runtime.ts_api.tsapp_enumerate_hw_devices(byref(count))
        self._check_code(code, "enumerate Tongxing devices")
        devices: List[dict[str, Any]] = []
        for index in range(max(int(count.value), 0)):
            info = self._runtime.ts_struct.TLIBHWInfo()
            code = self._runtime.ts_api.tsapp_get_hw_info_by_index(index, byref(info))
            self._check_code(code, f"read Tongxing device {index}")
            device_name = _decode_c_string(info.FDeviceName)
            subtype = self._resolve_device_sub_type(device_name)
            devices.append(
                {
                    "enumeration_index": index,
                    "device_index": int(info.FDeviceIndex),
                    "device_type": int(info.FDeviceType),
                    "device_type_name": self._enum_name(self._runtime.ts_enum._TLIBBusToolDeviceType, int(info.FDeviceType)),
                    "device_sub_type": subtype if subtype is not None else -1,
                    "device_sub_type_name": self._enum_name(self._runtime.ts_enum._TLIB_TS_Device_Sub_Type, subtype),
                    "vendor_name": _decode_c_string(info.FVendorName),
                    "device_name": device_name,
                    "serial_number": _decode_c_string(info.FSerialString),
                }
            )
        return devices

    def _start_channel_locked(self, physical_channel: int, config: ChannelConfig) -> None:
        self._enable_fifo_locked()
        self._ensure_can_channel_count_locked(physical_channel + 1)
        self._apply_mapping_locked(physical_channel)
        self._configure_channel_locked(physical_channel, config)
        self._clear_receive_buffers_locked(physical_channel)

    def _should_retry_with_project(self, exc: AdapterOperationError) -> bool:
        if self._project_fallback_used or not self._project_path:
            return False
        message = str(exc).lower()
        return "map tsmaster channel" in message or "set can channel count" in message

    def _enable_fifo_locked(self) -> None:
        if self._fifo_enabled:
            return
        self._runtime.ts_api.tsfifo_enable_receive_fifo()
        self._fifo_enabled = True

    def _ensure_can_channel_count_locked(self, required_count: int) -> None:
        current = self._query_can_channel_count_locked()
        if current >= required_count:
            return
        code = self._runtime.ts_api.tsapp_set_can_channel_count(required_count)
        if code not in (0, None):
            current = self._query_can_channel_count_locked()
            if current >= required_count:
                return
            raise self._build_operation_error(code, f"set CAN channel count to {required_count}")

    def _query_can_channel_count_locked(self) -> int:
        count = c_int32(0)
        getter = getattr(self._runtime.ts_api, "tsapp_get_can_channel_count", None)
        if getter is None:
            return 0
        code = getter(byref(count))
        if code not in (0, None):
            return 0
        return max(int(count.value), 0)

    def _apply_mapping_locked(self, physical_channel: int) -> None:
        if self._device_info is None:
            self._device_info = self._find_device_locked()
        app_type = int(self._runtime.ts_enum._TLIBApplicationChannelType.APP_CAN)
        code = self._runtime.ts_api.tsapp_set_mapping_verbose(
            self._application_name.encode("utf-8"),
            app_type,
            physical_channel,
            self._device_info["device_name"].encode("utf-8"),
            self._device_info["device_type"],
            self._device_info["device_sub_type"],
            self._device_info["device_index"],
            physical_channel,
            True,
        )
        self._check_code(code, f"map TSMaster channel {physical_channel}")

    def _configure_channel_locked(self, physical_channel: int, config: ChannelConfig) -> None:
        nominal_kbps = float(config.nominal_baud) / 1000.0
        if config.bus_type == BusType.CANFD:
            controller_type = int(self._runtime.ts_enum._TLIBCANFDControllerType.lfdtISOCAN)
            controller_mode = int(
                self._runtime.ts_enum._TLIBCANFDControllerMode.lfdmACKOff
                if config.listen_only
                else self._runtime.ts_enum._TLIBCANFDControllerMode.lfdmNormal
            )
            code = self._runtime.ts_api.tsapp_configure_baudrate_canfd(
                physical_channel,
                nominal_kbps,
                float(config.data_baud) / 1000.0,
                controller_type,
                controller_mode,
                bool(config.resistance_enabled),
            )
            self._check_code(code, f"configure CANFD channel {physical_channel}")
            return
        code = self._runtime.ts_api.tsapp_configure_baudrate_can(
            physical_channel,
            nominal_kbps,
            bool(config.listen_only),
            bool(config.resistance_enabled),
        )
        self._check_code(code, f"configure CAN channel {physical_channel}")

    def _transmit_event_locked(self, event: FrameEvent) -> Any:
        if event.bus_type == BusType.CANFD:
            frame = self._build_canfd_frame(event)
            return self._runtime.ts_api.tsapp_transmit_canfd_async(byref(frame))
        if event.bus_type == BusType.ETH:
            raise AdapterOperationError("Tongxing adapter does not support raw ETH replay.")
        frame = self._build_can_frame(event)
        return self._runtime.ts_api.tsapp_transmit_can_async(byref(frame))

    def _build_can_frame(self, event: FrameEvent):
        frame = self._runtime.ts_struct.TLIBCAN()
        payload = bytes(event.payload[:8])
        properties = 0x01
        if _is_extended_id(event):
            properties |= 0x04
        if event.flags.get("remote"):
            properties |= 0x02
        frame.FIdxChn = int(event.channel)
        frame.FProperties = properties
        frame.FDLC = min(len(payload), 8)
        frame.FIdentifier = int(_raw_can_id(event))
        frame.FTimeUs = max(int(event.ts_ns // 1000), 0)
        for index, value in enumerate(payload):
            frame.FData[index] = value
        return frame

    def _build_canfd_frame(self, event: FrameEvent):
        frame = self._runtime.ts_struct.TLIBCANFD()
        payload = bytes(event.payload[:64])
        dlc = canfd_payload_length_to_dlc(len(payload))
        properties = 0x01
        if _is_extended_id(event):
            properties |= 0x04
        if event.flags.get("remote"):
            properties |= 0x02
        fd_properties = 0x01
        if event.flags.get("brs"):
            fd_properties |= 0x02
        if event.flags.get("esi"):
            fd_properties |= 0x04
        frame.FIdxChn = int(event.channel)
        frame.FProperties = properties
        frame.FDLC = int(dlc)
        frame.FFDProperties = fd_properties
        frame.FIdentifier = int(_raw_can_id(event))
        frame.FTimeUs = max(int(event.ts_ns // 1000), 0)
        for index, value in enumerate(payload):
            frame.FData[index] = value
        return frame

    def _poll_events_locked(self, limit: int) -> List[FrameEvent]:
        events: List[FrameEvent] = []
        include_tx = any(config.tx_echo for config in self._channel_configs.values())
        for physical_channel in sorted(self._started_channels):
            remaining = min(max(limit - len(events), 0), _MAX_FIFO_READ)
            if remaining <= 0:
                break
            events.extend(self._receive_canfd_frames_locked(physical_channel, remaining, include_tx))
            remaining = min(max(limit - len(events), 0), _MAX_FIFO_READ)
            if remaining <= 0:
                break
            events.extend(self._receive_can_frames_locked(physical_channel, remaining, include_tx))
        return events

    def _receive_can_frames_locked(self, physical_channel: int, requested: int, include_tx: bool) -> List[FrameEvent]:
        if requested <= 0:
            return []
        buffer = (self._runtime.ts_struct.TLIBCAN * requested)()
        size = c_int32(requested)
        code = self._runtime.ts_api.tsfifo_receive_can_msgs(buffer, byref(size), physical_channel, include_tx)
        self._check_code(code, f"read CAN FIFO on channel {physical_channel}")
        return [
            self._convert_can_frame(buffer[index])
            for index in range(max(int(size.value), 0))
        ]

    def _receive_canfd_frames_locked(self, physical_channel: int, requested: int, include_tx: bool) -> List[FrameEvent]:
        if requested <= 0:
            return []
        buffer = (self._runtime.ts_struct.TLIBCANFD * requested)()
        size = c_int32(requested)
        code = self._runtime.ts_api.tsfifo_receive_canfd_msgs(buffer, byref(size), physical_channel, include_tx)
        self._check_code(code, f"read CANFD FIFO on channel {physical_channel}")
        return [
            self._convert_canfd_frame(buffer[index])
            for index in range(max(int(size.value), 0))
        ]

    def _convert_can_frame(self, frame) -> FrameEvent:
        payload_length = min(int(frame.FDLC), 8)
        channel = int(frame.FIdxChn)
        bus_type = self._bus_type_for_channel(channel, BusType.CAN)
        return FrameEvent(
            ts_ns=int(frame.FTimeUs) * 1000,
            bus_type=bus_type,
            channel=channel,
            message_id=int(frame.FIdentifier),
            payload=bytes(frame.FData[:payload_length]),
            dlc=payload_length,
            flags={
                "direction": "Tx" if int(frame.FProperties) & 0x01 else "Rx",
                "extended": bool(int(frame.FProperties) & 0x04),
                "remote": bool(int(frame.FProperties) & 0x02),
            },
        )

    def _convert_canfd_frame(self, frame) -> FrameEvent:
        payload_length = self._runtime.ts_struct.DLC_DATA_BYTE_CNT[int(frame.FDLC)]
        channel = int(frame.FIdxChn)
        return FrameEvent(
            ts_ns=int(frame.FTimeUs) * 1000,
            bus_type=BusType.CANFD,
            channel=channel,
            message_id=int(frame.FIdentifier),
            payload=bytes(frame.FData[:payload_length]),
            dlc=int(frame.FDLC),
            flags={
                "direction": "Tx" if int(frame.FProperties) & 0x01 else "Rx",
                "extended": bool(int(frame.FProperties) & 0x04),
                "remote": bool(int(frame.FProperties) & 0x02),
                "brs": bool(int(frame.FFDProperties) & 0x02),
                "esi": bool(int(frame.FFDProperties) & 0x04),
            },
        )

    def _clear_receive_buffers_locked(self, physical_channel: int) -> None:
        for function_name in ("tsfifo_clear_can_receive_buffers", "tsfifo_clear_canfd_receive_buffers"):
            function = getattr(self._runtime.ts_api, function_name, None)
            if function is None:
                continue
            try:
                function(physical_channel)
            except Exception:
                continue

    def _ensure_channel_started(self, physical_channel: int) -> None:
        if physical_channel in self._started_channels:
            return
        raise ConfigurationError(f"Channel {physical_channel} is not started on Tongxing adapter.")

    def _bus_type_for_channel(self, physical_channel: int, default: BusType) -> BusType:
        config = self._channel_configs.get(physical_channel)
        if config is None:
            return default
        return config.bus_type

    def _resolve_bus_tool_device_type(self, value: Any) -> Optional[int]:
        return _resolve_enum_value(getattr(self._runtime.ts_enum, "_TLIBBusToolDeviceType", None), value)

    def _resolve_device_sub_type(self, value: Any) -> Optional[int]:
        return _resolve_enum_value(getattr(self._runtime.ts_enum, "_TLIB_TS_Device_Sub_Type", None), value)

    def _enum_name(self, enum_type: Any, value: Optional[int]) -> str:
        if enum_type is None or value is None:
            return ""
        for member in enum_type:
            if int(member) == int(value):
                return member.name
        return str(value)

    def _check_code(self, code: Any, action: str) -> None:
        if code in (0, None):
            return
        raise self._build_operation_error(code, action)

    def _build_operation_error(self, code: Any, action: str) -> AdapterOperationError:
        try:
            code_text = str(int(code))
        except (TypeError, ValueError):
            code_text = str(code)
        return AdapterOperationError(f"{action} failed (code {code_text}): {self._runtime.describe_error(code)}")


def _candidate_sdk_paths(sdk_root: str) -> List[Path]:
    candidates: List[Path] = []
    for raw in (sdk_root, _DEFAULT_SDK_ROOT):
        if not raw:
            continue
        path = _normalize_path(raw)
        if path not in candidates:
            candidates.append(path)
    return candidates


def _contains_tsmaster_dll(path: Path) -> bool:
    return path.exists() and (
        (path / "TSMaster.dll").exists()
        or (path / "libTSMaster.dll").exists()
    )


def _registry_sdk_bin_dir() -> Optional[Path]:
    if platform.system() != "Windows":
        return None
    try:
        import winreg

        key_path = r"Software\\TOSUN\\TSMaster"
        value_name = "bin64" if platform.architecture()[0] == "64bit" else "bin"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
        path = Path(value)
        return path.resolve() if path.exists() else None
    except Exception:
        return None


def _normalize_path(value: Union[str, os.PathLike[str]]) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _normalize_optional_path(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(_normalize_path(str(value)))


def _decode_c_string(value: Any) -> str:
    raw = bytes(value)
    return raw.split(b"\\x00", 1)[0].decode("utf-8", "ignore")


def _resolve_enum_value(enum_type: Any, value: Any) -> Optional[int]:
    if value in (None, "") or enum_type is None:
        return None
    if isinstance(value, int):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        pass
    if hasattr(enum_type, text):
        return int(getattr(enum_type, text))
    for member in enum_type:
        if member.name.lower() == text.lower():
            return int(member)
    return None


def _is_extended_id(event: FrameEvent) -> bool:
    return bool(event.flags.get("extended")) or event.bus_type == BusType.J1939 or int(event.message_id) > 0x7FF


def _raw_can_id(event: FrameEvent) -> int:
    return int(event.message_id) & 0x1FFFFFFF
