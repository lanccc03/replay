from __future__ import annotations

import importlib.util
import platform
from ctypes import addressof, c_char_p, c_void_p, cast, memset, sizeof
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Sequence

from replay_platform.adapters.base import DeviceAdapter
from replay_platform.core import (
    AdapterCapabilities,
    AdapterHealth,
    BusType,
    ChannelConfig,
    ChannelDescriptor,
    DeviceChannelBinding,
    DeviceDescriptor,
    FrameEvent,
    canfd_payload_length_to_dlc,
)
from replay_platform.errors import AdapterOperationError, ConfigurationError


class ZlgDeviceAdapter(DeviceAdapter):
    def __init__(self, adapter_id: str, seed_binding: DeviceChannelBinding) -> None:
        super().__init__(adapter_id)
        self.seed_binding = seed_binding
        self._sdk_module = None
        self._zcan = None
        self._device_handle = None
        self._descriptor: Optional[DeviceDescriptor] = None
        self._channel_handles: Dict[int, Any] = {}
        self._channel_configs: Dict[int, ChannelConfig] = {}

    def open(self) -> DeviceDescriptor:
        if self._descriptor is not None:
            return self._descriptor
        if platform.system() != "Windows":
            raise AdapterOperationError("ZLG 适配器仅支持 Windows。")
        self._sdk_module = self._load_sdk_module(Path(self.seed_binding.sdk_root))
        self._zcan = self._sdk_module.ZCAN()
        device_type = self._resolve_device_type(self.seed_binding.device_type)
        self._device_handle = self._zcan.OpenDevice(device_type, self.seed_binding.device_index, 0)
        if self._device_handle in (None, self._sdk_module.INVALID_DEVICE_HANDLE):
            raise AdapterOperationError("打开 ZLG 设备失败。")
        self._apply_network_settings(self.seed_binding)
        info = self._zcan.GetDeviceInf(self._device_handle)
        self._descriptor = DeviceDescriptor(
            adapter_id=self.adapter_id,
            driver="zlg",
            name=getattr(info, "hw_type", self.seed_binding.device_type) if info else self.seed_binding.device_type,
            serial_number=getattr(info, "serial", "") if info else "",
            channel_count=int(getattr(info, "can_num", 0) or 0),
            metadata={
                "device_type": self.seed_binding.device_type,
                "device_index": self.seed_binding.device_index,
                "sdk_root": self.seed_binding.sdk_root,
            },
        )
        return self._descriptor

    def close(self) -> None:
        if self._zcan is None or self._device_handle is None:
            return
        for physical_channel in list(self._channel_handles):
            try:
                self.stop_channel(physical_channel)
            except Exception:
                pass
        self._zcan.CloseDevice(self._device_handle)
        self._device_handle = None
        self._descriptor = None

    def enumerate_channels(self) -> Sequence[ChannelDescriptor]:
        descriptor = self.open()
        bus_type = self.seed_binding.bus_type
        count = max(descriptor.channel_count, self.seed_binding.physical_channel + 1)
        return [
            ChannelDescriptor(
                logical_channel=index,
                physical_channel=index,
                bus_type=bus_type,
                label=f"ZLG CH{index}",
            )
            for index in range(count)
        ]

    def start_channel(self, physical_channel: int, config: ChannelConfig) -> None:
        self.open()
        assert self._zcan is not None
        assert self._device_handle is not None
        self._configure_channel_values(physical_channel, config)
        init_cfg = self._sdk_module.ZCAN_CHANNEL_INIT_CONFIG()
        memset(addressof(init_cfg), 0, sizeof(init_cfg))
        if config.bus_type == BusType.CANFD:
            init_cfg.can_type = self._sdk_module.ZCAN_TYPE_CANFD
            init_cfg.config.canfd.mode = 1 if config.listen_only else 0
        else:
            init_cfg.can_type = self._sdk_module.ZCAN_TYPE_CAN
            init_cfg.config.can.mode = 1 if config.listen_only else 0
        handle = self._zcan.InitCAN(self._device_handle, physical_channel, init_cfg)
        if handle in (None, self._sdk_module.INVALID_CHANNEL_HANDLE):
            raise AdapterOperationError(f"通道 {physical_channel} InitCAN 失败。")
        result = self._zcan.StartCAN(handle)
        if result != self._sdk_module.ZCAN_STATUS_OK:
            raise AdapterOperationError(f"通道 {physical_channel} StartCAN 失败。")
        self._channel_handles[physical_channel] = handle
        self._channel_configs[physical_channel] = config

    def stop_channel(self, physical_channel: int) -> None:
        handle = self._channel_handles.pop(physical_channel, None)
        self._channel_configs.pop(physical_channel, None)
        if handle is not None and self._zcan is not None:
            self._zcan.ResetCAN(handle)

    def send(self, batch: Sequence[FrameEvent]) -> int:
        if not batch:
            return 0
        sent_total = 0
        for physical_channel, channel_events in _group_by_channel(batch).items():
            handle = self._channel_handles.get(physical_channel)
            if handle is None:
                raise AdapterOperationError(f"物理通道 {physical_channel} 尚未启动。")
            classic = [item for item in channel_events if item.bus_type in (BusType.CAN, BusType.J1939)]
            fd = [item for item in channel_events if item.bus_type == BusType.CANFD]
            if classic:
                sent_total += self._send_classic(handle, classic)
            if fd:
                sent_total += self._send_fd(handle, fd)
        return sent_total

    def send_scheduled(self, batch: Sequence[FrameEvent], enqueue_base_ns: int) -> int:
        if not batch or any(item.bus_type == BusType.CANFD for item in batch):
            return self.send(batch)
        now_ns = time.perf_counter_ns()
        scheduled_batch = []
        for item in batch:
            delay_us = max((enqueue_base_ns + item.ts_ns - now_ns) // 1_000, 0)
            flags = dict(item.flags)
            flags["queue_delay_us"] = int(delay_us)
            scheduled_batch.append(item.clone(flags=flags))
        return self.send(scheduled_batch)

    def read(self, limit: int = 256, timeout_ms: int = 0) -> List[FrameEvent]:
        self.open()
        assert self._zcan is not None
        assert self._device_handle is not None
        events: List[FrameEvent] = []
        if self.seed_binding.merge_receive:
            merge_count = int(self._zcan.GetReceiveNum(self._device_handle, self._sdk_module.ZCAN_TYPE_MERGE) or 0)
            if merge_count:
                messages, actual = self._zcan.ReceiveData(self._device_handle, min(merge_count, limit), timeout_ms)
                events.extend(self._convert_merge(messages[:actual]))
                return sorted(events, key=lambda item: item.ts_ns)
        for physical_channel, handle in list(self._channel_handles.items()):
            remaining = max(limit - len(events), 0)
            if remaining <= 0:
                break
            can_count = int(self._zcan.GetReceiveNum(handle, self._sdk_module.ZCAN_TYPE_CAN) or 0)
            if can_count:
                messages, actual = self._zcan.Receive(handle, min(can_count, remaining), timeout_ms)
                events.extend(self._convert_can(physical_channel, messages[:actual]))
            remaining = max(limit - len(events), 0)
            if remaining <= 0:
                break
            fd_count = int(self._zcan.GetReceiveNum(handle, self._sdk_module.ZCAN_TYPE_CANFD) or 0)
            if fd_count:
                messages, actual = self._zcan.ReceiveFD(handle, min(fd_count, remaining), timeout_ms)
                events.extend(self._convert_fd(physical_channel, messages[:actual]))
        return sorted(events, key=lambda item: item.ts_ns)

    def health(self) -> AdapterHealth:
        if self._zcan is None or self._device_handle is None:
            return AdapterHealth(online=False, detail="已关闭")
        online = bool(self._zcan.DeviceOnLine(self._device_handle))
        per_channel = {physical: online for physical in self._channel_handles}
        return AdapterHealth(online=online, detail="设备在线" if online else "设备离线", per_channel=per_channel)

    def reconnect(self, physical_channel: Optional[int] = None) -> None:
        self.open()
        if physical_channel is None:
            self.close()
            self.open()
            for channel, config in list(self._channel_configs.items()):
                self.start_channel(channel, config)
            return
        config = self._channel_configs.get(physical_channel)
        if config is None:
            raise ConfigurationError(f"通道 {physical_channel} 没有可用配置。")
        self.stop_channel(physical_channel)
        self.start_channel(physical_channel, config)

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            can=True,
            canfd=True,
            j1939=True,
            merge_receive=True,
            queue_send=True,
            tx_timestamp=True,
            bus_usage=True,
            can_uds=True,
        )

    def raw_uds_request(self, *args: Any) -> Any:
        self.open()
        return self._zcan.UDS_Request(*args)

    def raw_uds_request_ex(self, *args: Any) -> Any:
        self.open()
        return self._zcan.UDS_RequestEX(*args)

    def raw_uds_control(self, *args: Any) -> Any:
        self.open()
        return self._zcan.UDS_Control(*args)

    def raw_uds_control_ex(self, *args: Any) -> Any:
        self.open()
        return self._zcan.UDS_ControlEX(*args)

    def _load_sdk_module(self, sdk_root: Path):
        zlgcan_file = sdk_root / "zlgcan.py"
        if not zlgcan_file.exists():
            raise ConfigurationError(f"未找到 ZLG SDK：{zlgcan_file}")
        spec = importlib.util.spec_from_file_location("replay_zlg_sdk", zlgcan_file)
        if spec is None or spec.loader is None:
            raise ConfigurationError("无法为 ZLG SDK 创建导入规格。")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _resolve_device_type(self, device_type: str) -> Any:
        assert self._sdk_module is not None
        if isinstance(device_type, int):
            return device_type
        if hasattr(self._sdk_module, device_type):
            return getattr(self._sdk_module, device_type)
        prefixed = device_type if device_type.startswith("ZCAN_") else f"ZCAN_{device_type}"
        if hasattr(self._sdk_module, prefixed):
            return getattr(self._sdk_module, prefixed)
        raise ConfigurationError(f"未知的 ZLG 设备类型：{device_type}")

    def _apply_network_settings(self, binding: DeviceChannelBinding) -> None:
        network = binding.network
        if not network:
            return
        if "work_mode" in network:
            self._set_string_value("0/work_mode", network["work_mode"])
        if "ip" in network:
            self._set_string_value("0/ip", network["ip"])
        if "work_port" in network:
            self._set_string_value("0/work_port", network["work_port"])
        if "local_port" in network:
            self._set_string_value("0/local_port", network["local_port"])

    def _configure_channel_values(self, physical_channel: int, config: ChannelConfig) -> None:
        path_prefix = f"{physical_channel}"
        if config.bus_type == BusType.CANFD:
            self._set_string_value(f"{path_prefix}/canfd_abit_baud_rate", config.nominal_baud)
            self._set_string_value(f"{path_prefix}/canfd_dbit_baud_rate", config.data_baud)
        else:
            self._set_string_value(f"{path_prefix}/baud_rate", config.nominal_baud)
        self._set_string_value(f"{path_prefix}/initenal_resistance", 1 if config.resistance_enabled else 0, strict=False)
        self._set_string_value(f"{path_prefix}/set_device_tx_echo", 1 if config.tx_echo else 0, strict=False)
        self._set_string_value("0/set_device_recv_merge", 1 if self.seed_binding.merge_receive else 0, strict=False)

    def _set_string_value(self, path: str, value: Any, strict: bool = True) -> None:
        assert self._zcan is not None
        assert self._device_handle is not None
        payload = str(value).encode("utf-8")
        result = self._zcan.ZCAN_SetValue(self._device_handle, path, payload)
        if strict and result != self._sdk_module.ZCAN_STATUS_OK:
            raise AdapterOperationError(f"ZCAN_SetValue 设置失败：{path} -> {value}")

    def _send_classic(self, handle: Any, events: Sequence[FrameEvent]) -> int:
        messages = (self._sdk_module.ZCAN_Transmit_Data * len(events))()
        memset(addressof(messages), 0, sizeof(messages))
        for index, event in enumerate(events):
            messages[index].transmit_type = 0
            messages[index].frame.can_id = self._raw_can_id(event)
            payload = event.payload[:8]
            messages[index].frame.can_dlc = min(event.dlc, len(payload), 8)
            if event.flags.get("tx_echo"):
                messages[index].frame._pad |= 0x20
            delay_us = event.flags.get("queue_delay_us")
            if delay_us:
                messages[index].frame._pad |= 1 << 7
                if delay_us % 100:
                    messages[index].frame._pad |= 1 << 6
                delay_ticks = int(delay_us / (100 if delay_us % 100 else 1000))
                messages[index].frame._res0 = delay_ticks & 0xFF
                messages[index].frame._res1 = (delay_ticks >> 8) & 0xFF
            for payload_index, value in enumerate(payload[:8]):
                messages[index].frame.data[payload_index] = value
        return int(self._zcan.Transmit(handle, messages, len(events)) or 0)

    def _send_fd(self, handle: Any, events: Sequence[FrameEvent]) -> int:
        messages = (self._sdk_module.ZCAN_TransmitFD_Data * len(events))()
        memset(addressof(messages), 0, sizeof(messages))
        for index, event in enumerate(events):
            messages[index].transmit_type = 0
            messages[index].frame.can_id = self._raw_can_id(event)
            payload = event.payload[:64]
            # ZLG 的 CANFD len 字段使用 DLC 码值，不是原始字节数。
            messages[index].frame.len = canfd_payload_length_to_dlc(len(payload))
            if event.flags.get("tx_echo"):
                messages[index].frame.flags |= 0x20
            if event.flags.get("brs"):
                messages[index].frame.flags |= 0x01
            for payload_index, value in enumerate(payload[:64]):
                messages[index].frame.data[payload_index] = value
        return int(self._zcan.TransmitFD(handle, messages, len(events)) or 0)

    def _convert_can(self, physical_channel: int, messages: Sequence[Any]) -> List[FrameEvent]:
        items: List[FrameEvent] = []
        for message in messages:
            payload = bytes(message.frame.data[: message.frame.can_dlc])
            items.append(
                FrameEvent(
                    ts_ns=int(message.timestamp) * 1000,
                    bus_type=BusType.CAN,
                    channel=physical_channel,
                    message_id=int(message.frame.can_id),
                    payload=payload,
                    dlc=int(message.frame.can_dlc),
                    flags={"direction": "Tx" if message.frame._pad & 0x20 else "Rx"},
                )
            )
        return items

    def _convert_fd(self, physical_channel: int, messages: Sequence[Any]) -> List[FrameEvent]:
        items: List[FrameEvent] = []
        for message in messages:
            payload = bytes(message.frame.data[: message.frame.len])
            items.append(
                FrameEvent(
                    ts_ns=int(message.timestamp) * 1000,
                    bus_type=BusType.CANFD,
                    channel=physical_channel,
                    message_id=int(message.frame.can_id),
                    payload=payload,
                    dlc=int(message.frame.len),
                    flags={
                        "direction": "Tx" if message.frame.flags & 0x20 else "Rx",
                        "brs": bool(message.frame.flags & 0x01),
                    },
                )
            )
        return items

    def _convert_merge(self, messages: Sequence[Any]) -> List[FrameEvent]:
        items: List[FrameEvent] = []
        for message in messages:
            if message.dataType != self._sdk_module.ZCAN_DT_ZCAN_CAN_CANFD_DATA:
                continue
            frame = message.data.zcanfddata.frame
            payload = bytes(frame.data[: frame.len])
            items.append(
                FrameEvent(
                    ts_ns=int(message.data.zcanfddata.timestamp) * 1000,
                    bus_type=BusType.CANFD if message.data.zcanfddata.flag.frameType else BusType.CAN,
                    channel=int(message.chnl),
                    message_id=int(frame.can_id),
                    payload=payload,
                    dlc=int(frame.len),
                    flags={
                        "direction": "Tx" if message.data.zcanfddata.flag.txEchoed else "Rx",
                        "brs": bool(frame.flags & 0x01),
                    },
                )
            )
        return items

    @staticmethod
    def _raw_can_id(event: FrameEvent) -> int:
        message_id = event.message_id
        if event.bus_type == BusType.J1939 and not (message_id & (1 << 31)):
            message_id |= 1 << 31
        return message_id


def _group_by_channel(batch: Sequence[FrameEvent]) -> Dict[int, List[FrameEvent]]:
    grouped: Dict[int, List[FrameEvent]] = {}
    for event in batch:
        grouped.setdefault(event.channel, []).append(event)
    return grouped
