# Staged Scenario, Application, Runtime, and Adapter Refactor

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This plan follows `.agent/PLANS.md` from the repository root. It is self-contained so a future contributor can resume the refactor from this file alone.

## Purpose / Big Picture

This refactor improves maintainability without changing user-visible behavior. The current project already split the old monolithic main-window file, unified scenario resource mapping, and optimized large-trace startup paths. The remaining pressure points are now mostly internal: scenario draft validation still lives beside Qt dialog code, `ReplayApplication` still owns several unrelated orchestration responsibilities, `ReplayEngine` still owns frame preparation, diagnostic queueing, health snapshots, and timing state in one class, and ZLG/Tongxing adapters duplicate CAN frame encoding details.

After this change, contributors can edit scenario validation, replay preparation, runtime frame dispatch, diagnostic queueing, health snapshots, and adapter frame encoding in focused modules. The observable behavior remains the same: existing scenarios still load through `ScenarioSpec.from_dict()` and save through `to_dict()`, UI copy stays Chinese, replay start/pause/resume/stop behavior is unchanged, and ZLG/Tongxing true hardware behavior remains limited to Windows validation.

## Progress

- [x] (2026-04-28 00:00 +08:00) Created this ExecPlan before implementation.
- [x] (2026-04-28) Extracted Qt-free scenario draft validation and normalization into `replay_platform.ui.scenario_draft` while preserving compatibility exports from `window_presenters`.
- [x] (2026-04-28) Extracted `ReplayApplication` trace preparation, runtime override/database binding, and adapter/diagnostic construction collaborators.
- [x] (2026-04-28) Extracted `ReplayEngine` frame preparation helpers, diagnostic worker, and adapter health snapshot policy.
- [x] (2026-04-28) Extracted shared CAN/CANFD/J1939 frame codec helpers for ZLG and Tongxing adapters.
- [x] (2026-04-28) Ran focused and full validation and recorded tested/untested boundaries.

## Surprises & Discoveries

- Observation: The worktree was clean at the start of this refactor.
  Evidence: `git status --short` returned no files.

- Observation: Sandbox-local `compileall` and temp SQLite tests can fail on Windows with `WinError 5` / `sqlite3.OperationalError: disk I/O error` while writing under repository-local temporary directories.
  Evidence: Initial non-escalated `compileall` and the stage 2 unittest batch failed with pycache/temp cleanup permission errors; rerunning the same validations with approved elevated permissions passed.

- Observation: PySide6 is not installed in this environment, and `cantools` is not installed.
  Evidence: Qt dialog tests skipped with `PySide6 未安装`; real DBC parsing tests skipped with `cantools 未安装`.

- Observation: Existing tests still patch `replay_platform.app_controller.TongxingDeviceAdapter` and construct a lightweight `ReplayApplication` with `__new__`.
  Evidence: The first elevated stage 2 run exposed compatibility failures. The app facade now keeps patchable adapter class imports and lazily creates `RuntimeOverrideCoordinator` for those lightweight tests.

## Decision Log

- Decision: Preserve all current public and compatibility entrypoints.
  Rationale: Existing UI, tests, and scripts import from `replay_platform.ui.main_window`, call `ReplayApplication.prepare_replay()` / `start_prepared_replay()`, and use `ScenarioSpec.from_dict()` / `to_dict()`. This refactor is not a feature change or data migration.
  Date/Author: 2026-04-28 / Codex

- Decision: Extract behavior behind adapters and facades before deleting old helper names.
  Rationale: Small additive modules with compatibility re-exports keep the refactor easy to bisect and reduce the chance of breaking Qt tests in environments where PySide6 is unavailable.
  Date/Author: 2026-04-28 / Codex

- Decision: Keep `ReplayApplication` private methods as delegating compatibility shims.
  Rationale: They are not user-facing, but tests and future maintainers use them as narrow verification seams for trace preparation, adapter construction, and override behavior.
  Date/Author: 2026-04-28 / Codex

- Decision: Preserve adapter-specific CAN ID behavior while sharing common helpers.
  Rationale: Tongxing expects a masked 29-bit arbitration ID and an explicit extended flag, while the existing ZLG send path only added the high-bit marker for J1939. The shared codec encodes those differences with separate helper names instead of forcing one interpretation.
  Date/Author: 2026-04-28 / Codex

## Outcomes & Retrospective

Completed. The refactor introduced focused internal modules without changing persisted scenario JSON, public `ReplayApplication` entrypoints, or hardware lifecycle behavior.

The biggest practical improvement is separation of pure data/validation logic from Qt and runtime side effects. Scenario draft validation can now be tested without PySide6, application replay preparation and runtime override handling no longer live directly inside the controller body, and the engine delegates frame dispatch, diagnostic worker lifecycle, and adapter health caching to focused helpers.

No schema migration was required, and the adapter changes stayed at the frame-conversion layer only.

## Context and Orientation

The desktop UI lives under `src/replay_platform/ui/`. `window_presenters.py` is a Qt-free helper module, but it still contains scenario draft parsing and validation helpers. `scenario_editor_validation.py` owns the Qt dialog validation flow and currently performs most draft validation inline in `_validate_current_draft()`.

`src/replay_platform/app_controller.py` defines `ReplayApplication`, which is the UI-facing application orchestrator. It owns trace library access, workspace signal overrides, database loading, replay frame preparation, prepared trace caching, adapter construction, diagnostic client construction, and engine start/stop calls.

`src/replay_platform/runtime/engine.py` defines `ReplayEngine`, which owns timeline state, frame batching, scheduled send decisions, diagnostic queueing, link actions, adapter health snapshots, logging, stats, pause/resume, loop playback, and cleanup.

`src/replay_platform/adapters/zlg.py` and `src/replay_platform/adapters/tongxing.py` both convert `FrameEvent` objects into hardware-specific CAN/CANFD frames. A small shared helper can centralize common decisions such as extended ID handling, raw CAN ID masking, payload clipping, DLC calculation, and flag interpretation while leaving hardware calls untouched.

## Plan of Work

First, create a Qt-free scenario draft module. Move or wrap the parsing, normalization, and scenario draft validation logic there, and make `scenario_editor_validation.py` call that module. Keep `window_presenters.py` re-exporting the old helper names so tests and compatibility imports continue to work.

Second, split `ReplayApplication` internals into collaborators. A trace preparation helper should own trace-bound binding grouping, source filtering, cache keys, per-binding mapping, and merge-based frame assembly. A runtime override helper should own database loading and signal override validation/application. An adapter factory should own construction of ZLG, Tongxing, Mock, CAN UDS, and DoIP clients. `ReplayApplication` remains the public facade.

Third, split `ReplayEngine` internals. A frame dispatch helper should own enabled-frame filtering and logical-to-physical frame preparation. A diagnostic worker should own the queue/thread lifecycle. A health snapshot cache should own throttled adapter health collection. `ReplayEngine` remains the owner of timing state and public lifecycle methods.

Fourth, add shared adapter frame-codec helpers and update ZLG/Tongxing to use them for common CAN/CANFD/J1939 decisions. Do not change device open, channel start, send, read, reconnect, or hardware API call order.

## Concrete Steps

Run commands from `C:\code\replay`.

1. Edit only repository files under `src/`, `tests/`, and `.agent/`. Keep all files UTF-8.
2. Add focused modules and update imports in small steps.
3. After scenario draft extraction, run:

       python -m compileall src tests
       python -m unittest tests.test_ui_helpers tests.test_ui_dialog -v

4. After application-controller extraction, run:

       python -m unittest tests.test_app_controller tests.test_library tests.test_signal_catalog -v

5. After runtime extraction, run:

       python -m unittest tests.test_engine -v

6. After adapter codec extraction, run:

       python -m unittest tests.test_zlg_adapter tests.test_tongxing_adapter tests.test_engine -v

7. At the end, run:

       python -m compileall src tests
       python -m unittest discover -s tests -v

## Validation and Acceptance

Acceptance requires all current public behavior to remain stable. Existing scenario JSON files must continue to round-trip through `ScenarioSpec.from_dict()` and `to_dict()`. Existing UI helper imports from `replay_platform.ui.main_window` and `replay_platform.ui.window_presenters` must keep working. Replay launch source fallback, trace file mapping, database bindings, signal overrides, frame enables, pause/resume, loop playback, link disconnect/reconnect, startup sync, diagnostic queue completion, and adapter mock tests must behave as before.

If PySide6 is unavailable, Qt dialog tests may skip. The final result must state whether Qt manual click validation was performed. Because ZLG and Tongxing real hardware can only be validated on Windows with devices attached, the final result must state that hardware validation was not performed unless it actually was.

## Idempotence and Recovery

Each extraction should be additive first. Keep existing public methods as delegates until tests pass. If one extraction fails, revert only that focused module and leave completed earlier phases intact. Do not change persisted scenario schema, examples, or workspace data. Do not delete generated cache directories unless they are clearly created by the validation commands and cleanup is explicitly safe.

## Artifacts and Notes

Validation run from `C:\code\replay`:

    $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_ui_helpers tests.test_ui_dialog -v
    $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_app_controller tests.test_library tests.test_signal_catalog -v
    $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_engine -v
    $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_zlg_adapter tests.test_tongxing_adapter tests.test_engine -v
    $env:PYTHONPYCACHEPREFIX=(Join-Path $PWD '.pycache_tmp_compile'); if (Test-Path $env:PYTHONPYCACHEPREFIX) { Remove-Item -LiteralPath $env:PYTHONPYCACHEPREFIX -Recurse -Force -ErrorAction SilentlyContinue }; python -m compileall src tests
    $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest discover -s tests -v
    git diff --check

Results:

- UI helper/dialog target: 64 tests passed, 11 skipped because PySide6 is unavailable.
- Application/library/signal catalog target: 46 tests passed, 4 skipped because cantools is unavailable.
- Engine target: 46 tests passed.
- Adapter + engine target: 60 tests passed.
- Compileall passed.
- Full unittest discover: 181 tests passed, 15 skipped.
- `git diff --check` passed; Git only reported CRLF normalization warnings for modified files.

Not validated:

- No Qt manual click-through was performed.
- No Windows ZLG or Tongxing real hardware validation was performed.
- No new evidence was gathered for replay timing performance changes; this refactor intentionally did not revisit sync send or intra-2ms spacing as a performance solution.

## Interfaces and Dependencies

Stable external interfaces:

    ScenarioSpec.from_dict(payload)
    ScenarioSpec.to_dict()
    ReplayApplication.prepare_replay(...)
    ReplayApplication.start_prepared_replay(...)
    ReplayApplication.start_replay(...)
    replay_platform.ui.main_window.build_main_window(app_logic)

New modules introduced by this refactor should be internal implementation details and should avoid PySide6 unless they are explicitly Qt-facing.

Revision note: initial ExecPlan created before code changes to satisfy the repository requirement for significant refactors spanning UI, application controller, runtime, and adapters.
