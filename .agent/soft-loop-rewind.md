# Soft Loop Rewind For ReplayEngine

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document must be maintained in accordance with `.agent/PLANS.md`.

## Purpose / Big Picture

Users report that loop playback pays a visible cost between consecutive rounds. Today the replay engine treats every loop boundary as a mini restart: it closes adapters, reopens channels, and re-sends startup sync frames before replaying from the beginning. After this change, a continuous loop session will rewind the runtime cursor to the beginning of the timeline and re-anchor the runtime clock without doing adapter teardown. The observable result is that the next loop begins sooner, but loop boundaries will now inherit the previous round's link state until a time-axis reconnect or a manual stop/start resets it.

The change is fully contained inside the runtime engine, tests, and operator-facing documentation. Scenario JSON, UI forms, and public dataclasses stay unchanged.

## Progress

- [x] (2026-04-16 11:17 +08:00) Inspected `src/replay_platform/runtime/engine.py`, `tests/test_engine.py`, `src/replay_platform/adapters/mock.py`, `src/replay_platform/adapters/zlg.py`, `src/replay_platform/adapters/tongxing.py`, and `docs/replay_playback_analysis.html` to confirm the existing loop boundary behavior and affected tests/docs.
- [x] (2026-04-16 11:23 +08:00) Implemented soft loop rewind in `src/replay_platform/runtime/engine.py` by removing adapter teardown/channel preparation from `_restart_loop_playback()` and resetting only cursor/timebase/runtime snapshot state.
- [x] (2026-04-16 11:24 +08:00) Updated loop-related tests in `tests/test_engine.py`, including the disconnect-across-loop assertion flip, the "startup sync only once per continuous loop session" regression, and a manual stop/start regression that still expects channel reinitialization and startup sync.
- [x] (2026-04-16 11:24 +08:00) Updated `docs/replay_playback_analysis.html` to describe soft loop rewind and the inherited-link-state tradeoff.
- [x] (2026-04-16 11:25 +08:00) Ran `python -m unittest discover -s tests -v`; all tests passed (`Ran 151 tests in 2.066s`, `OK`, `skipped=4` for PySide6 UI dialog coverage).

## Surprises & Discoveries

- Observation: current loop playback behavior is intentionally documented as "not just resetting the index".
  Evidence: `docs/replay_playback_analysis.html` explicitly states that loop playback tears down adapters and prepares channels again, and `tests/test_engine.py` contains assertions for repeated `open()` and repeated startup sync.

- Observation: a pure cursor rewind is behaviorally significant, not just a performance optimization.
  Evidence: `MockDeviceAdapter.close()` clears started channels, `stop_channel()` removes per-channel health state, and the loop disconnect test currently relies on another `open()/start_channel()` cycle to restore the channel in the next round.

## Decision Log

- Decision: implement the user's requested "pure rewind" behavior for loop boundaries instead of a lighter reconnect/start-channel refresh.
  Rationale: the user explicitly chose the pure rewind strategy and accepted the semantic tradeoff that loop playback will no longer restore channel state or replay startup sync on every round.
  Date/Author: 2026-04-16 / Codex

- Decision: keep first-start behavior unchanged and prove that only loop boundaries changed by adding a stop/start regression test.
  Rationale: this isolates the optimization to the reported cost center while preserving existing semantics for a fresh replay session.
  Date/Author: 2026-04-16 / Codex

## Outcomes & Retrospective

The implementation now matches the requested "pure rewind" loop behavior. Continuous loop playback no longer reopens adapters or channels and no longer re-sends startup sync on every round; instead it rewinds `_timeline_index`, re-anchors `_base_perf_ns`, and keeps the existing adapter/channel state intact. The main tradeoff is now explicit in both code and docs: a disconnect at the end of one loop remains in effect at the start of the next loop until a time-axis reconnect or a manual stop/start resets it.

The automated verification target was met. `python -m unittest discover -s tests -v` passed after the change, including the updated loop-behavior tests and the new stop/start regression. Qt manual click-through validation and Windows hardware validation were not performed in this environment.

## Context and Orientation

`src/replay_platform/runtime/engine.py` is the core replay scheduler. It prepares adapters, builds a single time axis from frames, diagnostics, and link actions, and advances through that axis using `_timeline_index`. When loop playback reaches the end, `_handle_timeline_exhausted()` currently calls `_restart_loop_playback()`, which closes adapters, reopens channels, resets the cursor, arms a fresh start anchor, and marks startup sync as pending again.

`tests/test_engine.py` is the authoritative runtime regression suite. It already covers loop progress, pause/resume in loop mode, frame-enable persistence across loop boundaries, loop behavior with empty timelines, startup sync on first start, and startup sync repeating across loop rounds. Several of those tests will need their expectations updated because the behavior is changing deliberately.

`docs/replay_playback_analysis.html` is the operator/developer-facing explanation of the current replay runtime. It currently says that loop playback reinitializes channels and is not merely resetting the cursor. That text must be updated so future maintainers do not "fix" the new behavior back to the old one.

For this task, "soft loop rewind" means only rewinding runtime state inside the already-running replay session. It does not mean a new public API, a new scenario field, or a new UI option.

## Plan of Work

First, update `src/replay_platform/runtime/engine.py`. Replace the current loop restart sequence so `_restart_loop_playback()` no longer tears down adapters or prepares channels. The function must increment `_completed_loops`, set `_timeline_index = 0`, set `_base_perf_ns = time.perf_counter_ns()`, clear any pending start-anchor state, ensure `_startup_sync_pending` is false, reset `_pause_started_ns`, clear `_frame_log_counts`, and refresh the runtime snapshot with `current_ts_ns = 0`, `timeline_index = 0`, empty current item/source fields, preserved running state, and the new `completed_loops` value. Keep the log line that announces the next round.

Second, update `tests/test_engine.py`. Preserve the tests that verify loop progress, frame-enable persistence, pause/resume/stop in loop mode, and empty-timeline handling. Rewrite the disconnect-across-loop test so it proves the opposite of the old behavior: after a disconnect and a soft rewind, the next loop does not automatically reopen the channel. Rewrite the startup sync loop test so it proves only the first start sends a sync frame even if loop playback keeps running. Add a new regression test that stops and starts the same configured engine again, proving that a fresh session still opens channels again and sends startup sync again.

Third, update `docs/replay_playback_analysis.html` so the loop section says the engine now rewinds runtime state without reinitializing adapters on loop boundaries, and add a warning that loop playback inherits the previous round's link state until the timeline itself reconnects or the user performs a fresh stop/start.

Finally, run the full `unittest` suite from the repository root and record the outcome here.

## Concrete Steps

From `C:\code\replay`:

1. Edit `.agent/soft-loop-rewind.md`, `src/replay_platform/runtime/engine.py`, `tests/test_engine.py`, and `docs/replay_playback_analysis.html`.
2. Run:

       python -m unittest discover -s tests -v

3. Review failures, adjust code or tests, and rerun the same command until it passes.

Expected successful signal:

    ...
    OK

## Validation and Acceptance

Acceptance is reached when all of the following are true:

1. In code, loop playback no longer calls adapter teardown or channel preparation when it wraps to the next round.
2. `tests/test_engine.py` proves that loop playback still advances to the next round and accumulates stats, but no longer auto-recovers channels after a loop-ending disconnect and no longer repeats startup sync every round.
3. A dedicated regression test proves that a manual stop followed by a new start still reinitializes channels and sends startup sync again.
4. `python -m unittest discover -s tests -v` passes from `C:\code\replay`.
5. The replay analysis document describes the new loop semantics and the inherited-link-state tradeoff.

## Idempotence and Recovery

All code and documentation edits are ordinary source changes and can be safely reapplied or refined. The validation command is safe to rerun. If a test fails mid-implementation, fix the relevant expectation or runtime logic and rerun the same test command; no migration or cleanup step is required beyond ignoring generated cache artifacts.

## Artifacts and Notes

Important code locations:

- `src/replay_platform/runtime/engine.py::_handle_timeline_exhausted`
- `src/replay_platform/runtime/engine.py::_restart_loop_playback`
- `tests/test_engine.py::test_loop_playback_reinitializes_channels_after_link_disconnect`
- `tests/test_engine.py::test_loop_playback_restarts_startup_sync_for_each_loop`
- `docs/replay_playback_analysis.html` loop behavior section

## Interfaces and Dependencies

No new interfaces are introduced. The implementation continues using the existing `ReplayEngine`, `DeviceAdapter`, `ReplayRuntimeSnapshot`, and `FrameEnableService` types. The only semantic change is how `ReplayEngine` rewinds its internal runtime state at loop boundaries.

Revision note: updated this ExecPlan on 2026-04-16 after implementation and test completion to record the final behavior, verification results, and accepted tradeoffs.
