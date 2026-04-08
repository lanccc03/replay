# 测试与验证

本文集中说明验证命令、测试文件映射和不同类型改动的最低验证要求。`agents.md` 只保留执行清单，这里保留可直接引用的命令与覆盖范围。

## 1. 常用命令

安装开发依赖：

```powershell
python -m pip install -e .[dev]
```

启动程序：

```powershell
python -m replay_platform
```

运行全部 `unittest`：

```powershell
python -m unittest discover -s tests -v
```

做语法编译检查：

```powershell
python -m compileall src tests
```

如果希望避免把 `__pycache__` 写回工作区，PowerShell 可先设置：

```powershell
$env:PYTHONPYCACHEPREFIX = (Join-Path $PWD ".pycache_tmp")
```

## 2. 测试文件映射

- `tests/test_engine.py`
  回放引擎时间轴、暂停恢复、链路动作、循环完成态
- `tests/test_app_controller.py`
  应用编排、场景加载、启动来源回退、日志策略
- `tests/test_library.py`
  trace 导入、缓存与场景持久化
- `tests/test_trace_loader.py`
  ASC / BLF 导入解析
- `tests/test_signal_catalog.py`
  DBC / J1939 DBC 信号覆盖与编解码
- `tests/test_frame_enable.py`
  报文启停规则
- `tests/test_dtc.py`
  DTC 解析
- `tests/test_ui_helpers.py`
  场景编辑表单解析与草稿归一化
- `tests/test_ui_dialog.py`
  Qt 场景编辑器对话框回归
- `tests/test_zlg_adapter.py`
  ZLG 适配器封装与 CAN FD 发送行为
- `tests/test_tongxing_adapter.py`
  同星 TSMaster 适配器封装、项目回退、收发与连接状态行为

## 3. 最低验证要求

### 3.1 纯文档改动

- 检查路径、模块名、命令与仓库实际一致

### 3.2 纯 UI 改动

- 运行 `python -m compileall src tests`
- 如果改动涉及表单解析或场景编辑逻辑，补或更新 `tests/test_ui_helpers.py`
- 如果改动影响场景编辑器交互或对话框回归，优先同步检查 `tests/test_ui_dialog.py`

### 3.3 运行时 / 解析 / 场景结构改动

- 运行全部 `unittest`
- 补对应模块测试
- 在最终说明中明确是否未做 Qt 手工点击验证 / Windows 硬件验证

### 3.4 设备适配 / 诊断相关改动

- 运行全部 `unittest`
- 涉及 ZLG / 同星适配器封装时，至少同步检查对应的适配器测试
- 涉及 DoIP、CAN UDS 或回放时序时，除自动化测试外，还要说明未覆盖的真机验证范围

## 4. 验证边界

- 非 Windows 环境允许做结构开发、单元测试和语法检查，但不能声称已完成 ZLG / 同星 真机联调
- `tests/test_zlg_adapter.py` 与 `tests/test_tongxing_adapter.py` 只覆盖封装和模拟路径，不等于真机联调
- Qt 自动化测试不等于完整人工点击验证
- 涉及 ZLG / 同星 / DoIP / 回放时序的改动，除了自动化测试外，通常还需要明确说明未覆盖的手工或硬件验证范围
