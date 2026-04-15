# Large Trace UI/Replayer Performance Plan

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

Large trace files currently make three common user actions feel frozen: importing a trace, opening the scenario editor for a scenario that references that trace, and clicking start replay. After this plan is implemented, those actions should stay responsive on the desktop UI, show visible progress when heavy work is unavoidable, and avoid re-decoding the same trace data multiple times just to populate helper widgets.

The observable goal is simple. A user can import a large ASC or BLF trace, open the scenario editor, and start replay without the main window hanging for seconds. Import should show progress instead of appearing dead. Opening the editor should rely on stored metadata instead of full trace reloads. Starting replay should perform heavy preparation off the UI thread and reuse cached summaries whenever possible.

## Progress

- [x] (2026-04-15 13:53 +08:00) Read required repository context: `README.md`, `docs/architecture.md`, `docs/scenario-and-trace.md`, `docs/testing.md`, `src/replay_platform/core.py`, `src/replay_platform/app_controller.py`, `src/replay_platform/runtime/engine.py`, and `src/replay_platform/ui/main_window.py`.
- [x] (2026-04-15 13:53 +08:00) Traced the three affected code paths and identified the current synchronous hotspots in `src/replay_platform/services/library.py`, `src/replay_platform/services/trace_loader.py`, `src/replay_platform/app_controller.py`, and `src/replay_platform/ui/main_window.py`.
- [x] (2026-04-15 13:53 +08:00) Ran a synthetic local probe with 200,000 CAN FD frames to estimate repeated binary-cache load cost and memory growth.
- [x] (2026-04-15 13:53 +08:00) Wrote this initial optimization plan with phased implementation order, validation targets, and risk boundaries.
- [x] (2026-04-15 14:12 +08:00) Implemented Milestone 1: trace import now persists `message_id_summaries`, the main window frame-enable helper path consumes stored summaries instead of `load_trace_events()`, and the scenario editor caches trace metadata lookups during a dialog session.
- [x] (2026-04-15 14:57 +08:00) Implemented Milestone 2 core UI responsiveness work: trace import now runs on a background Qt worker, replay frame preparation now runs off the UI thread via `ReplayApplication.prepare_replay()`, and the main window shows explicit busy-state feedback while preparation is in progress.
- [x] (2026-04-15 16:16 +08:00) Finished the remaining Milestone 2 follow-up: ASC import now parses line-by-line instead of `read_text().splitlines()`, while still preserving time ordering for out-of-order files.
- [x] (2026-04-15 16:16 +08:00) Implemented the first Milestone 3 preparation optimization: binary cache reads can now be filtered by `(source_channel, bus_type)`, and replay startup requests only the mapped sources it actually needs for trace-bound channels.
- [x] (2026-04-15 16:16 +08:00) Re-ran `python -m compileall src tests` and `python -m unittest discover -s tests -v`; the full suite passed with 4 skipped Qt dialog tests because PySide6 is not installed in this environment.
- [x] (2026-04-15 16:40 +08:00) Completed the remaining Milestone 3 work: replay preparation now merges already-sorted per-trace sequences instead of `extend + sort`, repeated starts reuse a bounded in-memory prepared-trace cache keyed by trace/mapping signature, and runtime adapter health snapshots are refreshed on a coarse interval instead of per timeline item.
- [x] (2026-04-15 16:40 +08:00) Re-ran `python -m compileall src tests` and `python -m unittest discover -s tests -v`; the full suite passed with 150 tests total and 4 skipped Qt dialog tests because PySide6 is not installed in this environment.

## Surprises & Discoveries

- Observation: importing a trace immediately triggers another full trace load on the main window refresh path, even though import already parsed the entire file and wrote the binary cache.
  Evidence: `src/replay_platform/services/library.py:81-105` parses and caches the trace during `import_trace()`, then `src/replay_platform/ui/main_window.py:3876-3880` calls `_refresh_frame_enable_candidates(force=True)`, and `src/replay_platform/ui/main_window.py:3797` loads the full events again through `load_trace_events()`.

- Observation: opening or reselecting a scenario also reloads full trace events before the editor is shown, even though the editor itself mostly needs metadata such as selected trace names and source-channel summaries.
  Evidence: `src/replay_platform/ui/main_window.py:3717-3724` calls `_refresh_frame_enable_candidates()` from `_set_current_scenario_payload()`, and `_edit_current_scenario()` at `src/replay_platform/ui/main_window.py:4125-4130` invokes `_set_current_scenario_payload()` before opening the editor.

- Observation: clicking start replay currently pays for trace preparation twice in the same UI action: once for frame-enable candidate extraction in `_set_current_scenario_payload()`, and once again in `ReplayApplication.start_replay()`.
  Evidence: `_scenario_from_current_source()` at `src/replay_platform/ui/main_window.py:4179-4191` ends with `_set_current_scenario_payload(...)`; `ReplayApplication.start_replay()` at `src/replay_platform/app_controller.py:198-218` then calls `_load_replay_frames()`, which calls `load_trace_events()` at `src/replay_platform/app_controller.py:262-286`.

- Observation: ASC import is especially memory-unfriendly because the loader reads the whole text file into a single string and splits every line before parsing.
  Evidence: `src/replay_platform/services/trace_loader.py:211` uses `path.read_text(...).splitlines()`, and `src/replay_platform/services/trace_loader.py:230` sorts the full in-memory event list afterward.

- Observation: the current frame-enable helper path loads entire traces only to derive unique message IDs per channel; that is helper UI data, not replay-essential data.
  Evidence: `src/replay_platform/ui/main_window.py:3789-3806` calls `load_trace_events()` for every effective trace, then only builds `set(event.message_id)` grouped by `event.channel`.

- Observation: the replay start path clones and re-sorts large event sets even when the source caches are already time-ordered.
  Evidence: `src/replay_platform/app_controller.py:277-286` loads every trace into memory, clones source-file labels, optionally clones mapped frames again, then calls `frames.sort(...)` across the combined list.

- Observation: replay runtime performs adapter health snapshot work on every snapshot update, which is not the root cause of the UI freeze but can become a throughput tax for large timelines.
  Evidence: `src/replay_platform/runtime/engine.py:850-878` refreshes adapter health inside `_update_runtime_snapshot_for_item()`, which is called in the main replay loop at `src/replay_platform/runtime/engine.py:324-327`.

- Observation: the repository does not currently contain a large real trace sample; only small imported traces were present locally.
  Evidence: querying `.replay_platform/library.sqlite3` on 2026-04-15 showed two traces with 76 and 68 frames respectively, so the local performance probe had to use synthetic data instead of a checked-in large corpus.

- Observation: the synthetic probe already shows that even binary-cache reloads are not cheap enough to repeat in several UI-triggered code paths.
  Evidence: on 2026-04-15, a local synthetic run with 200,000 CAN FD frames produced approximately `load_binary_cache() = 0.618 s`, `message-id scan = 0.039 s`, `mapped clone = 0.090 s`, and `tracemalloc peak = 83.14 MB`. This is not a user trace, but it confirms near-linear repeated cost.

- Observation: the old frame-enable helper path was not only expensive, it also keyed candidate message IDs by trace source channel even when the scenario remapped that source to a different logical channel.
  Evidence: the pre-change implementation in `src/replay_platform/ui/main_window.py:3789-3806` grouped `event.message_id` by `event.channel`; after the refactor, `tests/test_ui_helpers.py` now covers both unmapped and file-mapped candidate generation.

- Observation: moving replay preparation to a worker is much safer if the worker only performs trace-frame loading, while engine configuration, adapter creation, and runtime service mutation stay on the main thread.
  Evidence: `ReplayEngine`, `SignalOverrideService`, and `FrameEnableService` all mutate in-memory state without a shared lock, so the implemented split now uses `ReplayApplication.prepare_replay()` in the worker and `ReplayApplication.start_prepared_replay()` back on the UI thread.

- Observation: local sandboxed `unittest` runs can fail for `test_app_controller.py` even when the code is correct, because temporary workspace creation under `%LOCALAPPDATA%\\Temp` is permission-restricted in this environment.
  Evidence: on 2026-04-15, `python -m unittest tests.test_app_controller -v` failed inside the sandbox with `PermissionError` while creating `.replay_platform` under a temporary directory, but the same suite passed via the already-approved elevated `python -m unittest discover -s tests -v` command.

- Observation: switching ASC parsing from `read_text().splitlines()` to line-by-line iteration removes one full-text copy of the file without changing parse semantics, and most ASC traces are already time-ordered so the sort can often be skipped entirely.
  Evidence: `TraceLoader.iter_asc()` now streams file lines directly, `tests/test_trace_loader.py` asserts that `Path.read_text()` is no longer used for ASC loading, and `_load_asc()` only sorts when it detects a timestamp regression.

- Observation: the replay start path does not need every frame from a mapped multi-channel trace if only one source channel is bound into the scenario.
  Evidence: `FileLibraryService.load_trace_events(..., source_filters=...)` now forwards source filters into streamed binary-cache reads, and `tests/test_app_controller.py` plus `tests/test_library.py` now verify that replay preparation requests only the mapped `(source_channel, bus_type)` pairs it actually needs.

- Observation: once each trace-specific input stream is already time-ordered, replay assembly can preserve behavior with merge-based composition and avoid a final full-list sort.
  Evidence: `ReplayApplication._load_replay_frames()` now builds sorted per-trace replay sequences, merges mapped bindings from the same trace, and then uses a merge step across traces; `tests/test_app_controller.py` now covers both interleaved multi-trace order and multiple mapped bindings from the same trace.

- Observation: caching replay-ready per-trace sequences is more practical than caching full-scenario frame lists, because the expensive work is dominated by per-trace decode/remap and different scenarios can still share those results safely.
  Evidence: the new bounded cache in `src/replay_platform/app_controller.py` keys entries by `trace_id`, source label, requested source filters, and mapping signature, while `tests/test_app_controller.py` verifies repeated calls reuse the cached prepared sequence without reloading the trace.

- Observation: runtime snapshot cost can be reduced without losing meaningful status updates if adapter health is forced on state transitions and throttled only during per-item progress updates.
  Evidence: `ReplayEngine` now refreshes cached adapter health during configure/start/pause/resume/stop/finalize/loop-restart and reuses that snapshot between timeline items, while `tests/test_engine.py` verifies repeated frame progress does not call `adapter.health()` for every item.

## Decision Log

- Decision: prioritize eliminating eager UI-thread full trace reloads before deeper replay-engine changes.
  Rationale: the reported freezes happen during import, opening the scenario editor, and clicking start replay. The code-path evidence shows those freezes are dominated by synchronous UI-side preparation, so removing repeated helper-data reloads produces the largest immediate improvement with the lowest regression risk.
  Date/Author: 2026-04-15 / Codex

- Decision: store and consume richer trace summaries at import time instead of rebuilding helper information from full events during normal UI interaction.
  Rationale: the frame-enable UI and binding-source selectors need compact metadata, not full `FrameEvent` lists. Persisting summaries once avoids repeated binary-cache decoding.
  Date/Author: 2026-04-15 / Codex

- Decision: make replay preparation asynchronous even after UI helper paths are fixed.
  Rationale: starting replay for a genuinely large trace still requires I/O, filtering, cloning, and timeline assembly. Keeping that work off the UI thread is necessary even if the total wall-clock time does not drop to zero.
  Date/Author: 2026-04-15 / Codex

- Decision: split replay startup into a worker-safe preparation phase and a main-thread start phase, instead of calling `ReplayApplication.start_replay()` wholesale from the worker thread.
  Rationale: the dominant large-trace cost is frame loading, while adapter construction and runtime-service mutation touch shared in-memory objects. `ReplayApplication.prepare_replay()` / `start_prepared_replay()` keep the expensive file work off the UI thread without introducing cross-thread mutation risk into engine state, override catalogs, or frame-enable rules.
  Date/Author: 2026-04-15 / Codex

- Decision: put source filtering at the trace-library boundary, not only inside replay mapping loops.
  Rationale: the real cost is paid when decoding binary caches and materializing frame objects. Exposing `source_filters` from `FileLibraryService.load_trace_events()` lets replay preparation avoid building irrelevant `FrameEvent` objects in the first place while still preserving the old fallback path for legacy caches and uncached traces.
  Date/Author: 2026-04-15 / Codex

- Decision: cache prepared replay sequences per trace-and-mapping signature rather than only raw trace loads or whole-scenario frame lists.
  Rationale: the main repeated-start cost is per-trace decode plus logical-channel remap. A per-trace cache stays bounded, can be reused across scenarios that share the same imported trace and mapping shape, and avoids coupling cache invalidation to scenario JSON changes.
  Date/Author: 2026-04-15 / Codex

- Decision: replace the replay-frame `extend + sort` path with merge-based composition only after each contributing sequence is known to remain time-ordered.
  Rationale: mapped bindings from the same trace and different trace files are both already sorted individually. Merging those streams preserves timeline order while removing one large combined sort from replay preparation.
  Date/Author: 2026-04-15 / Codex

- Decision: throttle adapter health collection only inside timeline-progress snapshot updates, while forcing immediate refresh on lifecycle boundaries.
  Rationale: state transitions such as start, pause, resume, stop, finalization, and loop restart are exactly when the UI most needs a fresh health view. Between those points, refreshing health for every timeline item is needless overhead on large traces.
  Date/Author: 2026-04-15 / Codex

- Decision: defer any change to `ScenarioSpec.from_dict()` / `to_dict()` data compatibility unless a later milestone proves it is necessary.
  Rationale: repository rules require scenario JSON compatibility. The current optimization scope can be achieved with service-layer summaries, preparation workers, and caches without changing the scenario contract.
  Date/Author: 2026-04-15 / Codex

- Decision: generate frame-enable candidates from persisted per-source summaries plus current binding metadata, instead of from full event lists.
  Rationale: this removes UI-thread full trace reloads and also corrects the helper UI so file-mapped traces expose candidate IDs under the mapped logical channel rather than the original source channel.
  Date/Author: 2026-04-15 / Codex

## Outcomes & Retrospective

As of 2026-04-15 16:40 +08:00, Milestone 1, Milestone 2, and Milestone 3 are all complete in code and automated tests. The repository now persists per-channel message-ID summaries at import time, lazily backfills them for older library entries, uses those summaries in the main window instead of decoding full trace caches for helper UI data, imports ASC traces line-by-line instead of loading the full text into memory first, prepares replay frames on a background worker before starting the engine on the UI thread, merges already-sorted replay inputs instead of re-sorting a monolithic combined list, reuses a bounded in-memory prepared-trace cache for repeated starts, and rate-limits adapter health polling during replay progress updates.

The main user-facing wins now cover all three originally reported freeze points. Import no longer blocks the UI and avoids an unnecessary full-text ASC copy. Opening the scenario editor and related helper UI no longer performs extra full-trace reloads just to build candidate data. Starting replay now stays off the UI thread, reads only the mapped trace slices it needs, and avoids repeating the same decode/remap work for identical repeated starts in the same session. The remaining work after this plan is outside core implementation: Qt manual click-through validation on large traces and Windows ZLG / 同星 hardware verification.

## Context and Orientation

The desktop application is split across a UI layer, an application-service layer, runtime replay code, and device/diagnostic adapters. The user-visible freezes all start in the UI layer but are caused by lower-layer trace preparation being invoked synchronously.

`src/replay_platform/services/trace_loader.py` is the trace parser and binary-cache reader. It converts ASC or BLF files into `FrameEvent` objects, which are defined in `src/replay_platform/core.py`. `src/replay_platform/services/library.py` owns the local trace library under `.replay_platform/`, including imported raw files, binary caches, and SQLite metadata. `src/replay_platform/app_controller.py` is the application orchestrator that the UI calls. `src/replay_platform/ui/main_window.py` contains both the main window and the scenario editor dialog. `src/replay_platform/runtime/engine.py` is the replay engine that consumes prepared frame timelines.

In this repository, a “trace summary” means lightweight information derived from a trace without keeping every `FrameEvent` object alive. Today the trace library already stores per-source-channel counts in `metadata["source_summaries"]`. The plan below extends that idea to the rest of the helper data that the UI needs. A “replay preparation” step means the expensive work that turns selected trace files plus scenario mappings into the final list of `FrameEvent` objects that the replay engine will schedule.

The current high-cost paths are these:

1. Import path:
   `MainWindow._import_trace()` calls `ReplayApplication.import_trace()`, which parses and caches the whole trace in `FileLibraryService.import_trace()`. Immediately afterward the main window refresh path calls `_refresh_frame_enable_candidates(force=True)`, which loads the trace back from disk again just to derive message IDs.

2. Scenario/editor path:
   `_set_current_scenario_payload()` always calls `_refresh_frame_enable_candidates()`. That means selecting a scenario or opening the scenario editor can trigger full trace loads even before the editor is shown.

3. Replay start path:
   `_scenario_from_current_source()` calls `_set_current_scenario_payload()` first, then `ReplayApplication.start_replay()` loads the traces again, remaps channels, clones source-file strings, and sorts the combined frame list.

## Plan of Work

Milestone 1 removes the most wasteful helper-data reloads. Extend the metadata written by `src/replay_platform/services/library.py::import_trace()` so each imported trace stores a compact, normalized summary of unique message IDs per source channel and bus type. Keep the stored format JSON-friendly and small; the UI only needs channel identity, bus type, frame count, and message IDs. Add read methods in `FileLibraryService` that return these summaries without ever calling `load_trace_events()`. Then update `src/replay_platform/ui/main_window.py::_refresh_frame_enable_candidates()` to build candidate IDs from the stored summary data. Remove eager calls that refresh those candidates from `_set_current_scenario_payload()` and `_refresh_traces()` unless the frame-enable panel is actually visible or a caller explicitly requests it. The scenario editor should continue using `get_trace_source_summaries()` for mapping dropdowns, but it should memoize `list_traces()` and summary lookups within a single dialog session to avoid repeated SQLite round trips during live validation.

Milestone 2 keeps unavoidable heavy work off the UI thread. Introduce a dedicated worker object in the UI layer for import and another for replay preparation. Import should move `ReplayApplication.import_trace()` off the main thread and report progress text back to the window. For ASC files, add a streaming parse path in `TraceLoader` so the import worker can iterate file lines instead of calling `read_text().splitlines()`. The import worker should write the binary cache and metadata summaries incrementally, then notify the UI when the new trace record is ready. Replay start should similarly build the frame set in the background, then configure and start `ReplayEngine` only after the worker has produced the prepared frame list.

Milestone 3 reduces the absolute preparation cost for large scenarios. Add a filtered binary-cache load path in `src/replay_platform/services/library.py` and `src/replay_platform/services/trace_loader.py` so mapped scenarios can request only the source channels and bus types they actually use. This avoids loading an entire multi-channel trace when the scenario only replays one channel from it. Replace the current “load all traces, extend a list, then sort” flow in `ReplayApplication._load_replay_frames()` with a merge-based approach whenever the input traces are already time-ordered. If repeated starts of the same scenario are common, add a bounded in-memory LRU cache for prepared trace event lists keyed by trace ID plus cache-file modification time. Finally, trim replay runtime overhead by rate-limiting adapter health snapshots in `ReplayEngine` so the status UI does not do per-item health polling.

## Concrete Steps

All commands below are run from `C:\code\replay`.

1. Implement metadata-based helper summaries.

   - Edit `src/replay_platform/services/library.py` so `import_trace()` stores a new metadata field for per-channel unique message IDs.
   - Edit `src/replay_platform/services/trace_loader.py` to expose helper functions that build those summaries from an event iterator or event sequence.
   - Edit `src/replay_platform/ui/main_window.py` so `_refresh_frame_enable_candidates()` uses metadata summaries instead of `load_trace_events()`.
   - Update `tests/test_library.py`, `tests/test_ui_helpers.py`, and `tests/test_ui_dialog.py` to cover the new summary path and the absence of forced trace loads during editor open.

2. Move import and replay preparation off the main thread.

   - Add Qt worker objects or threads in `src/replay_platform/ui/main_window.py` for trace import and replay preparation.
   - Add streaming import support in `src/replay_platform/services/trace_loader.py`.
   - Ensure the start button transitions through a visible “preparing replay” state instead of freezing the window.
   - Add tests for worker orchestration where practical, and keep business logic out of UI slots by introducing small helper functions as needed.

3. Add filtered replay preparation and bounded caches.

   - Extend `src/replay_platform/services/library.py` with filtered load methods.
   - Refactor `src/replay_platform/app_controller.py::_load_replay_frames()` to request only needed channels for mapped traces and to merge pre-sorted trace streams.
   - Add targeted unit tests in `tests/test_app_controller.py` and `tests/test_library.py` for filtered loading, mapping correctness, and repeated-start reuse behavior.

4. Reduce runtime overhead after replay begins.

   - Edit `src/replay_platform/runtime/engine.py` so adapter health snapshots are refreshed on a timer or coarse interval instead of every item.
   - Verify that `pause / resume`, loop playback, and disconnect / reconnect behavior remain correct.

## Validation and Acceptance

Acceptance is user-visible and should be checked in this order.

First, import a large ASC or BLF trace from the desktop UI. The window must remain responsive. The user should see a progress or busy indicator, and after import completes the trace should appear in the list without an immediate second full-trace stall.

Second, select a scenario that references a large trace and open the scenario editor. The editor should appear promptly. Binding-source dropdowns should still show the correct source-channel summaries, but opening the editor should not force a full `load_trace_events()` call just to populate helper widgets.

Third, click start replay for a scenario backed by a large trace. The UI should remain responsive while preparation happens in the background, and the replay should start after preparation finishes. Starting the same scenario again in the same session should be faster if the bounded preparation cache is hit.

Run the repository validation commands required by `docs/testing.md` for runtime, parser, and UI changes:

    python -m compileall src tests
    python -m unittest discover -s tests -v

Relevant tests that must either pass unchanged or gain new coverage include:

    tests/test_library.py
    tests/test_trace_loader.py
    tests/test_app_controller.py
    tests/test_ui_helpers.py
    tests/test_ui_dialog.py
    tests/test_engine.py

The final implementation notes must explicitly state:

- what was validated automatically;
- whether Qt manual-click verification was performed;
- whether Windows ZLG / 同星 hardware validation was not performed.

## Idempotence and Recovery

The metadata-summary changes are additive. If a trace record lacks the new summary fields, the library should rebuild them lazily once and persist them back into SQLite metadata without changing the original imported file or the scenario JSON structure. This makes the migration safe for existing workspaces.

The new in-memory caches must be bounded and disposable. If a cache entry is stale or missing, the system should fall back to the existing full-load path rather than failing replay startup. Worker-thread failures must surface clear Chinese error dialogs and leave the UI in a recoverable state, with start/import buttons re-enabled.

Because scenario compatibility must be preserved, no step in this plan should require rewriting existing scenario JSON beyond normal save behavior. If a milestone introduces a bug, the previous synchronous path can remain available behind a helper function during refactor until the new tests pass.

## Artifacts and Notes

Key evidence captured during planning:

    FileLibraryService.import_trace()
      src/replay_platform/services/library.py:81-105
      Parses the trace, writes binary cache, and stores source summaries.

    MainWindow._refresh_frame_enable_candidates()
      src/replay_platform/ui/main_window.py:3789-3806
      Reloads full trace events only to compute unique message IDs.

    ReplayApplication._load_replay_frames()
      src/replay_platform/app_controller.py:262-286
      Reloads trace events, clones mapped frames, and sorts the combined list.

    TraceLoader._load_asc()
      src/replay_platform/services/trace_loader.py:211-230
      Reads the full text file into memory, parses all events, then sorts them.

Synthetic local probe from 2026-04-15:

    input: 200000 synthetic CAN FD frames
    binary cache size: 8.96 MB
    binary cache load: 0.618 s
    message-id scan for helper UI data: 0.039 s
    mapped clone path: 0.090 s
    tracemalloc peak during load: 83.14 MB

This probe is not a production trace, but it confirms that repeated binary-cache loads are too expensive to keep on several UI-triggered paths.

## Interfaces and Dependencies

The implementation should keep using the existing core types from `src/replay_platform/core.py`, especially `FrameEvent`, `TraceFileRecord`, and `ScenarioSpec`.

In `src/replay_platform/services/trace_loader.py`, define helper interfaces that can be consumed without materializing a full event list when possible. A good target shape is:

    def build_trace_source_summaries(events: Sequence[FrameEvent]) -> list[dict[str, Any]]
    def build_trace_message_id_summaries(events: Sequence[FrameEvent]) -> list[dict[str, Any]]
    def iter_asc(path: Path) -> Iterator[FrameEvent]
    def iter_binary_cache(path: Path) -> Iterator[FrameEvent]

In `src/replay_platform/services/library.py`, add read APIs that return helper summaries directly from stored metadata, plus a filtered event-load API that accepts explicit source-channel and bus-type constraints. The filtered API must preserve current behavior when no filter is supplied.

In `src/replay_platform/ui/main_window.py`, keep business decisions in small helper methods instead of embedding them directly in Qt slot bodies. The scenario editor and main window should depend on summary-returning service methods, not direct full trace loads, for helper widgets.

In `src/replay_platform/app_controller.py`, keep `start_replay()` compatible with existing callers. If replay preparation moves into a worker, expose a helper that prepares the frames and adapters without starting the engine until the UI signals readiness.

In `src/replay_platform/runtime/engine.py`, any snapshot-throttling change must preserve pause, resume, loop playback, and link-action behavior.

Change note (2026-04-15, Codex): created the initial large-trace optimization ExecPlan after tracing import, scenario-editor, and replay-start code paths and capturing a synthetic local probe to prioritize the milestones.
