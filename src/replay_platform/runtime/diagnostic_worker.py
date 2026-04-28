from __future__ import annotations

from collections import deque
import threading
import time
from typing import Callable, Deque, Dict

from replay_platform.adapters.base import DiagnosticClient
from replay_platform.core import DiagnosticAction


class DiagnosticWorker:
    def __init__(
        self,
        *,
        dispatch: Callable[[DiagnosticAction], None],
        record_error: Callable[[str], None],
        log_warning: Callable[[str], None],
    ) -> None:
        self._dispatch = dispatch
        self._record_error = record_error
        self._log_warning = log_warning
        self._thread: threading.Thread | None = None
        self._condition = threading.Condition()
        self._queue: Deque[DiagnosticAction] = deque()
        self._stop_requested = False
        self._active = False
        self._diagnostics: Dict[str, DiagnosticClient] = {}

    def configure(self, diagnostics: Dict[str, DiagnosticClient]) -> None:
        self._diagnostics = diagnostics
        with self._condition:
            self._queue.clear()
            self._stop_requested = False
            self._active = False

    def start(self) -> None:
        with self._condition:
            self._queue.clear()
            self._stop_requested = False
            self._active = False
        if not self._diagnostics:
            self._thread = None
            return
        self._thread = threading.Thread(
            target=self._loop,
            name="replay-diagnostics",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        with self._condition:
            pending = len(self._queue)
            self._queue.clear()
            self._stop_requested = True
            self._condition.notify_all()
        if pending:
            self._log_warning(f"诊断队列已取消 {pending} 条未执行动作。")
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def enqueue(self, action: DiagnosticAction) -> None:
        with self._condition:
            self._queue.append(action)
            self._condition.notify_all()

    def wait_idle(self, stop_requested: Callable[[], bool]) -> None:
        while True:
            with self._condition:
                if (not self._queue and not self._active) or stop_requested():
                    return
            time.sleep(0.01)

    def _loop(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._stop_requested:
                    self._condition.wait(timeout=0.1)
                if self._stop_requested and not self._queue:
                    return
                action = self._queue.popleft()
                self._active = True
            try:
                self._dispatch(action)
            except Exception as exc:  # pragma: no cover - defensive runtime logging
                self._record_error(str(exc))
                self._log_warning(f"诊断异常：{exc}")
            finally:
                with self._condition:
                    self._active = False
                    self._condition.notify_all()


__all__ = ("DiagnosticWorker",)
