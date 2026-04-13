# 文本编码排查与预防

本文记录仓库中中文文本出现乱码时的排查顺序与预防约定，重点适用于 Windows + PowerShell + UTF-8 源码的组合场景。

## 1. 先记住的结论

- 先确认文件真实字节，再判断源码是否损坏。
- 看到乱码，不要立刻假设“文件已经坏了”；很多时候只是终端或编辑器按错编码显示。
- Windows 下检查中文源码时，优先使用显式 UTF-8 的读取方式，不要只凭 `Get-Content` 的显示结果下结论。

## 2. 常见症状

- PowerShell 中 `Get-Content` 看起来是一整片乱码，但编辑器里部分中文又是正常的。
- 同一个文件里，前半段中文正常、后半段只有少量字符串异常。
- 日志、异常消息、文档标题里出现类似 “鍥炴斁”“閫傞厤鍣?” 这样的文本。

这类情况通常有两种来源：

- 显示乱码：文件本身仍是 UTF-8，终端或编辑器按错误编码解释。
- 源码乱码：文件里真的混入了错误字符，往往是局部污染，不一定整文件都坏。

## 3. 推荐排查步骤

### 3.1 先用 Python 按 UTF-8 直接读取

```powershell
@'
from pathlib import Path
path = Path("src/replay_platform/runtime/engine.py")
for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    if "回放" in line or "适配器" in line:
        print(i, line)
'@ | python -X utf8 -
```

如果这里读出来是正常中文，而终端普通查看仍是乱码，问题大概率在显示链路，不在源码本体。

### 3.2 必要时打印 `unicode_escape`

```powershell
@'
from pathlib import Path
path = Path("src/replay_platform/runtime/engine.py")
for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    if any(ord(ch) > 127 for ch in line):
        print(i, line.encode("unicode_escape").decode())
'@ | python -X utf8 -
```

这个视图更适合区分：

- 正常中文：会显示为 `\u56de\u653e` 这类标准 Unicode 转义
- 异常乱码：常见为杂乱 Unicode，甚至混入私有区字符

### 3.3 扫描异常字符，不要只看肉眼观感

```powershell
@'
from pathlib import Path
path = Path("src/replay_platform/runtime/engine.py")
found = False
for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    if any(0xE000 <= ord(ch) <= 0xF8FF for ch in line):
        found = True
        print(i, line.encode("unicode_escape").decode())
if not found:
    print("no-private-use-chars")
'@ | python -X utf8 -
```

私有区字符不一定百分之百代表乱码，但在本仓库的中文源码里，出现它们通常值得优先检查。

### 3.4 只修真正坏掉的局部文本

- 如果 Python 直读结果正常，不要整文件重写编码。
- 如果只有个别异常消息、日志文案或注释损坏，只修这些局部字符串。
- 修完后再跑一次 UTF-8 直读与异常字符扫描，确认没有残留。

## 4. Windows / PowerShell 使用建议

- 优先使用 `python -X utf8` 做文本检查。
- 如果必须用 PowerShell 直接输出文本，先设置输出编码：

```powershell
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
```

- 不要把裸 `Get-Content` 的输出当成唯一事实来源。

## 5. 提交前的预防约定

- 源码与文档统一保存为 UTF-8。
- Python 读写文本时显式写 `encoding="utf-8"`。
- 改了中文文案后，至少做一次 UTF-8 直读自检。
- 如果怀疑是局部污染，重点检查：
  - 异常消息
  - 日志文案
  - 最近复制粘贴过的中文段落
  - 从外部文档迁移进来的内容

## 6. 本仓库的经验总结

一次典型案例是：`engine.py` 看起来“很多乱码”，但实际情况分成两层：

- 大片乱码来自 PowerShell 显示链路误判
- 少量真实乱码混在启动同步相关字符串里

这说明以后处理同类问题时，最重要的不是先改文件，而是先回答两个问题：

1. 文件真实字节是不是 UTF-8 正常文本？
2. 乱码是全局显示问题，还是局部源码污染？

把这两个问题先分清，通常就能避免误修、过修和整文件无谓重写。
