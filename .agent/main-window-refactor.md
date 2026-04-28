# Split main_window.py into focused UI modules

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This plan follows `.agent/PLANS.md` from the repository root. It is self-contained so a future contributor can resume the refactor from this file alone.

## Purpose / Big Picture

The current `src/replay_platform/ui/main_window.py` is a single 4,800+ line module that contains pure presenter helpers, Qt worker classes, generic collection dialogs, the scenario editor, and the main application window. This makes UI changes risky because unrelated concepts live in one file and helper tests import from the same module that defines heavy Qt classes.

After this change, `main_window.py` remains the compatibility entrypoint, but most code lives in smaller modules under `src/replay_platform/ui/`. The application should look and behave the same: existing scenarios still load through `ScenarioSpec.from_dict()` and save through `to_dict()`, replay start/pause/resume/stop behavior is unchanged, and all user-facing UI text remains Chinese. The visible outcome is internal maintainability: future edits can target focused modules, and the existing automated tests continue to pass.

## Progress

- [x] (2026-04-28 10:12 +08:00) Created this ExecPlan and recorded the intended compatibility-first refactor scope.
- [x] (2026-04-28 10:13 +08:00) Captured baseline: `main_window.py` has 4,814 lines, UTF-8 read check passed, and `tests.test_ui_helpers tests.test_ui_dialog` ran 64 tests with 11 PySide6 dialog skips.
- [x] (2026-04-28 10:20 +08:00) Moved pure helper dataclasses, constants, parsers, normalizers, and summary builders into `src/replay_platform/ui/window_presenters.py`.
- [x] (2026-04-28 10:20 +08:00) Replaced `src/replay_platform/ui/main_window.py` with a 28-line compatibility facade exporting `build_main_window`, existing helper names, and lazy Qt class access.
- [x] (2026-04-28 10:20 +08:00) Moved Qt support code into `qt_workers.py`, `collection_dialog.py`, and `styles.py`.
- [x] (2026-04-28 10:20 +08:00) Moved `ScenarioEditorDialog` into focused editor modules while preserving method and widget attribute names.
- [x] (2026-04-28 10:20 +08:00) Moved `MainWindow` into focused main-window modules while preserving method and widget attribute names.
- [x] (2026-04-28 10:25 +08:00) Updated architecture documentation to describe the split UI modules.
- [x] (2026-04-28 10:31 +08:00) Ran targeted and full validation, then recorded the final tested and untested boundaries.

## Surprises & Discoveries

- Observation: In this environment the Qt dialog tests are skipped because PySide6 is not installed, so automated validation can prove import/helper behavior but not real dialog construction.
  Evidence: baseline command `$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_ui_helpers tests.test_ui_dialog -v` reported `OK (skipped=11)` with each skipped dialog test saying `PySide6 未安装，跳过 Qt 对话框回归测试`.

- Observation: Sandbox execution blocks Python bytecode cache replacement and SQLite temp-workspace use, but the same commands pass with approved elevated execution.
  Evidence: `python -m compileall src tests` failed in the sandbox with `PermissionError: [WinError 5]` while replacing `.pyc` files under `.pycache_tmp_refactor`; rerunning with the approved elevated command passed. `tests.test_app_controller` initially failed with SQLite `disk I/O error` under `.tmp/tests`, then passed elevated after removing untracked generated test temp directories.

## Decision Log

- Decision: Treat this as a behavior-preserving refactor and keep the public import path `replay_platform.ui.main_window` stable.
  Rationale: Existing tests and the Qt launcher import from `main_window.py`; preserving that path minimizes downstream churn and proves the split did not change the user-facing surface.
  Date/Author: 2026-04-28 / Codex

- Decision: Move pure helper code before Qt code.
  Rationale: The helper layer has the broadest automated coverage and does not require PySide6, so it gives the safest first milestone and keeps non-Qt imports lightweight.
  Date/Author: 2026-04-28 / Codex

- Decision: Use mixin modules for the large existing `ScenarioEditorDialog` and `MainWindow` method groups, while keeping the final Qt classes in `scenario_editor.py` and `main_window_view.py`.
  Rationale: This keeps the refactor mechanical and preserves method names, widget attributes, and slot wiring without redesigning behavior during the split.
  Date/Author: 2026-04-28 / Codex

## Outcomes & Retrospective

The refactor is complete. `src/replay_platform/ui/main_window.py` is now a compatibility facade instead of a 4,814-line implementation module. Pure helper behavior lives in `window_presenters.py`; Qt worker/dialog/style support lives in `qt_workers.py`, `collection_dialog.py`, and `styles.py`; the scenario editor and main window now have focused final classes plus mixin modules for UI construction, state refresh, validation, bindings, and actions.

The observable behavior is intended to be unchanged. Existing helper imports from `replay_platform.ui.main_window` still work, `build_main_window(app_logic)` still returns the main window, and `ScenarioEditorDialog` is exposed lazily so non-Qt helper imports continue to work when PySide6 is missing. No scenario JSON fields, runtime replay behavior, adapter behavior, or UI copy were intentionally changed.

Validation passed with the limitations noted below. Qt dialog construction tests are still skipped in this environment because PySide6 is not installed, so no Qt manual click-through validation was performed. No Windows ZLG / 同星 hardware validation was performed because this is a UI organization refactor and does not change hardware paths.

## Context and Orientation

The desktop UI lives under `src/replay_platform/ui/`. Before this refactor, `main_window.py` contained top-level helper dataclasses and pure functions, then a `build_main_window(app_logic)` function that imported PySide6 and defined nested classes: `BackgroundTask`, `CollectionItemDialog`, `ScenarioEditorDialog`, and `MainWindow`. `src/replay_platform/ui/qt_app.py` calls `build_main_window(app_logic)`.

The core scenario contract is `src/replay_platform/core.py::ScenarioSpec`. This refactor must not change its JSON shape. The application controller in `src/replay_platform/app_controller.py` owns replay preparation, trace library access, workspace signal overrides, and runtime control. The UI must continue calling those methods with the same values. The replay engine in `src/replay_platform/runtime/engine.py` owns timeline execution and is outside this refactor.

In this plan, a "facade" means a small module that keeps old imports working while delegating implementation to new modules. A "presenter helper" means a pure function or small dataclass that converts internal state into UI text, validates form payloads, or normalizes draft dictionaries without importing PySide6.

## Plan of Work

First create `window_presenters.py` and move the existing top-level constants, dataclasses, parsing helpers, summary builders, and normalization helpers there. Update `main_window.py` to import and re-export these names so existing tests keep working.

Next extract Qt-only support classes. `BackgroundTask` belongs in `qt_workers.py`, `CollectionItemDialog` belongs in `collection_dialog.py`, and stylesheet strings plus small style application helpers belong in `styles.py`. These modules may import PySide6 because they are Qt-facing.

Then move `ScenarioEditorDialog` out of the nested `build_main_window()` body. Keep method names, widget attribute names, signals, and callback semantics unchanged. If the class is large, use mixins split by responsibility: UI construction, binding/database mapping, validation/export/save, and small utilities. Use inheritance to assemble the final class in `scenario_editor.py`.

Finally move `MainWindow` out of the nested `build_main_window()` body. Keep `build_main_window(app_logic)` as a tiny function returning `MainWindow(app_logic)`. Use mixins split by responsibility for UI construction, refresh/state synchronization, and actions/slots.

## Concrete Steps

Run commands from `C:\code\replay`.

1. Measure the file and run the current targeted UI baseline:

    `$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_ui_helpers tests.test_ui_dialog -v`

2. After moving pure helpers, run:

    `python -m compileall src tests`
    `python -m unittest tests.test_ui_helpers tests.test_app_controller -v`

3. After moving Qt classes and dialogs, run:

    `python -m compileall src tests`
    `python -m unittest tests.test_ui_helpers tests.test_ui_dialog -v`

4. After the full split and documentation update, run:

    `python -m compileall src tests`
    `python -m unittest discover -s tests -v`

## Validation and Acceptance

Acceptance requires the same import paths to keep working:

- `from replay_platform.ui.main_window import build_main_window`
- `from replay_platform.ui.main_window import _parse_int_text`
- `from replay_platform.ui.main_window import ScenarioEditorDialog` when PySide6 is installed

The helper tests must pass. If PySide6 is not installed in this environment, `tests/test_ui_dialog.py` may skip Qt dialog tests, and the final result must say that Qt manual click validation was not performed. Because this refactor does not change hardware adapters or replay timing, no Windows ZLG / 同星 hardware validation is expected.

## Idempotence and Recovery

The split is mechanical and additive until the final facade cleanup. If a module extraction fails, restore the previous import delegation for that module and rerun the targeted tests before continuing. Do not touch unrelated `.tmp` worktree changes. Do not rewrite scenario examples or persisted JSON.

## Artifacts and Notes

Baseline:

    main_window.py line count: 4814
    UTF-8 check: utf8-ok
    Targeted UI baseline: Ran 64 tests in 0.003s, OK (skipped=11)

Post-helper split validation:

    $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_ui_helpers tests.test_ui_dialog -v
    Ran 64 tests in 0.003s, OK (skipped=11)

    $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_ui_helpers tests.test_app_controller -v
    Ran 77 tests in 0.335s, OK (skipped=2)

    python -m compileall src tests
    Passed when run with approved elevated execution because sandboxed pycache replacement failed with WinError 5.

Final validation:

    python -m compileall src tests
    Passed with approved elevated execution after final import fix.

    $env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest discover -s tests -v
    Ran 179 tests in 2.112s, OK (skipped=15)

    UTF-8 self-check for docs, ExecPlan, facade, and presenter module: utf8-ok

    main_window.py final line count: 28

Untested boundaries:

    PySide6 is not installed in this environment, so tests/test_ui_dialog.py skipped 11 Qt dialog tests and no manual Qt clicking was performed.
    No Windows ZLG / 同星 hardware validation was performed.
    No DoIP or replay-timing behavior was manually exercised because this refactor did not change runtime behavior.

## Interfaces and Dependencies

`src/replay_platform/ui/main_window.py` must expose the compatibility API. New internal modules may import from `window_presenters.py` and from PySide6 where needed. Pure helper modules must not import PySide6.

The final `build_main_window` signature remains:

    def build_main_window(app_logic: ReplayApplication):
        return MainWindow(app_logic)

Revision note: initial ExecPlan created before code changes to satisfy the repository requirement for significant UI refactors.
