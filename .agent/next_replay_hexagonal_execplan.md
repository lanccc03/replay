# Build next_replay as a hexagonal replay tool

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document follows `.agent/PLANS.md` in this repository. It is self-contained so that a new contributor can continue the work without reading earlier chat.

## Purpose / Big Picture

The user wants a new replay tool project that lives beside the existing `replay_platform` implementation. The new project must use a `src` layout, be managed by `uv`, follow a ports-and-adapters architecture, and prioritize Tongxing / TSMaster CAN and CANFD replay. After this work, a developer can validate a JSON replay scenario, run a mock replay from the command line, and run the same runtime against Tongxing hardware on Windows using the SDK under `TSMaster/Windows/TSMasterApi`.

The current continuation implements the first production-hardening slice of that project. After this slice, the CLI will no longer leak debug prints during replay, Tongxing fake-SDK tests and hardware validation notes will better support Windows TC1014 verification, and `next_replay` will have a first Trace Library: a small local project store that can import ASC traces, cache normalized frames, list imported traces, inspect source/message summaries, and let scenarios reference imported trace IDs instead of only raw file paths.

## Progress

- [x] (2026-04-28 00:00Z) Confirmed `next_replay` does not exist and Python 3.12.10 is available.
- [x] (2026-04-28 00:00Z) Confirmed `uv` is not currently available on PATH in this environment.
- [x] (2026-04-28 00:00Z) Confirmed Tongxing SDK source is `TSMaster/Windows/TSMasterApi`, with demos under `TSMaster/Windows/Demo`.
- [x] (2026-04-28 00:00Z) Created the `next_replay` project skeleton with `src` layout, pyproject, examples, README, and tests.
- [x] (2026-04-28 00:00Z) Implemented domain models, ports, planner, ASC reader, runtime, mock adapter, Tongxing adapter, app service, and CLI.
- [x] (2026-04-28 00:00Z) Added unit tests for scenario parsing, ASC parsing, planning, runtime, mock adapter, and fake TSMaster integration.
- [x] (2026-04-28 00:00Z) Ran compile and unit test validation.
- [x] (2026-04-28 00:00Z) Recorded outcomes and remaining validation limits.
- [x] (2026-04-29 10:05+08) Re-read the current `next_replay` implementation, existing architecture guide, and root project validation requirements before modifying files.
- [x] (2026-04-29 10:05+08) Remove runtime debug prints and add CLI tests proving validate/run output stays clean.
- [x] (2026-04-29 10:05+08) Extend Tongxing fake-SDK coverage and add Windows hardware validation documentation for TC1014.
- [x] (2026-04-29 10:05+08) Add Trace Library ports, SQLite/cache storage, app methods, CLI import/list/inspect commands, and tests.
- [x] (2026-04-29 10:05+08) Run `next_replay` unit tests and compile checks, then update outcomes with verified and unverified boundaries.

## Surprises & Discoveries

- Observation: `uv` is not installed in the current shell.
  Evidence: `Get-Command uv -ErrorAction SilentlyContinue` returned no command.
- Observation: `TSMasterAPI.py` imports `TSAPI` as `dll`, and that module re-exports structures such as `TLIBCAN` and `TLIBCANFD`.
  Evidence: `TSMaster/Windows/TSMasterApi/TSMasterAPI.py` starts with `from . import TSAPI as dll`.
- Observation: TSMaster demo code passes frame and buffer objects directly to wrapper functions, not `byref(...)`.
  Evidence: `TSMaster/Windows/Demo/TSMaster_Message_Send_and_Receive.py` calls `tsapp_transmit_can_async(TCAN1)` and `tsfifo_receive_can_msgs(listcanmsg, cansize, 0, READ_TX_RX_DEF.TX_RX_MESSAGES)`.
- Observation: `compileall` failed inside the default sandbox with Windows `PermissionError` while renaming `.pyc` files, then succeeded when rerun outside the sandbox.
  Evidence: the sandboxed run reported `[WinError 5]` for `.pyc` rename operations; the escalated run listed and compiled every `src` and `tests` module successfully.
- Observation: The existing `ReplayRuntime._run_loop()` contains bare `print(1)` and `print(2)` calls.
  Evidence: Running `python -B -m unittest discover -s tests -v` from `next_replay` passed 9 tests but printed many lines of `1` and `2` before the unittest summary.
- Observation: The old project has a much broader verified feature baseline than `next_replay`.
  Evidence: Running `python -B -m unittest discover -s tests -v` from the repository root passed 181 tests with 15 environment skips when run outside the sandbox.
- Observation: Trace Library v1 can stay schema-version compatible by resolving imported trace IDs in `ReplayApplication` before calling the planner.
  Evidence: `tests/test_trace_store.py::TraceStoreTests.test_application_compiles_scenario_using_imported_trace_id` writes a schema v1 scenario whose `traces[0].path` is the imported trace ID, and the compiled plan contains the expected CANFD frame.

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
- Decision: Implement the current request as a focused hardening and Trace Library v1 slice, not as DBC, diagnostics, ZLG, and Qt UI all at once.
  Rationale: The plan itself orders those as later milestones. Delivering a clean Trace Library and Tongxing validation base first preserves the new architecture boundary and avoids recreating the old monolithic controller shape.
  Date/Author: 2026-04-29 / Codex.
- Decision: Keep `ReplayScenario schema_version=1` compatible and allow `TraceConfig.path` to mean either a filesystem path or an imported trace ID resolved by the app layer.
  Rationale: This lets existing examples continue to work while adding imported-trace workflows without forcing a schema migration in the same slice.
  Date/Author: 2026-04-29 / Codex.

## Outcomes & Retrospective

The new `next_replay` project is implemented as a separate uv-managed `src` layout package. It includes a CLI, JSON scenario parser, ASC reader, replay planner, threaded runtime, mock adapter, and Tongxing adapter that loads `TSMasterApi.TSMasterAPI` from `TSMaster/Windows`.

Validation completed with:

    python -m compileall src tests

and:

    $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONPATH=(Join-Path $PWD 'src'); python -m unittest discover -s tests -v

The unit test run executed 9 tests and passed. Hardware validation was not performed; the Tongxing tests use a fake TSMaster API wrapper and verify the intended wrapper call shapes.

The continuation is in progress. Its intended outcome is a clean CLI and a first local Trace Library, with hardware validation still documented but not performed in this environment.

Continuation outcome on 2026-04-29: runtime debug prints were removed, CLI output is covered by tests, Tongxing fake-SDK coverage now includes multi-channel mapping, channel-count growth, FIFO sorting, close/drain behavior, and error description propagation, and Trace Library v1 is implemented. The library stores imported trace metadata in SQLite, copies the source trace, writes a JSON frame cache, exposes source and message summaries, and lets schema v1 scenarios refer to imported trace IDs through `traces[].path`.

Validation completed with:

    python -B -m unittest discover -s tests -v

which ran 17 tests and passed, and:

    python -m compileall src tests

which completed successfully. The compile command was run outside the sandbox because the Windows sandbox can deny `.pyc` rename operations. Windows TC1014 hardware validation was not performed; `next_replay/docs/tongxing-hardware-validation.md` now records the manual procedure.

## Context and Orientation

The existing project is a Windows multi-bus replay tool under `src/replay_platform`. It already has a Tongxing adapter, but this plan intentionally builds a separate project under `next_replay`. A port is an interface that core code depends on, such as `BusDevice`. An adapter is an implementation of a port, such as a mock device or a Tongxing device. The runtime is the part that executes a compiled replay plan on a timeline.

Tongxing API files live under `TSMaster/Windows/TSMasterApi`. The wrapper module is `TSMasterApi.TSMasterAPI`, and it provides functions such as `initialize_lib_tsmaster`, `tsapp_set_mapping_verbose`, `tsapp_configure_baudrate_canfd`, `tsapp_transmit_can_async`, and `tsfifo_receive_canfd_msgs`. The wrapper functions generally accept ctypes objects and call `byref` internally, so the adapter must pass frame and buffer objects directly.

The first Trace Library should live inside `next_replay` only. It should not import from `src/replay_platform`. A trace is a recorded bus file such as a Vector ASC file. Importing a trace means copying the source file into a managed directory, parsing it into normalized `Frame` objects, writing a compact JSON cache for those frames, and storing metadata in SQLite. A source summary groups frames by recorded source channel and bus type. A message summary groups message IDs by recorded source channel and bus type. These summaries let CLI users and future UI views choose mappings without reading the full trace every time.

## Plan of Work

Create `next_replay/pyproject.toml` for a uv-managed Python 3.12 package named `next-replay-tool`. Create `next_replay/src/replay_tool` with modules for domain models, ports, planning, runtime, storage, app orchestration, CLI, mock adapter, and Tongxing adapter.

Implement domain dataclasses for frames, channels, devices, scenarios, and snapshots. Implement JSON scenario parsing and validation. Implement an ASC reader for common CAN and CANFD Vector ASC rows. Implement a planner that loads trace frames, maps source channels to logical channels, sorts events, and emits a replay plan. Implement a runtime that opens devices, starts channels, dispatches frames according to `perf_counter_ns`, supports pause, resume, stop, and loop, then closes devices.

Implement the Tongxing adapter so it imports `TSMasterApi.TSMasterAPI` from `TSMaster/Windows` by default. It must use wrapper calls only, use `api.dll` structures, parse private enum classes from `TSMasterApi.TSEnum` when present, and fall back to demo numeric values when enum classes are absent.

Add examples for mock and Tongxing scenarios plus a small ASC file. Add unit tests with fake TSMaster modules so tests run without Windows hardware.

For the continuation, first remove the debug prints in `next_replay/src/replay_tool/runtime/engine.py` and rely on the existing logger for human-visible replay messages. Add CLI tests that call `replay_tool.cli.main()` under captured stdout/stderr and prove `validate` and `run` show only the intended status lines.

Second, extend `next_replay/tests/test_tongxing_adapter.py`. The fake API should be exercised for multiple physical channels, explicit channel-count growth, receive FIFO conversion across channels, close/drain behavior, and error descriptions. Add a Chinese hardware validation document at `next_replay/docs/tongxing-hardware-validation.md` with exact Windows commands, expected observations, and fields for recording TC1014 device, channel, baud rate, send, receive, and cleanup results.

Third, add Trace Library interfaces and implementation. Create a storage-backed port in `next_replay/src/replay_tool/ports/trace_store.py` or equivalent names exported from `ports/__init__.py`. Implement the concrete store in `next_replay/src/replay_tool/storage/trace_store.py` using only the standard library: `sqlite3`, `json`, `shutil`, `uuid`, `datetime`, and `pathlib`. The default store root should be `next_replay/.replay_tool` when running from the project directory, unless the CLI receives `--workspace`. Add app methods `import_trace`, `list_traces`, `inspect_trace`, and make `compile_plan` resolve `TraceConfig.path` through the store when it is an imported trace ID. Add CLI subcommands `import`, `traces`, and `inspect`.

## Concrete Steps

Work from `C:\code\replay`. Create files with `apply_patch`. Then run from `C:\code\replay\next_replay`:

    $env:PYTHONPYCACHEPREFIX=(Join-Path $PWD ".pycache_tmp_compile"); $env:PYTHONPATH=(Join-Path $PWD "src"); python -m compileall src tests
    $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONPATH=(Join-Path $PWD "src"); python -m unittest discover -s tests -v

If `uv` is installed later, run:

    uv sync
    uv run python -m unittest discover -s tests -v

During the continuation, run these command examples from `C:\code\replay\next_replay` after implementing the Trace Library:

    python -m replay_tool.cli import examples/sample.asc
    python -m replay_tool.cli traces
    python -m replay_tool.cli inspect <trace-id-from-import-output>

The import command should print a stable line containing the imported trace ID, name, event count, and cache path. The traces command should list the same ID. The inspect command should show source summaries and message IDs without reparsing the ASC file directly.

## Validation and Acceptance

Validation succeeds when compileall completes and all unit tests pass. The CLI is accepted when these commands work from `next_replay`:

    python -m replay_tool.cli validate examples/mock_canfd.json
    python -m replay_tool.cli run examples/mock_canfd.json

Hardware validation is not expected in this environment. On Windows with TSMaster and TC1014 hardware, run:

    uv run replay-tool devices --driver tongxing
    uv run replay-tool run examples/tongxing_tc1014_canfd.json

The user should observe device enumeration and CAN/CANFD traffic in TSMaster or another bus monitor.

For the Trace Library slice, validation also succeeds when tests demonstrate that importing `examples/sample.asc` creates SQLite metadata and a cache file, listing returns the imported record, inspecting returns source and message summaries, and compiling a scenario whose trace path is the imported trace ID produces the same replay plan as compiling against the raw ASC path.

## Idempotence and Recovery

The work is additive under `next_replay` and `.agent/next_replay_hexagonal_execplan.md`. If a file is partially created, rerun the patch after inspecting the current contents. Tests write bytecode only to a temporary pycache prefix when possible.

The Trace Library writes generated runtime data under `.replay_tool/`, which is not source. If manual CLI runs leave that directory behind, it can be deleted safely after validation. Do not delete source files or the existing root `.replay_platform` data directory as part of this plan.

## Artifacts and Notes

Key validation result:

    Ran 9 tests in 0.078s
    OK

The `uv` command is not available on the current PATH, so `uv sync` was not executed in this environment.

Continuation validation artifacts should be appended here after implementation.

Continuation validation result:

    Ran 17 tests in 0.179s
    OK

    python -m compileall src tests
    ... completed successfully

## Interfaces and Dependencies

The package exposes a CLI module `replay_tool.cli` and console script `replay-tool`. Core code depends only on the standard library. The Tongxing adapter depends at runtime on the local SDK path `../TSMaster/Windows`, where the importable package is `TSMasterApi`.

At the end of the Trace Library slice, `replay_tool.ports` must export trace storage contracts such as `TraceStore`, `TraceRecord`, and `TraceInspection`. `replay_tool.storage` must export a concrete SQLite-backed implementation. `ReplayApplication` must accept an optional workspace/root for the store while preserving existing `ReplayApplication()` behavior in tests and examples.

Revision note, 2026-04-29 / Codex: appended the current hardening and Trace Library v1 scope because the user asked to implement the previously proposed next-step plan. The revision narrows immediate implementation to the ordered early milestones and explicitly leaves DBC, diagnostics, ZLG parity, and Qt UI for later plans.
