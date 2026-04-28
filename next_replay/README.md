# next_replay

`next_replay` is a new, parallel replay tool project. It does not import or modify the existing `replay_platform` package.

The first version is a CLI-first MVP with a ports-and-adapters architecture:

- `domain`: stable replay types.
- `ports`: interfaces for trace readers and bus devices.
- `planning`: compiles a scenario into a replay plan.
- `runtime`: executes the plan.
- `adapters`: mock and Tongxing implementations.
- `storage`: ASC trace parsing.
- `app`: CLI use cases.

## Commands

Install and run with uv when available:

```powershell
uv sync
uv run replay-tool validate examples/mock_canfd.json
uv run replay-tool run examples/mock_canfd.json
uv run python -m unittest discover -s tests -v
```

Without uv, use Python directly from this directory:

```powershell
$env:PYTHONPATH = (Join-Path $PWD "src")
python -m replay_tool.cli validate examples/mock_canfd.json
python -m replay_tool.cli run examples/mock_canfd.json
python -m unittest discover -s tests -v
```

## Tongxing / TSMaster

The Tongxing adapter loads the SDK from `../TSMaster/Windows` by default. It imports `TSMasterApi.TSMasterAPI` from that directory and does not use the repository-root `TSMasterApi/` package.

Hardware validation must be done on Windows with TSMaster installed and a connected Tongxing device:

```powershell
uv run replay-tool devices --driver tongxing
uv run replay-tool run examples/tongxing_tc1014_canfd.json
```
