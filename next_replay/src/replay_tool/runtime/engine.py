from __future__ import annotations

from dataclasses import replace
import threading
import time
from typing import Callable, Optional

from replay_tool.domain import Frame, ReplaySnapshot, ReplayState
from replay_tool.planning import ReplayPlan
from replay_tool.ports.device import BusDevice
from replay_tool.ports.registry import DeviceRegistry


Clock = Callable[[], int]
Sleeper = Callable[[float], None]


class ReplayRuntime:
    def __init__(
        self,
        registry: DeviceRegistry,
        *,
        clock: Clock = time.perf_counter_ns,
        sleeper: Sleeper = time.sleep,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.registry = registry
        self.clock = clock
        self.sleeper = sleeper
        self.logger = logger or (lambda _message: None)
        self._plan: Optional[ReplayPlan] = None
        self._devices: dict[str, BusDevice] = {}
        self._thread: Optional[threading.Thread] = None
        self._condition = threading.Condition()
        self._stop_requested = False
        self._pause_started_ns = 0
        self._base_perf_ns = 0
        self._timeline_index = 0
        self._snapshot = ReplaySnapshot()
        self._errors: list[str] = []
        self._sent_frames = 0
        self._skipped_frames = 0
        self._completed_loops = 0

    def configure(self, plan: ReplayPlan) -> None:
        if self._snapshot.state != ReplayState.STOPPED:
            raise RuntimeError("Runtime must be stopped before configure().")
        self._plan = plan
        self._devices = {}
        self._timeline_index = 0
        self._errors = []
        self._sent_frames = 0
        self._skipped_frames = 0
        self._completed_loops = 0
        self._snapshot = ReplaySnapshot(total_ts_ns=plan.total_ts_ns)

    def start(self) -> None:
        if self._plan is None:
            raise RuntimeError("Runtime is not configured.")
        if self._snapshot.state == ReplayState.RUNNING:
            return
        self._open_and_start_devices()
        with self._condition:
            self._stop_requested = False
            self._base_perf_ns = self.clock()
            self._snapshot = replace(self._snapshot, state=ReplayState.RUNNING)
        self.logger("Replay started.")
        self._thread = threading.Thread(target=self._run_loop, name="next-replay-runtime", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        with self._condition:
            if self._snapshot.state != ReplayState.RUNNING:
                return
            self._pause_started_ns = self.clock()
            self._snapshot = replace(self._snapshot, state=ReplayState.PAUSED)
            self._condition.notify_all()
        self.logger("Replay paused.")

    def resume(self) -> None:
        with self._condition:
            if self._snapshot.state != ReplayState.PAUSED:
                return
            paused_duration = self.clock() - self._pause_started_ns
            self._base_perf_ns += paused_duration
            self._pause_started_ns = 0
            self._snapshot = replace(self._snapshot, state=ReplayState.RUNNING)
            self._condition.notify_all()
        self.logger("Replay resumed.")

    def stop(self) -> None:
        with self._condition:
            self._stop_requested = True
            self._snapshot = replace(self._snapshot, state=ReplayState.STOPPED)
            self._condition.notify_all()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._close_devices()
        self.logger("Replay stopped.")

    def wait(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def snapshot(self) -> ReplaySnapshot:
        with self._condition:
            return self._snapshot

    def _run_loop(self) -> None:
        assert self._plan is not None
        try:
            while True:
                print(1)
                with self._condition:
                    if self._stop_requested:
                        return
                    while self._snapshot.state == ReplayState.PAUSED and not self._stop_requested:
                        self._condition.wait(timeout=0.05)
                    if self._stop_requested:
                        return
                if self._timeline_index >= len(self._plan.frames):
                    print(len(self._plan.frames))
                    if not self._plan.loop or not self._plan.frames:
                        self._finish()
                        return
                    self._restart_loop()
                    continue
                frame = self._plan.frames[self._timeline_index]
                target_ns = self._base_perf_ns + frame.ts_ns
                if self.clock() < target_ns:
                    self._sleep_until(target_ns)
                    continue
                print(2)
                self._dispatch_frame(frame)
                self._timeline_index += 1
        except Exception as exc:  # pragma: no cover - defensive runtime reporting
            self._errors.append(str(exc))
            self.logger(f"Replay error: {exc}")
            self._finish()

    def _sleep_until(self, target_ns: int) -> None:
        while True:
            with self._condition:
                if self._stop_requested or self._snapshot.state == ReplayState.PAUSED:
                    return
            now = self.clock()
            if now >= target_ns:
                return
            self.sleeper(min((target_ns - now) / 1_000_000_000, 0.002))

    def _dispatch_frame(self, frame: Frame) -> None:
        assert self._plan is not None
        channel = self._plan.channel_for_logical(frame.channel)
        device = self._devices[channel.device_id]
        physical = frame.clone(channel=channel.physical_channel)
        sent = int(device.send([physical]) or 0)
        if sent > 0:
            self._sent_frames += 1
        else:
            self._skipped_frames += 1
        with self._condition:
            self._snapshot = replace(
                self._snapshot,
                current_ts_ns=frame.ts_ns,
                sent_frames=self._sent_frames,
                skipped_frames=self._skipped_frames,
                errors=tuple(self._errors),
                completed_loops=self._completed_loops,
            )

    def _open_and_start_devices(self) -> None:
        assert self._plan is not None
        if self._devices:
            return
        for config in self._plan.devices:
            device = self.registry.create(config)
            device.open()
            self._devices[config.id] = device
        for channel in self._plan.channels:
            self._devices[channel.device_id].start_channel(channel.physical_channel, channel.binding.config)

    def _close_devices(self) -> None:
        for device in self._devices.values():
            try:
                device.close()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                self._errors.append(str(exc))
        self._devices.clear()
        with self._condition:
            self._snapshot = replace(self._snapshot, errors=tuple(self._errors))

    def _finish(self) -> None:
        with self._condition:
            self._snapshot = replace(
                self._snapshot,
                state=ReplayState.STOPPED,
                sent_frames=self._sent_frames,
                skipped_frames=self._skipped_frames,
                errors=tuple(self._errors),
                completed_loops=self._completed_loops,
            )
            self._condition.notify_all()
        self._close_devices()
        self.logger("Replay completed.")

    def _restart_loop(self) -> None:
        self._completed_loops += 1
        self._timeline_index = 0
        self._base_perf_ns = self.clock()
        with self._condition:
            self._snapshot = replace(
                self._snapshot,
                current_ts_ns=0,
                completed_loops=self._completed_loops,
            )
