# Build next_replay as a hexagonal replay tool

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document follows `.agent/PLANS.md` in this repository. It is self-contained so that a new contributor can continue the work without reading earlier chat.

## Purpose / Big Picture

The user wants a new replay tool project that lives beside the existing `replay_platform` implementation. The new project must use a `src` layout, be managed by `uv`, follow a ports-and-adapters architecture, and prioritize Tongxing / TSMaster CAN and CANFD replay. After this work, a developer can validate a JSON replay scenario, run a mock replay from the command line, and run the same runtime against Tongxing hardware on Windows using the SDK under `TSMaster/Windows/TSMasterApi`.

## Progress

- [x] (2026-04-28 00:00Z) Confirmed `next_replay` does not exist and Python 3.12.10 is available.
- [x] (2026-04-28 00:00Z) Confirmed `uv` is not currently available on PATH in this environment.
- [x] (2026-04-28 00:00Z) Confirmed Tongxing SDK source is `TSMaster/Windows/TSMasterApi`, with demos under `TSMaster/Windows/Demo`.
- [x] (2026-04-28 00:00Z) Created the `next_replay` project skeleton with `src` layout, pyproject, examples, README, and tests.
- [x] (2026-04-28 00:00Z) Implemented domain models, ports, planner, ASC reader, runtime, mock adapter, Tongxing adapter, app service, and CLI.
- [x] (2026-04-28 00:00Z) Added unit tests for scenario parsing, ASC parsing, planning, runtime, mock adapter, and fake TSMaster integration.
- [x] (2026-04-28 00:00Z) Ran compile and unit test validation.
- [x] (2026-04-28 00:00Z) Recorded outcomes and remaining validation limits.

## Surprises & Discoveries

- Observation: `uv` is not installed in the current shell.
  Evidence: `Get-Command uv -ErrorAction SilentlyContinue` returned no command.
- Observation: `TSMasterAPI.py` imports `TSAPI` as `dll`, and that module re-exports structures such as `TLIBCAN` and `TLIBCANFD`.
  Evidence: `TSMaster/Windows/TSMasterApi/TSMasterAPI.py` starts with `from . import TSAPI as dll`.
- Observation: TSMaster demo code passes frame and buffer objects directly to wrapper functions, not `byref(...)`.
  Evidence: `TSMaster/Windows/Demo/TSMaster_Message_Send_and_Receive.py` calls `tsapp_transmit_can_async(TCAN1)` and `tsfifo_receive_can_msgs(listcanmsg, cansize, 0, READ_TX_RX_DEF.TX_RX_MESSAGES)`.
- Observation: `compileall` failed inside the default sandbox with Windows `PermissionError` while renaming `.pyc` files, then succeeded when rerun outside the sandbox.
  Evidence: the sandboxed run reported `[WinError 5]` for `.pyc` rename operations; the escalated run listed and compiled every `src` and `tests` module successfully.

## Decision Log

- Decision: Create the new project under `next_replay` and leave existing `src/replay_platform` untouched.
  Rationale: The user requested a new project architecture rather than an in-place refactor, and preserving the current tool makes comparison and migration safer.
  Date/Author: 2026-04-28 / Codex.
- Decision: Use `src/replay_tool` as the package root.
  Rationale: The user explicitly changed the plan to require `src` layout.
  Date/Author: 2026-04-28 / Codex.
- Decision: Load Tongxing API from `TSMaster/Windows`, not from root `TSMasterApi`.
  Rationale: The user explicitly requested the `TSMaster` directory because it contains the demos and API.
  Date/Author: 2026-04-28 / Codex.
- Decision: Keep the first runtime synchronous internally with a single worker thread.
  Rationale: It is enough for CLI start, pause, resume, stop, loop tests, and keeps the first implementation easy to reason about.
  Date/Author: 2026-04-28 / Codex.

## Outcomes & Retrospective

The new `next_replay` project is implemented as a separate uv-managed `src` layout package. It includes a CLI, JSON scenario parser, ASC reader, replay planner, threaded runtime, mock adapter, and Tongxing adapter that loads `TSMasterApi.TSMasterAPI` from `TSMaster/Windows`.

Validation completed with:

    python -m compileall src tests

and:

    $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONPATH=(Join-Path $PWD 'src'); python -m unittest discover -s tests -v

The unit test run executed 9 tests and passed. Hardware validation was not performed; the Tongxing tests use a fake TSMaster API wrapper and verify the intended wrapper call shapes.

## Context and Orientation

The existing project is a Windows multi-bus replay tool under `src/replay_platform`. It already has a Tongxing adapter, but this plan intentionally builds a separate project under `next_replay`. A port is an interface that core code depends on, such as `BusDevice`. An adapter is an implementation of a port, such as a mock device or a Tongxing device. The runtime is the part that executes a compiled replay plan on a timeline.

Tongxing API files live under `TSMaster/Windows/TSMasterApi`. The wrapper module is `TSMasterApi.TSMasterAPI`, and it provides functions such as `initialize_lib_tsmaster`, `tsapp_set_mapping_verbose`, `tsapp_configure_baudrate_canfd`, `tsapp_transmit_can_async`, and `tsfifo_receive_canfd_msgs`. The wrapper functions generally accept ctypes objects and call `byref` internally, so the adapter must pass frame and buffer objects directly.

## Plan of Work

Create `next_replay/pyproject.toml` for a uv-managed Python 3.12 package named `next-replay-tool`. Create `next_replay/src/replay_tool` with modules for domain models, ports, planning, runtime, storage, app orchestration, CLI, mock adapter, and Tongxing adapter.

Implement domain dataclasses for frames, channels, devices, scenarios, and snapshots. Implement JSON scenario parsing and validation. Implement an ASC reader for common CAN and CANFD Vector ASC rows. Implement a planner that loads trace frames, maps source channels to logical channels, sorts events, and emits a replay plan. Implement a runtime that opens devices, starts channels, dispatches frames according to `perf_counter_ns`, supports pause, resume, stop, and loop, then closes devices.

Implement the Tongxing adapter so it imports `TSMasterApi.TSMasterAPI` from `TSMaster/Windows` by default. It must use wrapper calls only, use `api.dll` structures, parse private enum classes from `TSMasterApi.TSEnum` when present, and fall back to demo numeric values when enum classes are absent.

Add examples for mock and Tongxing scenarios plus a small ASC file. Add unit tests with fake TSMaster modules so tests run without Windows hardware.

## Concrete Steps

Work from `C:\code\replay`. Create files with `apply_patch`. Then run from `C:\code\replay\next_replay`:

    $env:PYTHONPYCACHEPREFIX=(Join-Path $PWD ".pycache_tmp_compile"); $env:PYTHONPATH=(Join-Path $PWD "src"); python -m compileall src tests
    $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONPATH=(Join-Path $PWD "src"); python -m unittest discover -s tests -v

If `uv` is installed later, run:

    uv sync
    uv run python -m unittest discover -s tests -v

## Validation and Acceptance

Validation succeeds when compileall completes and all unit tests pass. The CLI is accepted when these commands work from `next_replay`:

    python -m replay_tool.cli validate examples/mock_canfd.json
    python -m replay_tool.cli run examples/mock_canfd.json

Hardware validation is not expected in this environment. On Windows with TSMaster and TC1014 hardware, run:

    uv run replay-tool devices --driver tongxing
    uv run replay-tool run examples/tongxing_tc1014_canfd.json

The user should observe device enumeration and CAN/CANFD traffic in TSMaster or another bus monitor.

## Idempotence and Recovery

The work is additive under `next_replay` and `.agent/next_replay_hexagonal_execplan.md`. If a file is partially created, rerun the patch after inspecting the current contents. Tests write bytecode only to a temporary pycache prefix when possible.

## Artifacts and Notes

Key validation result:

    Ran 9 tests in 0.078s
    OK

The `uv` command is not available on the current PATH, so `uv sync` was not executed in this environment.

## Interfaces and Dependencies

The package exposes a CLI module `replay_tool.cli` and console script `replay-tool`. Core code depends only on the standard library. The Tongxing adapter depends at runtime on the local SDK path `../TSMaster/Windows`, where the importable package is `TSMasterApi`.
