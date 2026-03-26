# 诊断能力说明

本文说明当前 CAN UDS、DoIP、DTC 与 ZLG 原始 UDS 导出能力的边界。

## 1. 当前覆盖能力

当前诊断侧重点包括：

- CAN UDS
- DoIP
- DTC 读取 / 清除 / 解析

V1 的 ETH 重点是 DoIP 诊断链路，不是通用原始以太网帧回放。

## 2. CAN UDS

当前 CAN UDS 通过应用层 ISO-TP 实现，适合以下场景：

- 读 DID
- 读 DTC
- 清 DTC

这样做的主要原因是：

- 不依赖尚未完全完成真机参数校验的底层 UDS 函数签名
- 更容易调试与跨环境做结构验证

相关代码位于 `src/replay_platform/diagnostics/can_uds.py`。

## 3. ZLG 原始 UDS 导出

仓库内的 `zlgcan.py` 已补充以下 DLL 导出入口：

- `UDS_Request`
- `UDS_RequestEX`
- `UDS_Control`
- `UDS_ControlEX`

当前边界：

- 这部分已经打通 DLL 导出入口
- 高级参数结构与性能路径仍建议在真实 Windows + ZLG 硬件环境下继续校验

## 4. DoIP

当前 DoIP 能力包括：

- TCP 连接
- Routing Activation
- Alive Check
- Diagnostic Message
- UDS 正负响应解析

相关代码位于 `src/replay_platform/diagnostics/doip.py`。

## 5. DTC 解析

当前 DTC 实现基于 UDS：

- `0x19 ReadDTCInformation`
- `0x14 ClearDiagnosticInformation`

已支持：

- 解析 DTC 编码
- 解析状态位
- 结合 JSON / CSV 字典补充描述

常见状态位解释包括：

- `test_failed`
- `pending_dtc`
- `confirmed_dtc`
- `warning_indicator_requested`

相关代码位于 `src/replay_platform/diagnostics/dtc.py`。

## 6. 诊断相关实现提醒

改动诊断功能时，建议同步确认：

- 场景中的 `diagnostic_targets` 与 `diagnostic_actions` 字段是否仍兼容
- CAN 与 DoIP 的超时、目标地址和路由参数是否保持清晰边界
- 是否错误把 DoIP 能力描述成“原始以太网回放”
- 是否在非 Windows 环境误表述为“已完成 ZLG 硬件验证”
