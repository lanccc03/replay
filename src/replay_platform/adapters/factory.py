from __future__ import annotations

from typing import Dict, List, Type

from replay_platform.adapters.base import DiagnosticClient, DeviceAdapter
from replay_platform.adapters.mock import MockDeviceAdapter
from replay_platform.adapters.tongxing import TongxingDeviceAdapter
from replay_platform.adapters.zlg import ZlgDeviceAdapter
from replay_platform.core import DeviceChannelBinding, DiagnosticTransport, ScenarioSpec
from replay_platform.diagnostics.can_uds import CanUdsClient, IsoTpConfig
from replay_platform.diagnostics.doip import DoipDiagnosticClient, DoipLinkAdapter


def build_adapters(
    scenario: ScenarioSpec,
    *,
    zlg_adapter_cls: Type[ZlgDeviceAdapter] = ZlgDeviceAdapter,
    tongxing_adapter_cls: Type[TongxingDeviceAdapter] = TongxingDeviceAdapter,
    mock_adapter_cls: Type[MockDeviceAdapter] = MockDeviceAdapter,
) -> Dict[str, DeviceAdapter]:
    adapters: Dict[str, DeviceAdapter] = {}
    bindings_by_adapter: Dict[str, List[DeviceChannelBinding]] = {}
    for binding in scenario.bindings:
        bindings_by_adapter.setdefault(binding.adapter_id, []).append(binding)
    for adapter_id, binding_group in bindings_by_adapter.items():
        binding = binding_group[0]
        driver = binding.driver.lower()
        if driver == "zlg":
            adapters[adapter_id] = zlg_adapter_cls(adapter_id, binding)
        elif driver == "tongxing":
            seed_binding = max(binding_group, key=lambda item: int(item.physical_channel))
            adapters[adapter_id] = tongxing_adapter_cls(adapter_id, seed_binding)
        elif driver == "mock":
            adapters[adapter_id] = mock_adapter_cls(adapter_id)
        else:
            raise ValueError(f"不支持的驱动类型：{binding.driver}")
    return adapters


def build_diagnostics(
    scenario: ScenarioSpec,
    adapters: Dict[str, DeviceAdapter],
) -> Dict[str, DiagnosticClient]:
    diagnostics: Dict[str, DiagnosticClient] = {}
    for target in scenario.diagnostic_targets:
        if target.transport == DiagnosticTransport.DOIP:
            diagnostics[target.name] = DoipDiagnosticClient(
                DoipLinkAdapter(
                    host=target.host,
                    port=target.port,
                    source_address=target.source_address,
                    target_address=target.target_address,
                    activation_type=target.activation_type,
                    timeout_ms=target.timeout_ms,
                )
            )
            continue
        binding = scenario.find_binding(target.logical_channel)
        if binding is None:
            raise ValueError(f"诊断逻辑通道 {target.logical_channel} 未绑定设备。")
        adapter = adapters[binding.adapter_id]
        diagnostics[target.name] = CanUdsClient(
            adapter,
            IsoTpConfig(
                channel=binding.physical_channel,
                tx_id=target.tx_id,
                rx_id=target.rx_id,
                bus_type=binding.bus_type,
                timeout_ms=target.timeout_ms,
            ),
        )
    return diagnostics


__all__ = ("build_adapters", "build_diagnostics")
