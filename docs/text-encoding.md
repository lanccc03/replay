# 文本编码排查与预防

本文记录仓库中中文文本出现乱码时的排查顺序与预防约定，重点适用于 Windows + PowerShell + UTF-8 源码的组合场景。

## 1. 仓库基线约束

- 仓库内源码、文档、配置文件默认使用 UTF-8 保存。
- 仓库根目录的 [`.editorconfig`](../.editorconfig) 负责提供默认 UTF-8 基线；新增文本文件时不要改成 GBK / ANSI。
- 涉及中文 UI 文案、日志、注释、Markdown 的改动，提交前至少做一次显式 UTF-8 自检。
- 在 PowerShell 中读取、写入、输出中文内容时显式指定 UTF-8，不要只凭默认编码链路下结论。
- 执行任务时的强约束以 [`agents.md`](../agents.md) 为准，本文提供详细操作步骤与判断标准。

## 2. 先记住的结论

- 先确认文件真实字节，再判断源码是否损坏。
- 看到乱码，不要立刻假设“文件已经坏了”；很多时候只是终端或编辑器按错编码显示。
- 发现乱码时，先区分“显示乱码”还是“源码污染”，不要一上来就整文件转码。
- 如果只有少量中文字符串损坏，只修局部，不要整文件重写编码。

## 3. 常见症状

- PowerShell 中 `Get-Content` 看起来是一整片乱码，但编辑器里部分中文又是正常的。
- 同一个文件里，前半段中文正常、后半段只有少量字符串异常。
- 日志、异常消息、UI 文案或文档标题里出现类似 “鍥炴斁”“閫傞厤鍣?” 这样的文本。

这类情况通常有两种来源：

- 显示乱码：文件本身仍是 UTF-8，终端或编辑器按错误编码解释。
- 源码乱码：文件里真的混入了错误字符，往往是局部污染，不一定整文件都坏。

## 4. 推荐排查步骤

### 4.1 先固定 PowerShell 输出编码

如果要在 PowerShell 中直接查看中文，先设置输出编码：

```powershell
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
```

这一步只能降低“显示链路误判”的概率，不能证明文件本身一定正常。

### 4.2 读取时显式指定 UTF-8

不要直接用裸 `Get-Content` 判断源码是否损坏，优先显式写出编码：

```powershell
Get-Content -Path src/replay_platform/ui/main_window.py -Encoding utf8 | Select-Object -First 40
```

如果只是想快速确认几行文本，这一步通常就够了。

### 4.3 用 Python 按 UTF-8 直接读取

```powershell
@'
from pathlib import Path
path = Path("src/replay_platform/runtime/engine.py")
for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
    if "回放" in line or "适配器" in line:
        print(i, line)
'@ | python -X utf8 -
```

如果这里读出来是正常中文，而 PowerShell 普通查看仍是乱码，问题大概率在显示链路，不在源码本体。

### 4.4 必要时打印 `unicode_escape`

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

- 正常中文：会显示为 `\u56de\u653e` 这类标准 Unicode 转义。
- 异常乱码：常见为杂乱 Unicode，甚至混入私有区字符。

### 4.5 扫描异常字符，不要只看肉眼观感

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

### 4.6 只修真正坏掉的局部文本

- 如果 Python 直读结果正常，不要整文件重写编码。
- 如果只有个别异常消息、日志文案、UI 文案或注释损坏，只修这些局部字符串。
- 修完后再跑一次 UTF-8 直读与异常字符扫描，确认没有残留。

## 5. PowerShell 读写中文时的约束

### 5.1 读取

- 优先使用 `Get-Content -Encoding utf8`。
- 搜索中文时，不要只看终端回显；必要时配合 `python -X utf8` 二次确认。

```powershell
Get-Content -Path docs/text-encoding.md -Encoding utf8
Get-Content -Path src/replay_platform/ui/main_window.py -Encoding utf8 | Select-Object -Skip 1800 -First 80
```

### 5.2 写入

- 写入中文文本时显式指定 UTF-8，不要依赖默认编码。
- PowerShell 写文件时优先显式指定 `-Encoding utf8`：

```powershell
Set-Content -Path docs/probe.txt -Encoding utf8 -Value "中文探针"
Out-File -FilePath docs/probe.txt -Encoding utf8 -InputObject "中文探针"
```

- 如果是在仓库中改源码、文档或配置文件，优先使用编辑器、`apply_patch`，或 Python 的 `encoding="utf-8"` 显式读写。
- 如果必须用 PowerShell 重写已有仓库文件，先确认不会无意引入 BOM 或整文件重编码，再执行写回。

## 6. 中文改动后的最小自检

- 确认改动文件仍能被 UTF-8 直接读取。
- 确认没有把 PowerShell 显示乱码误判成源码损坏。
- 确认没有把局部字符串问题升级成整文件编码改动。
- 确认最近新增或修改的中文文案在 `git diff` 中可直接读懂，没有出现明显乱码片段。

示例：

```powershell
Get-Content -Path src/replay_platform/ui/main_window.py -Encoding utf8 | Select-Object -Skip 1800 -First 80
python -X utf8 -c "from pathlib import Path; Path('src/replay_platform/ui/main_window.py').read_text(encoding='utf-8'); print('utf8-ok')"
git diff -- src/replay_platform/ui/main_window.py
```

## 7. 本仓库的经验总结

本仓库已经出现过两类典型案例：

1. `engine.py` 看起来“很多乱码”，但真实问题主要来自 PowerShell 显示链路误判。
2. `main_window.py` 的“资源映射”区域曾出现局部源码污染，真正坏掉的是少量新增中文字符串，而不是整文件编码。

这说明处理同类问题时，最重要的不是先改文件，而是先回答两个问题：

1. 文件真实字节是不是 UTF-8 正常文本？
2. 乱码是全局显示问题，还是局部源码污染？

把这两个问题先分清，通常就能避免误修、过修和整文件无谓重写。
