# CAN DBC Usage Closure

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

After this change, a user can bind a CAN/CAN FD DBC to a logical channel, see readable message and signal candidates in the workspace override panel, apply temporary overrides for the current debugging session, and optionally write those overrides back into the current scenario payload. Replay startup will rebuild database and override state every time, so stale DBC data or stale overrides from a previous scenario no longer leak into the next run.

The user-visible proof is straightforward: bind a valid `.dbc`, load a scenario, open the workspace signal override tab, choose `0x123 | MessageName`, choose a signal, see its metadata hint, apply an override, start replay, and observe that the transmitted frame uses the overridden value. If the DBC cannot be loaded and an active override depends on it, replay startup must fail with a clear channel/path/reason message.

## Progress

- [x] (2026-04-20 08:39Z) Read the existing DBC, replay, and UI paths in `src/replay_platform/services/signal_catalog.py`, `src/replay_platform/app_controller.py`, `src/replay_platform/runtime/engine.py`, `src/replay_platform/ui/main_window.py`, and the matching tests.
- [x] (2026-04-20 08:45Z) Confirmed the current repo only has a thin `cantools` loading path, no real DBC fixture, and no separation between workspace overrides and runtime overrides.
- [x] (2026-04-20 09:32Z) Implemented service-layer catalog/query/reset changes in `src/replay_platform/services/signal_catalog.py`, including explicit `format="dbc"` validation, message/signal catalog metadata, and separate codec vs override clearing.
- [x] (2026-04-20 10:04Z) Implemented application-layer workspace override state and replay startup validation in `src/replay_platform/app_controller.py`, including separate preview/runtime override services and replay-time DBC rebuild logic.
- [x] (2026-04-20 10:41Z) Implemented UI status, load/write-back actions, and signal metadata hints in `src/replay_platform/ui/main_window.py`, including per-channel database status text, `0xID | Name` message display, and workspace-to-scenario write-back.
- [x] (2026-04-20 11:18Z) Added a real DBC fixture and updated `tests/test_signal_catalog.py`, `tests/test_app_controller.py`, `tests/test_ui_helpers.py`, and `tests/test_ui_dialog.py`.
- [x] (2026-04-20 11:54Z) Ran validation and captured tested / untested boundaries: targeted DBC/UI/app-controller tests passed, full `unittest discover -s tests -v` passed (`166` tests, `5` skipped), and `python -m compileall src tests` passed with a repo-local `PYTHONPYCACHEPREFIX`.

## Surprises & Discoveries

- Observation: the repo documentation already promises “DBC / J1939 DBC”, but the current editor only offers `format="dbc"` and the implementation ignores the `format` field completely.
  Evidence: `src/replay_platform/ui/main_window.py` limits the format combo to `("dbc",)` and `src/replay_platform/services/signal_catalog.py` previously accepted only `path`.

- Observation: the workspace override table currently reads directly from `SignalOverrideService`, so runtime cleanup clears the visible UI state even though the user may expect a session-scoped override set to persist.
  Evidence: `src/replay_platform/ui/main_window.py:_refresh_overrides()` reads `self.app_logic.signal_overrides.list_overrides()`, while `src/replay_platform/runtime/engine.py:stop()` calls `self.signal_overrides.clear_all()`.

- Observation: the test suite has no real `.dbc` fixture, so the current DBC behavior is only validated through the synthetic `StaticMessageCodec`.
  Evidence: `tests/test_signal_catalog.py` only instantiates `StaticMessageCodec`, and `rg --files -g "*.dbc"` returned no DBC fixture before this work.

- Observation: keeping one shared `SignalOverrideService` for both UI preview and runtime state makes replay teardown erase the user's workspace editing context.
  Evidence: `ReplayEngine.stop()` clears overrides, while the workspace override tab needs session persistence across stop/start for the same current scenario.

- Observation: the desktop sandbox can fail full-test or compileall flows when Python writes bytecode or sqlite scratch data outside the workspace.
  Evidence: the successful validation path used repo-local temp directories in `tests/bootstrap.py` and a repo-local `PYTHONPYCACHEPREFIX` for `compileall`.

## Decision Log

- Decision: scope this iteration to CAN / CAN FD DBC usage closure and explicitly not implement J1939/PGN semantics.
  Rationale: the requested plan and current UI wording both need closure around real DBC usability first; introducing PGN-aware matching now would enlarge the change surface and testing burden.
  Date/Author: 2026-04-20 / Codex

- Decision: keep the persisted scenario structure unchanged, including `SignalOverride.message_id_or_pgn` and `DatabaseBinding.format`.
  Rationale: the repository rules require JSON compatibility through `ScenarioSpec.from_dict()` / `to_dict()`, and the user explicitly asked to preserve structure compatibility.
  Date/Author: 2026-04-20 / Codex

- Decision: model workspace overrides as application-owned session state instead of storing them only inside `SignalOverrideService`.
  Rationale: replay startup and teardown need a clean runtime service, while the workspace UI must preserve temporary overrides across stop/start within the same current scenario.
  Date/Author: 2026-04-20 / Codex

- Decision: use two `SignalOverrideService` instances in `ReplayApplication`: one for workspace preview/catalog state and one for runtime replay application.
  Rationale: this keeps the runtime service disposable and replay-scoped without letting `ReplayEngine.stop()` wipe UI catalog state or session overrides.
  Date/Author: 2026-04-20 / Codex

- Decision: validate workspace/scenario overrides only after attempting to load all database bindings, and block replay or scenario write-back only when an active override depends on a failed binding.
  Rationale: raw-frame replay should remain available when DBC loading fails on an unused channel, but signal-level operations need a deterministic failure when their required DBC metadata is unavailable.
  Date/Author: 2026-04-20 / Codex

## Outcomes & Retrospective

- Implemented the requested CAN/CAN FD DBC usage closure without changing scenario JSON structure. `SignalOverrideService` now exposes message/signal catalog metadata, validates `format="dbc"`, and separates codec cleanup from override cleanup. `ReplayApplication` now owns workspace-scoped temporary overrides separately from runtime replay overrides, rebuilds runtime DBC state on every replay start, and enforces "scenario defaults first, workspace overrides second" precedence.

- The main-window workflow is now materially complete for the requested path: per-channel DBC load status is shown in scenario summary and override status text; message choices display as `0x123 | MessageName`; signal metadata hints show unit/range/enums; the user can load persisted scenario overrides into the workspace session or write workspace overrides back into the current scenario payload without saving the file automatically.

- Added a real `.dbc` fixture (`tests/fixtures/sample_vehicle.dbc`) and expanded service/app/UI coverage around metadata queries, format validation, runtime rebuild behavior, failed-binding handling, summary text, and scenario write-back compatibility.

- Validation outcome:
  - `C:\code\replay\.venv\Scripts\python.exe -m unittest discover -s tests -v` passed with `166` tests run and `5` skipped.
  - `C:\code\replay\.venv\Scripts\python.exe -m compileall src tests` passed using `PYTHONPYCACHEPREFIX=.pycache_tmp_compile`.
  - Not performed: Qt manual click-through validation and Windows hardware validation against ZLG / 同星 devices.

## Context and Orientation

`src/replay_platform/services/signal_catalog.py` owns database loading plus “decode -> patch -> encode” behavior. Today it exposes only message IDs, signal names, and raw override application. `src/replay_platform/app_controller.py` owns replay startup. Today it loads DBC files directly into the shared `SignalOverrideService`, then copies scenario overrides into the same shared store. `src/replay_platform/runtime/engine.py` uses that service during frame preparation and clears all overrides on stop. `src/replay_platform/ui/main_window.py` has two distinct override surfaces: scenario-level persisted `signal_overrides` inside the scenario editor, and workspace-level “信号覆盖” actions in the main window. Right now the workspace surface still talks straight to `SignalOverrideService`, so it does not have its own durable session state.

In this repository, a “workspace override” means a temporary signal override that belongs to the current UI session and current scenario selection. A “scenario initial override” means the persisted `signal_overrides` array inside the scenario JSON. A “catalog” means DBC-derived display/query metadata such as message names, signal names, units, value ranges, and enumerated choices.

## Plan of Work

First extend `src/replay_platform/services/signal_catalog.py`. Add lightweight dataclasses for message and signal catalog entries, teach `load_database()` to require `format="dbc"`, and add explicit `clear_codecs()` alongside the existing override-clearing behavior. Keep the `StaticMessageCodec` path for fast unit tests, but make the `cantools` path expose enough metadata for the UI.

Then update `src/replay_platform/app_controller.py`. Add an application-owned workspace override collection plus helper methods to list, replace, clear, and validate it against the current database bindings. Replay startup must now create a fresh runtime override service state on each run: clear codecs, clear runtime overrides, load scenario DBC bindings, validate scenario and workspace overrides against the load results, then apply scenario overrides followed by workspace overrides.

After that update `src/replay_platform/ui/main_window.py`. The scenario summary should display per-channel database status instead of only filenames. The workspace override tab needs two new actions to load scenario initial overrides into the workspace state and to write the current workspace overrides back into the current scenario payload. Message candidates should display `0xID | Name`, signal metadata should be rendered as a short hint line, and UI refreshes must read workspace overrides from the new application-owned state instead of the runtime service.

Finally add a real DBC fixture under `tests/fixtures/`, extend the tests listed above, and run the repo-required validation commands.

## Concrete Steps

Work from `C:\code\replay`.

1. Edit `.agent/can-dbc-usage-closure.md` as implementation proceeds so `Progress`, `Decision Log`, and `Outcomes & Retrospective` stay current.
2. Edit `src/replay_platform/services/signal_catalog.py` to add catalog entry types, format-aware loading, metadata query helpers, and separate codec reset behavior.
3. Edit `src/replay_platform/app_controller.py` to add workspace override state plus replay-time validation/rebuild logic.
4. Edit `src/replay_platform/ui/main_window.py` to surface database load state, workspace override actions, metadata hints, and scenario write-back behavior.
5. Add a real DBC fixture in `tests/fixtures/`.
6. Update the DBC, app controller, and UI tests.

## Validation and Acceptance

Run these commands from `C:\code\replay` after the code changes:

    $env:PYTHONPYCACHEPREFIX = (Join-Path $PWD ".pycache_tmp")
    python -m unittest discover -s tests -v

Acceptance is:

- A valid DBC exposes message names and signal names in the workspace override UI model.
- Scenario initial overrides and workspace overrides are both preserved in the current scenario flow, with workspace overrides taking precedence at replay start.
- Stopping replay does not clear the workspace override table.
- Switching to a different current scenario clears workspace overrides.
- DBC load failures are visible in scenario summary / validation and block replay only when an active override depends on the failed binding.

## Idempotence and Recovery

All code changes are additive and can be re-run safely. If a partial implementation breaks replay startup, the recovery path is to continue using raw-frame replay with no active overrides after clearing workspace overrides and removing invalid DBC bindings from the current scenario.

## Artifacts and Notes

Important baseline observations gathered before editing:

    python -m unittest tests.test_signal_catalog -v
    -> existing five StaticMessageCodec-based signal catalog tests pass

    python -m unittest tests.test_engine.ReplayEngineTests.test_prepare_frame_groups_maps_enabled_frames_by_adapter_and_skips_disabled_frames -v
    -> runtime override application path is currently green

## Interfaces and Dependencies

`src/replay_platform/services/signal_catalog.py` should define stable dataclasses for the UI-facing metadata:

    @dataclass(frozen=True)
    class MessageCatalogEntry:
        message_id: int
        message_name: str

    @dataclass(frozen=True)
    class SignalCatalogEntry:
        message_id: int
        signal_name: str
        unit: str
        minimum: Any
        maximum: Any
        choices: dict[int, str]

`SignalOverrideService` must expose:

    load_database(logical_channel: int, path: str, *, format: str = "dbc") -> None
    clear_codecs() -> None
    list_messages(logical_channel: int) -> list[MessageCatalogEntry]
    list_signals(logical_channel: int, message_id: int) -> list[SignalCatalogEntry]

`ReplayApplication` must expose:

    list_workspace_signal_overrides() -> list[SignalOverride]
    replace_workspace_signal_overrides(overrides: Sequence[SignalOverride]) -> None
    clear_workspace_signal_overrides() -> None
    rebuild_override_preview(bindings: Sequence[DatabaseBinding]) -> dict[int, dict[str, Any]]

Update note: created this ExecPlan to satisfy the repository requirement for multi-module feature work before implementation begins.
