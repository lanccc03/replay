# Scenario Editor Resource Mapping Unification

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, the scenario editor keeps trace selection, file mapping, and DBC binding in one continuous "资源映射" workflow. A user can select trace files, map one trace source to one logical channel, and immediately configure the selected logical channel's DBC with a file picker and status feedback, without switching to a separate generic database-binding list. The proof is visible in the editor and in the saved payload: `database_bindings` still serializes as the original list structure and can still be loaded by `ScenarioSpec.from_dict()`.

## Progress

- [x] (2026-04-20 12:40Z) Re-read `.agent/PLANS.md`, `src/replay_platform/ui/main_window.py`, and the current UI tests to ground the editor structure and validation path.
- [x] (2026-04-20 12:44Z) Confirmed the current scenario editor splits `trace_file_ids`, `bindings`, and `database_bindings` across separate sections and that `database_bindings` currently uses the generic collection dialog with no file picker.
- [x] (2026-04-20 05:10Z) Added editor-side DBC draft helpers, orphan-detection helpers, mapping-completion summaries, and logical-channel indexed export helpers in `src/replay_platform/ui/main_window.py`.
- [x] (2026-04-20 06:30Z) Reworked the editor layout so trace selection and file mappings now live inside one integrated "资源映射" section with inline DBC controls, browse/clear actions, orphan DBC handling, and per-channel load status.
- [x] (2026-04-20 06:55Z) Routed scenario load, validation, and export through editor-owned `database_bindings` draft state so the saved payload remains `ScenarioSpec` compatible while duplicate and orphan DBC bindings are surfaced as warnings.
- [x] (2026-04-20 07:18Z) Added helper regression tests plus Qt dialog regression coverage for inline DBC loading/export/switch/remove flows, then ran compile and full `unittest` validation.

## Surprises & Discoveries

- Observation: the old database-binding UI was not a dedicated editor at all; it was just one generic collection section, which is why `path` only had a plain text box.
  Evidence: `ScenarioEditorDialog._build_form_tab()` created `_create_summary_list_section(... key="database_bindings" ...)`, and `CollectionItemDialog._create_input()` only builds combo, bool, json, and plain line-edit widgets.

- Observation: `database_bindings` are already tied to logical channels in the runtime, not to trace files, so the safest implementation path was UI integration rather than schema redesign.
  Evidence: `DatabaseBinding` in `src/replay_platform/core.py` stores only `logical_channel`, `path`, and `format`, and `ReplayApplication` loads them per logical channel.

- Observation: direct `python -m compileall src tests` can fail in this workspace if it writes into repository `__pycache__` directories.
  Evidence: the first compile attempt raised `PermissionError: [WinError 5]`; rerunning with `PYTHONPYCACHEPREFIX=.pycache_tmp_compile` succeeded.

- Observation: the local validation environment still does not include PySide6, so Qt dialog tests compile but skip.
  Evidence: both the targeted UI test command and the full `unittest discover` run reported `PySide6 未安装，跳过 Qt 对话框回归测试`.

## Decision Log

- Decision: keep `trace_file_ids`, `bindings`, and `database_bindings` unchanged in the persisted scenario payload.
  Rationale: repository rules require `ScenarioSpec.from_dict()` / `to_dict()` compatibility, and the user asked for editor-level unification rather than a new storage model.
  Date/Author: 2026-04-20 / Codex

- Decision: remove standalone database-binding editing from the visible form and instead edit the current logical channel's DBC inline in the binding editor.
  Rationale: this delivers the requested integrated workflow without inventing a new public data model.
  Date/Author: 2026-04-20 / Codex

- Decision: keep DBC files as external paths and add browse/status helpers rather than building a new DBC library/import pipeline.
  Rationale: the requested scope explicitly chose external paths plus file selection as the minimum-risk direction.
  Date/Author: 2026-04-20 / Codex

- Decision: keep the legacy `database_bindings` collection section instantiated but hidden instead of deleting the generic collection framework entry outright.
  Rationale: this removes the standalone edit path from the user-visible form while avoiding a larger collection-framework refactor.
  Date/Author: 2026-04-20 / Codex

## Outcomes & Retrospective

The scenario editor now exposes one integrated resource-mapping workflow. The trace checklist sits above the mapping list and editor, and the selected mapping includes an inline DBC sub-panel with logical-channel scope text, file path editing, browse and clear buttons, orphan warnings, and preview status sourced from `ReplayApplication.rebuild_override_preview(...)`.

The editor no longer depends on `_collection_data["database_bindings"]` for saved output. Instead it loads DBC bindings into a logical-channel keyed draft map, preserves last-value-wins behavior for duplicate old payloads, warns about duplicate and orphan DBC records, prunes unused bindings when the last mapping on that channel is deleted, and serializes a deduplicated `database_bindings` list back into the saved scenario payload.

Regression coverage now proves the new summary, orphan, and dedup helpers. Qt dialog coverage was also added for inline DBC loading, export, logical-channel switching, duplicate warning, orphan removal, and prune-on-delete behavior. In this environment those Qt tests skip because PySide6 is unavailable, so the strongest available validation is helper tests, compile checks, and the full `unittest` run.

## Context and Orientation

`src/replay_platform/ui/main_window.py` contains both the main window and the scenario editor dialog. Before this change, `trace_file_ids` were edited by `_build_trace_section()`, file mappings (`bindings`) were edited by `_build_binding_section()`, and `database_bindings` were edited by the generic collection-section framework created by `_create_summary_list_section()`. Validation and payload export happen in `_validate_current_draft()`, which previously validated `_draft_bindings` and `_collection_data["database_bindings"]` separately and then assembled the scenario payload directly from both.

In this repository, a "file mapping" is one `DeviceChannelBinding`-shaped draft stored in `_draft_bindings`. It usually ties one selected scenario trace file source to one logical channel and one physical adapter channel. A "database binding" is one `DatabaseBinding` record that attaches one DBC path to one logical channel. The requested feature is to make these two concepts feel like one continuous editor flow while leaving the saved JSON structure unchanged.

The relevant automated checks live in `tests/test_ui_helpers.py` and `tests/test_ui_dialog.py`. Helper tests cover pure formatting and conversion helpers. Dialog tests exercise `ScenarioEditorDialog` behavior by creating it with a temporary workspace and inspecting its internal draft state and widgets.

## Plan of Work

Add pure helper functions near the existing summary helpers in `src/replay_platform/ui/main_window.py` to convert `database_bindings` lists to a logical-channel mapping, collapse duplicates using "last binding wins", rebuild a deduplicated list for export, summarize binding rows with DBC status text, and build orphan DBC warning text plus trace mapping-completion text.

Extend `ScenarioEditorDialog` state with a logical-channel indexed DBC draft mapping, a per-channel DBC status cache, and duplicate-count tracking for old payloads. Update `load_payload()` so the incoming `database_bindings` list is converted into that mapping instead of staying in `_collection_data`. Update validation and export so the dialog serializes the mapping back into `database_bindings` during `_validate_current_draft()`, emits warnings for duplicate logical-channel bindings from the loaded payload, and preserves explicit orphan DBC entries unless the user removes them.

Rework the form layout in `ScenarioEditorDialog._build_form_tab()`. Replace the separate trace and binding sections with one "资源映射" section that contains the trace checklist at the top and the mapping list/editor split below it. Inside the binding editor panel, add a dedicated inline DBC group with a path line edit, `浏览` and `清空` buttons, a read-only `format` display, a status label, and a helper label explaining that the DBC is shared by the current logical channel.

Wire the interactions. When a binding is selected, load its logical channel's DBC data into the inline DBC controls. When the logical channel changes, refresh the DBC sub-group to the new channel without moving the old DBC automatically. When the user browses or finishes editing the path, update the current logical channel's DBC mapping and refresh preview status through `ReplayApplication.rebuild_override_preview(...)`. When a binding is removed, delete that channel's DBC only if no other draft binding still references the channel.

## Concrete Steps

Work from `C:\code\replay`.

1. Maintain this ExecPlan as implementation proceeds so progress, decisions, and validation stay accurate.
2. Edit `src/replay_platform/ui/main_window.py` to add pure helper functions for logical-channel indexed DBC draft management and resource-mapping summaries.
3. Edit `ScenarioEditorDialog` in `src/replay_platform/ui/main_window.py` to:
   - replace standalone visible `database_bindings` editing with inline DBC controls in the binding editor,
   - keep editor-only DBC draft and status state,
   - serialize that state back into `database_bindings` during validation and export.
4. Update `tests/test_ui_helpers.py` and `tests/test_ui_dialog.py` to cover the new helpers and dialog behavior.
5. Run the validation commands:

       C:\code\replay\.venv\Scripts\python.exe -m unittest tests.test_ui_helpers tests.test_ui_dialog -v
       $env:PYTHONPYCACHEPREFIX=(Join-Path $PWD '.pycache_tmp_compile'); .venv\Scripts\python.exe -m compileall src tests
       C:\code\replay\.venv\Scripts\python.exe -m unittest discover -s tests -v

## Validation and Acceptance

Acceptance is behavior, not just code shape:

- Opening the scenario editor shows one "资源映射" workflow rather than separate "场景文件" and standalone visible "数据库绑定" sections.
- Selecting a file mapping shows the current logical channel's DBC path and load status inline in the mapping editor.
- Clicking `浏览` can pick a `.dbc` file and updates the DBC path field and status label.
- Changing the selected mapping or its logical channel updates the inline DBC panel to the correct logical channel without silently moving old bindings.
- Removing the last binding for a logical channel removes that channel's DBC from the exported payload.
- Saving or exporting still produces a payload that `ScenarioSpec.from_dict()` accepts unchanged.

Validation completed with:

    C:\code\replay\.venv\Scripts\python.exe -m unittest tests.test_ui_helpers tests.test_ui_dialog -v
    $env:PYTHONPYCACHEPREFIX=(Join-Path $PWD '.pycache_tmp_compile'); .venv\Scripts\python.exe -m compileall src tests
    C:\code\replay\.venv\Scripts\python.exe -m unittest discover -s tests -v

The targeted UI test command passed. The compile command passed when redirected to a temporary pycache prefix. The full `unittest` suite passed with Qt dialog tests skipped because PySide6 is not installed in this environment.

## Idempotence and Recovery

The implementation is additive within the editor. Reloading the same scenario rebuilds the editor-only DBC mapping from the saved payload deterministically. No destructive migration is involved because the persisted data shape remains unchanged. If a future edit breaks the inline DBC panel, the safe recovery path is to keep `database_bindings` serialization isolated in `_validate_current_draft()` so UI refresh logic can be repaired without changing the saved schema.

## Artifacts and Notes

Important implementation facts:

    `ScenarioEditorDialog._build_form_tab()` now calls `_build_resource_mapping_section(...)` and hides the old visible `database_bindings` collection section.

    `ScenarioEditorDialog.load_payload()` converts the incoming `database_bindings` list into `_database_binding_drafts` and `_database_binding_duplicate_counts`.

    `ScenarioEditorDialog._validate_current_draft()` serializes `database_bindings` from `_database_binding_items()` instead of `_collection_data["database_bindings"]`.

    `tests/test_ui_helpers.py` now covers `_resource_mapping_summary`, `_trace_mapping_completion_text`, `_database_binding_map_from_items`, `_database_binding_items_from_map`, `_database_binding_orphan_items`, and `_build_orphan_database_binding_text`.

## Interfaces and Dependencies

The implementation remains inside `src/replay_platform/ui/main_window.py` and continues using `ReplayApplication.rebuild_override_preview(...)` for non-persistent DBC load status checks. No new public repo-wide types were introduced. The editor now relies on these internal behaviors:

    list[dict] -> dict[int, dict]
    Build a logical-channel keyed DBC draft map where later items override earlier items for the same channel.

    dict[int, dict] -> list[dict]
    Export a deduplicated `database_bindings` list, skipping empty paths and sorting by logical channel.

    binding draft + DBC draft/status -> str
    Build the resource-mapping list summary that shows file/source, logical channel, adapter mapping, and DBC file/status.

Update note: created this ExecPlan at implementation start because the requested change is a significant scenario-editor refactor spanning UI structure, editor state, and validation/export behavior.

Update note: marked the plan complete after wiring the integrated resource-mapping UI, rerouting `database_bindings` serialization through logical-channel draft state, adding regression tests, and validating with compile plus full `unittest` runs.
