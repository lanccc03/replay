from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Optional

from replay_platform.adapters.base import DeviceAdapter
from replay_platform.core import AdapterHealth


class AdapterHealthCache:
    def __init__(self, adapters: Callable[[], Dict[str, DeviceAdapter]]) -> None:
        self._adapters = adapters
        self._lock = threading.Lock()
        self._last_refresh_ns = 0
        self._cached: Dict[str, AdapterHealth] = {}

    def reset(self) -> None:
        with self._lock:
            self._cached = {}
            self._last_refresh_ns = 0

    def snapshot(
        self,
        *,
        force: bool = False,
        now_ns: Optional[int] = None,
        refresh_interval_ns: int,
    ) -> Dict[str, AdapterHealth]:
        if now_ns is None:
            now_ns = time.perf_counter_ns()
        with self._lock:
            should_refresh = force or not self._cached
            if not should_refresh:
                should_refresh = refresh_interval_ns <= 0 or now_ns - self._last_refresh_ns >= refresh_interval_ns
            if should_refresh:
                self._cached = self.copy_map(self.safe_snapshot(self._adapters()))
                self._last_refresh_ns = now_ns
            return self.copy_map(self._cached)

    @staticmethod
    def safe_snapshot(adapters: Dict[str, DeviceAdapter]) -> Dict[str, AdapterHealth]:
        health_map: Dict[str, AdapterHealth] = {}
        for adapter_id, adapter in adapters.items():
            try:
                health = adapter.health()
            except Exception as exc:  # pragma: no cover - defensive snapshot collection
                health = AdapterHealth(online=False, detail=f"健康检查失败：{exc}")
            health_map[adapter_id] = health
        return health_map

    @staticmethod
    def copy_map(health_map: Dict[str, AdapterHealth]) -> Dict[str, AdapterHealth]:
        return {
            adapter_id: AdapterHealth(
                online=health.online,
                detail=health.detail,
                per_channel=dict(health.per_channel),
            )
            for adapter_id, health in health_map.items()
        }


__all__ = ("AdapterHealthCache",)
