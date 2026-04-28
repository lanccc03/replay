from __future__ import annotations

import heapq
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import List, Sequence

from replay_platform.core import BusType, DeviceChannelBinding, FrameEvent, ScenarioSpec, TraceFileRecord
from replay_platform.services.library import FileLibraryService


PREPARED_TRACE_CACHE_LIMIT = 6


@dataclass(frozen=True)
class PreparedTraceCacheKey:
    trace_id: str
    source_label: str
    source_filters: tuple[tuple[int, str], ...]
    mapped_bindings: tuple[tuple[int, int, str], ...]


class ReplayFramePreparer:
    def __init__(
        self,
        library: FileLibraryService,
        *,
        cache_limit: int = PREPARED_TRACE_CACHE_LIMIT,
    ) -> None:
        self.library = library
        self.cache_limit = max(int(cache_limit), 1)
        self._prepared_trace_cache: OrderedDict[PreparedTraceCacheKey, tuple[FrameEvent, ...]] = OrderedDict()
        self._prepared_trace_cache_lock = threading.Lock()

    def load_replay_frames(self, scenario: ScenarioSpec) -> List[FrameEvent]:
        trace_sequences: List[Sequence[FrameEvent]] = []
        trace_bound_bindings: dict[str, list[DeviceChannelBinding]] = {}
        for binding in scenario.bindings:
            if not binding.trace_file_id:
                continue
            if binding.source_channel is None or binding.source_bus_type is None:
                raise ValueError(f"逻辑通道 {binding.logical_channel} 的文件映射不完整。")
            trace_bound_bindings.setdefault(binding.trace_file_id, []).append(binding)
        missing_trace_ids = sorted(set(trace_bound_bindings) - set(scenario.trace_file_ids))
        if missing_trace_ids:
            raise ValueError(f"存在未勾选但仍被映射的文件：{', '.join(missing_trace_ids)}")

        for trace_id in scenario.trace_file_ids:
            record = self.library.get_trace_file(trace_id)
            if record is None:
                raise FileNotFoundError(trace_id)
            trace_sequences.append(
                self.prepared_trace_sequence(
                    record,
                    trace_bound_bindings.get(trace_id, []),
                )
            )
        return self.merge_sorted_frame_groups(trace_sequences)

    def prepared_trace_sequence(
        self,
        record: TraceFileRecord,
        mapped_bindings: Sequence[DeviceChannelBinding],
    ) -> Sequence[FrameEvent]:
        cache_key = self.prepared_trace_cache_key(record, mapped_bindings)
        cached = self.get_prepared_trace_cache(cache_key)
        if cached is not None:
            return cached
        source_filters = self.source_filters_for_bindings(mapped_bindings)
        trace_events = self.library.load_trace_events(record.trace_id, source_filters=source_filters)
        source_label = record.original_path or record.name
        if source_label:
            trace_events = [event.clone(source_file=source_label) for event in trace_events]
        if mapped_bindings:
            events_by_source: dict[tuple[int, BusType], list[FrameEvent]] = {}
            for event in trace_events:
                events_by_source.setdefault((event.channel, event.bus_type), []).append(event)
            mapped_sequences = [
                self.map_trace_events_for_binding(
                    events_by_source.get((int(binding.source_channel), binding.source_bus_type), []),
                    binding,
                )
                for binding in mapped_bindings
            ]
            prepared_sequence = tuple(self.merge_sorted_frame_groups(mapped_sequences))
        else:
            prepared_sequence = tuple(trace_events)
        self.store_prepared_trace_cache(cache_key, prepared_sequence)
        return prepared_sequence

    @staticmethod
    def source_filters_for_bindings(
        mapped_bindings: Sequence[DeviceChannelBinding],
    ) -> set[tuple[int, BusType]] | None:
        if not mapped_bindings:
            return None
        return {
            (int(binding.source_channel), binding.source_bus_type)
            for binding in mapped_bindings
            if binding.source_channel is not None and binding.source_bus_type is not None
        } or None

    @staticmethod
    def prepared_trace_cache_key(
        record: TraceFileRecord,
        mapped_bindings: Sequence[DeviceChannelBinding],
    ) -> PreparedTraceCacheKey:
        source_label = record.original_path or record.name
        source_filters = tuple(
            sorted(
                (int(binding.source_channel), binding.source_bus_type.value)
                for binding in mapped_bindings
                if binding.source_channel is not None and binding.source_bus_type is not None
            )
        )
        mapping_signature = tuple(
            (
                binding.logical_channel,
                int(binding.source_channel),
                binding.source_bus_type.value,
            )
            for binding in mapped_bindings
            if binding.source_channel is not None and binding.source_bus_type is not None
        )
        return PreparedTraceCacheKey(
            trace_id=record.trace_id,
            source_label=source_label,
            source_filters=source_filters,
            mapped_bindings=mapping_signature,
        )

    def get_prepared_trace_cache(
        self,
        cache_key: PreparedTraceCacheKey,
    ) -> tuple[FrameEvent, ...] | None:
        with self._prepared_trace_cache_lock:
            cached = self._prepared_trace_cache.get(cache_key)
            if cached is None:
                return None
            self._prepared_trace_cache.move_to_end(cache_key)
            return cached

    def store_prepared_trace_cache(
        self,
        cache_key: PreparedTraceCacheKey,
        frames: tuple[FrameEvent, ...],
    ) -> None:
        with self._prepared_trace_cache_lock:
            self._prepared_trace_cache[cache_key] = frames
            self._prepared_trace_cache.move_to_end(cache_key)
            while len(self._prepared_trace_cache) > self.cache_limit:
                self._prepared_trace_cache.popitem(last=False)

    def invalidate_prepared_trace_cache(self, trace_id: str) -> None:
        with self._prepared_trace_cache_lock:
            stale_keys = [key for key in self._prepared_trace_cache if key.trace_id == trace_id]
            for cache_key in stale_keys:
                self._prepared_trace_cache.pop(cache_key, None)

    @staticmethod
    def merge_sorted_frame_groups(frame_groups: Sequence[Sequence[FrameEvent]]) -> List[FrameEvent]:
        non_empty_groups = [group for group in frame_groups if group]
        if not non_empty_groups:
            return []
        if len(non_empty_groups) == 1:
            return list(non_empty_groups[0])
        return list(heapq.merge(*non_empty_groups, key=lambda item: item.ts_ns))

    @staticmethod
    def map_trace_events_for_binding(
        trace_events: Sequence[FrameEvent],
        binding: DeviceChannelBinding,
    ) -> List[FrameEvent]:
        assert binding.source_channel is not None
        assert binding.source_bus_type is not None
        mapped_events: List[FrameEvent] = []
        for event in trace_events:
            if event.channel != binding.source_channel or event.bus_type != binding.source_bus_type:
                continue
            mapped_events.append(event.clone(channel=binding.logical_channel))
        return mapped_events


__all__ = (
    "PREPARED_TRACE_CACHE_LIMIT",
    "PreparedTraceCacheKey",
    "ReplayFramePreparer",
)
