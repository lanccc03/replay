# Windows 与 ZLG 硬件说明

本文说明真实硬件接入前的环境要求、ZLG 预检查步骤、已知限制与建议联调顺序。

## 1. 环境要求

### 1.1 操作系统

- Windows 10 x64 / Windows 11 x64

当前项目的 ZLG SDK 加载方式是 Windows-only。

### 1.2 Python

- 建议：Python 3.12
- 当前 `pyproject.toml` 最低要求：Python 3.9

### 1.3 依赖

核心依赖包括：

- `PySide6`
- `python-can`
- `cantools`

开发依赖：

- `pytest` 或直接使用内置 `unittest`

## 2. ZLG 使用前准备

在接入真实硬件前，建议先用 ZLG 官方工具完成基础验证：

- `ZCANPRO`
- `ZXDOC`

网络类设备还可能需要先使用网络配置工具配置 IP、端口与工作模式。

建议流程：

1. 先在官方工具上确认设备工作正常
2. 确认设备型号与 `OpenDevice` 的类型一致
3. 确认波特率、采样点、终端电阻设置正确
4. CANFDNET / CANET 设备先确认 IP、目标端口、本地端口、客户端 / 服务器模式
5. 在官方工具中确认可以正常收发后，再用本项目接入

## 3. 场景模板与常见配置点

示例模板：

- `examples/zlg_canfdnet_doip_scenario.json`

真机接入前通常需要确认这些字段：

- `device_type`
- `device_index`
- `nominal_baud`
- `data_baud`
- `resistance_enabled`
- `listen_only`
- `tx_echo`
- `merge_receive`
- `network.ip`
- `network.work_port`
- `network.local_port`
- `diagnostic_targets.host`

## 4. 当前已知限制

- 当前开发机如果不是 Windows，只能做结构开发、单元测试和语法检查，不能声称已完成硬件联调
- ZLG 原始 UDS 导出已暴露，但高级调用参数仍建议在真实硬件环境下进一步校验
- 暂未实现原始 ETH 报文回放
- 暂未实现同星设备具体接入
- `BLF` 与 `DBC` 能力分别依赖 `python-can` 和 `cantools`

## 5. 推荐联调顺序

建议按以下顺序推进：

1. 在 Windows 上安装依赖并启动 UI
2. 用 Mock 场景验证界面与回放控制
3. 导入 `ASC` 文件验证 trace 管理
4. 接入 ZLG USB 设备验证 CAN / CANFD 单通道收发
5. 接入 CANFDNET 验证网络类设备
6. 绑定 DBC 验证信号改值
7. 验证 CAN UDS
8. 验证 DoIP
9. 验证 DTC 读取 / 清除
10. 验证断连恢复
