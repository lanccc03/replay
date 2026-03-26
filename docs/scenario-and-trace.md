# 场景与 Trace

本文说明场景 JSON 的主要字段、trace 文件导入策略、信号在线修改，以及运行期数据目录。

## 1. 场景文件约定

场景文件采用 JSON 表达，且必须继续兼容 `ScenarioSpec.from_dict()` / `to_dict()`。

常见字段如下：

### 1.1 `trace_file_ids`

表示已导入文件库中的 trace 记录 ID。

### 1.2 `bindings`

逻辑通道到物理设备通道的映射，典型字段包括：

- `adapter_id`
- `driver`
- `logical_channel`
- `physical_channel`
- `bus_type`
- `device_type`
- `device_index`
- `sdk_root`
- `nominal_baud`
- `data_baud`
- `resistance_enabled`
- `listen_only`
- `tx_echo`
- `merge_receive`
- `network`

### 1.3 `database_bindings`

数据库绑定列表，典型字段包括：

- `logical_channel`
- `path`
- `format`

### 1.4 `signal_overrides`

初始信号覆盖列表，典型字段包括：

- `logical_channel`
- `message_id_or_pgn`
- `signal_name`
- `value`

### 1.5 `diagnostic_targets`

诊断目标配置，支持两类传输：

- `CAN`
- `DOIP`

CAN 常见字段：

- `adapter_id`
- `logical_channel`
- `tx_id`
- `rx_id`
- `timeout_ms`

DoIP 常见字段：

- `host`
- `port`
- `source_address`
- `target_address`
- `activation_type`
- `timeout_ms`

### 1.6 `diagnostic_actions`

统一时间轴上的诊断动作，常见字段包括：

- `ts_ns`
- `target`
- `service_id`
- `payload`
- `transport`
- `timeout_ms`
- `description`

### 1.7 `link_actions`

统一时间轴上的链路动作，常见字段包括：

- `ts_ns`
- `adapter_id`
- `action`
- `logical_channel`
- `description`

## 2. Trace 导入与缓存策略

当前已支持：

- `ASC`
- `BLF`（依赖 `python-can`）

当前策略：

- 导入时统一转换为内部 `FrameEvent`
- 同时写入本地缓存 JSON，提高后续加载速度
- SQLite 仅保存元数据，不直接保存大体量原始报文

相关代码入口：

- `src/replay_platform/services/library.py`
- `src/replay_platform/services/trace_loader.py`
- `src/replay_platform/runtime/recorder.py`

## 3. 信号在线修改

在线修改依赖 DBC / J1939 DBC，基本流程为：

1. 为逻辑通道绑定数据库
2. 配置 `SignalOverride`
3. 回放发送前先解码报文
4. 修改指定信号值
5. 重新编码并下发

当前能力：

- 可通过场景 JSON 配置初始覆盖
- 可通过 UI 手工追加覆盖项

重要边界：

- 未绑定数据库时，只支持原始帧回放，不支持信号级编辑
- 变更相关逻辑时，需要同步检查场景保存、UI 表单解析与运行时发送路径

## 4. 回放时序要点

回放引擎使用统一逻辑时间轴：

- 每个事件都带 `ts_ns`
- 启动时以 `perf_counter_ns()` 建立时间基准
- 暂停时冻结逻辑时间
- 恢复时重新绑定基准时间

当前策略：

- 帧事件、诊断动作、链路动作在同一时间体系下执行
- 断连恢复遵循“不补发过期帧、不做追赶”的策略
- 涉及时序改动时，要重点检查 `pause / resume` 和链路恢复后的过期帧处理

## 5. 文件库与运行数据

程序运行后会在工作目录下生成：

```text
.replay_platform/
├── cache/          标准化缓存
├── databases/      预留数据库目录
├── traces/         导入后的 trace 文件副本
└── library.sqlite3 元数据库
```

作用说明：

- `traces/`：保存导入后的原始文件副本
- `cache/`：保存标准化帧缓存
- `library.sqlite3`：保存 trace 与 scenario 元信息
