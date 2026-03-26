# 测试与验证

本文集中说明常用命令、测试文件映射和不同类型改动的最低验证要求。

## 1. 常用命令

安装开发依赖：

```bash
python -m pip install -e .[dev]
```

启动程序：

```bash
python -m replay_platform
```

运行单元测试：

```bash
PYTHONPYCACHEPREFIX=/tmp/replay-pyc python3 -m unittest discover -s tests -v
```

做语法编译检查：

```bash
PYTHONPYCACHEPREFIX=/tmp/replay-pyc python3 -m compileall src tests
```

## 2. 测试文件映射

- `tests/test_engine.py`
  回放引擎启停、暂停、链路动作
- `tests/test_app_controller.py`
  应用编排、场景加载与 UI 协调相关逻辑
- `tests/test_library.py`
  trace 导入与场景保存
- `tests/test_trace_loader.py`
  ASC 导入
- `tests/test_signal_catalog.py`
  信号覆盖
- `tests/test_dtc.py`
  DTC 解析
- `tests/test_ui_helpers.py`
  场景编辑表单的解析辅助函数

## 3. 最低验证要求

### 3.1 纯文档改动

- 检查路径、模块名、命令与仓库实际一致

### 3.2 纯 UI 改动

- 运行 `python -m compileall src tests`
- 如果改动涉及表单解析或场景编辑逻辑，补或更新 `tests/test_ui_helpers.py`

### 3.3 运行时 / 解析 / 场景结构改动

- 运行全部 `unittest`
- 补对应模块测试
- 在最终说明中明确是否未做 Qt 手工点击验证 / Windows 硬件验证

## 4. 验证边界

- 非 Windows 环境允许做结构开发、单元测试和语法检查，但不能声称已完成 ZLG 真机联调
- 涉及 ZLG / DoIP / 回放时序的改动，除了自动化测试外，通常还需要明确说明未覆盖的手工或硬件验证范围
