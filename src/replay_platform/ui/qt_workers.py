from __future__ import annotations

from typing import Any, Callable

from replay_platform.ui.qt_imports import QObject, Signal, Slot

class BackgroundTask(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, task: Callable[[], Any]) -> None:
        super().__init__()
        self._task = task

    @Slot()
    def run(self) -> None:
        try:
            result = self._task()
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            self.failed.emit(message)
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()
