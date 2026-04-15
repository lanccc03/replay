from __future__ import annotations

from contextlib import contextmanager
import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional

from replay_platform.core import BusType, ScenarioSpec, TraceFileRecord
from replay_platform.paths import AppPaths
from replay_platform.services.trace_loader import (
    BINARY_CACHE_FORMAT,
    BINARY_CACHE_SUFFIX,
    TraceLoader,
    build_trace_message_id_summaries,
    build_trace_source_summaries,
)


@dataclass(frozen=True)
class DeleteTraceResult:
    trace_id: str
    name: str
    deleted_library_file: bool
    deleted_cache_file: bool
    referenced_by: list[str]


class FileLibraryService:
    def __init__(self, paths: AppPaths, trace_loader: Optional[TraceLoader] = None) -> None:
        self.paths = paths
        self.trace_loader = trace_loader or TraceLoader()
        self.paths.ensure()
        self._initialize_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.paths.sqlite_path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_files (
                    trace_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    original_path TEXT NOT NULL,
                    library_path TEXT NOT NULL,
                    format TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    event_count INTEGER NOT NULL DEFAULT 0,
                    start_ns INTEGER NOT NULL DEFAULT 0,
                    end_ns INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scenarios (
                    scenario_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def import_trace(self, source_path: str) -> TraceFileRecord:
        src = Path(source_path)
        trace_id = uuid.uuid4().hex
        library_name = f"{trace_id}{src.suffix.lower()}"
        dest = self.paths.trace_dir / library_name
        shutil.copy2(src, dest)
        imported_at = datetime.now(timezone.utc).isoformat()
        events = self.trace_loader.load(str(dest))
        summary = self.trace_loader.summarize(events)
        cache_path = self._binary_cache_path(trace_id)
        self.trace_loader.write_binary_cache(cache_path, events)
        record = TraceFileRecord(
            trace_id=trace_id,
            name=src.name,
            original_path=str(src),
            library_path=str(dest),
            format=src.suffix.lower().lstrip("."),
            imported_at=imported_at,
            event_count=summary.event_count,
            start_ns=summary.start_ns,
            end_ns=summary.end_ns,
            metadata={
                "cache_path": str(cache_path),
                "cache_format": BINARY_CACHE_FORMAT,
                "source_summaries": build_trace_source_summaries(events),
                "message_id_summaries": build_trace_message_id_summaries(events),
            },
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO trace_files (
                    trace_id, name, original_path, library_path, format, imported_at,
                    event_count, start_ns, end_ns, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trace_id,
                    record.name,
                    record.original_path,
                    record.library_path,
                    record.format,
                    record.imported_at,
                    record.event_count,
                    record.start_ns,
                    record.end_ns,
                    json.dumps(record.metadata),
                ),
            )
        return record

    def list_trace_files(self) -> List[TraceFileRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT trace_id, name, original_path, library_path, format, imported_at,
                       event_count, start_ns, end_ns, metadata_json
                FROM trace_files
                ORDER BY imported_at DESC
                """
            ).fetchall()
        return [
            TraceFileRecord(
                trace_id=row[0],
                name=row[1],
                original_path=row[2],
                library_path=row[3],
                format=row[4],
                imported_at=row[5],
                event_count=int(row[6]),
                start_ns=int(row[7]),
                end_ns=int(row[8]),
                metadata=json.loads(row[9]),
            )
            for row in rows
        ]

    def get_trace_file(self, trace_id: str) -> Optional[TraceFileRecord]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT trace_id, name, original_path, library_path, format, imported_at,
                       event_count, start_ns, end_ns, metadata_json
                FROM trace_files
                WHERE trace_id = ?
                """,
                (trace_id,),
            ).fetchone()
        if row is None:
            return None
        return TraceFileRecord(
            trace_id=row[0],
            name=row[1],
            original_path=row[2],
            library_path=row[3],
            format=row[4],
            imported_at=row[5],
            event_count=int(row[6]),
            start_ns=int(row[7]),
            end_ns=int(row[8]),
            metadata=json.loads(row[9]),
        )

    def find_scenarios_referencing_trace(self, trace_id: str) -> List[ScenarioSpec]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT body_json FROM scenarios ORDER BY updated_at DESC, rowid DESC"
            ).fetchall()
        scenarios: list[ScenarioSpec] = []
        for row in rows:
            scenario = ScenarioSpec.from_dict(json.loads(row[0]))
            if trace_id in scenario.trace_file_ids:
                scenarios.append(scenario)
        return scenarios

    def load_trace_events(
        self,
        trace_id: str,
        *,
        source_filters: Optional[set[tuple[int, BusType]]] = None,
    ):
        record = self.get_trace_file(trace_id)
        if record is None:
            raise FileNotFoundError(trace_id)
        normalized_filters = self._normalize_source_filters(source_filters)
        cache_path = Path(record.metadata.get("cache_path", ""))
        cache_format = str(record.metadata.get("cache_format", ""))
        if cache_path.exists():
            if cache_format == BINARY_CACHE_FORMAT or cache_path.suffix == BINARY_CACHE_SUFFIX:
                events = self.trace_loader.load_binary_cache(cache_path, source_filters=normalized_filters)
                if normalized_filters is None:
                    self._ensure_trace_summary_metadata(trace_id, record.metadata, events)
                return events
            events = self.trace_loader.load_cache(cache_path)
            self._write_binary_cache(trace_id, record.metadata, events)
            self._ensure_trace_summary_metadata(trace_id, record.metadata, events)
            return self._filter_trace_events_by_source(events, normalized_filters)
        events = self.trace_loader.load(record.library_path)
        self._write_binary_cache(trace_id, record.metadata, events)
        self._ensure_trace_summary_metadata(trace_id, record.metadata, events)
        return self._filter_trace_events_by_source(events, normalized_filters)

    def get_trace_source_summaries(self, trace_id: str) -> list[dict]:
        record = self.get_trace_file(trace_id)
        if record is None:
            raise FileNotFoundError(trace_id)
        existing = self._normalize_trace_source_summaries(record.metadata.get("source_summaries", []))
        if existing:
            return existing
        events = self.load_trace_events(trace_id)
        refreshed = self.get_trace_file(trace_id)
        if refreshed is not None:
            refreshed_existing = self._normalize_trace_source_summaries(refreshed.metadata.get("source_summaries", []))
            if refreshed_existing:
                return refreshed_existing
        summaries = build_trace_source_summaries(events)
        self._ensure_trace_summary_metadata(trace_id, record.metadata, events)
        return summaries

    def get_trace_message_id_summaries(self, trace_id: str) -> list[dict]:
        record = self.get_trace_file(trace_id)
        if record is None:
            raise FileNotFoundError(trace_id)
        existing = self._normalize_trace_message_id_summaries(record.metadata.get("message_id_summaries", []))
        if existing:
            return existing
        events = self.load_trace_events(trace_id)
        refreshed = self.get_trace_file(trace_id)
        if refreshed is not None:
            refreshed_existing = self._normalize_trace_message_id_summaries(
                refreshed.metadata.get("message_id_summaries", [])
            )
            if refreshed_existing:
                return refreshed_existing
        summaries = build_trace_message_id_summaries(events)
        self._ensure_trace_summary_metadata(trace_id, record.metadata, events)
        return summaries

    def _binary_cache_path(self, trace_id: str) -> Path:
        return self.paths.cache_dir / f"{trace_id}{BINARY_CACHE_SUFFIX}"

    @staticmethod
    def _normalize_source_filters(
        source_filters: Optional[set[tuple[int, BusType]]],
    ) -> Optional[set[tuple[int, BusType]]]:
        if not source_filters:
            return None
        normalized: set[tuple[int, BusType]] = set()
        for source_channel, bus_type in source_filters:
            normalized.add((int(source_channel), bus_type if isinstance(bus_type, BusType) else BusType(bus_type)))
        return normalized or None

    @staticmethod
    def _filter_trace_events_by_source(
        events,
        source_filters: Optional[set[tuple[int, BusType]]],
    ):
        if not source_filters:
            return list(events)
        return [
            event
            for event in events
            if (int(event.channel), event.bus_type) in source_filters
        ]

    def _write_binary_cache(
        self,
        trace_id: str,
        metadata: dict,
        events,
    ) -> None:
        cache_path = self._binary_cache_path(trace_id)
        self.trace_loader.write_binary_cache(cache_path, events)
        updated_metadata = dict(metadata)
        updated_metadata["cache_path"] = str(cache_path)
        updated_metadata["cache_format"] = BINARY_CACHE_FORMAT
        self._update_trace_metadata(trace_id, updated_metadata)

    def _ensure_trace_summary_metadata(
        self,
        trace_id: str,
        metadata: dict,
        events,
    ) -> dict[str, list[dict]]:
        source_summaries = self._normalize_trace_source_summaries(metadata.get("source_summaries", []))
        message_id_summaries = self._normalize_trace_message_id_summaries(metadata.get("message_id_summaries", []))
        if source_summaries and message_id_summaries:
            return {
                "source_summaries": source_summaries,
                "message_id_summaries": message_id_summaries,
            }
        updated_metadata = dict(metadata)
        current_record = self.get_trace_file(trace_id)
        if current_record is not None:
            updated_metadata.update(current_record.metadata)
        if not source_summaries:
            updated_metadata["source_summaries"] = build_trace_source_summaries(events)
        if not message_id_summaries:
            updated_metadata["message_id_summaries"] = build_trace_message_id_summaries(events)
        self._update_trace_metadata(trace_id, updated_metadata)
        return {
            "source_summaries": self._normalize_trace_source_summaries(updated_metadata.get("source_summaries", [])),
            "message_id_summaries": self._normalize_trace_message_id_summaries(
                updated_metadata.get("message_id_summaries", [])
            ),
        }

    @staticmethod
    def _normalize_trace_source_summaries(raw_items) -> list[dict]:
        items: list[dict] = []
        for raw_item in raw_items or []:
            if not isinstance(raw_item, dict):
                continue
            source_channel = raw_item.get("source_channel")
            bus_type = raw_item.get("bus_type")
            frame_count = raw_item.get("frame_count")
            if source_channel is None or bus_type in (None, ""):
                continue
            try:
                normalized_channel = int(source_channel)
                normalized_count = int(frame_count or 0)
            except (TypeError, ValueError):
                continue
            label = raw_item.get("label") or f"CH{normalized_channel} | {bus_type} | {normalized_count}\u5e27"
            items.append(
                {
                    "source_channel": normalized_channel,
                    "bus_type": str(bus_type),
                    "frame_count": normalized_count,
                    "label": str(label),
                }
            )
        return sorted(items, key=lambda item: (item["source_channel"], item["bus_type"]))

    @staticmethod
    def _normalize_trace_message_id_summaries(raw_items: Any) -> list[dict]:
        items: list[dict] = []
        for raw_item in raw_items or []:
            if not isinstance(raw_item, dict):
                continue
            source_channel = raw_item.get("source_channel")
            bus_type = raw_item.get("bus_type")
            frame_count = raw_item.get("frame_count")
            raw_message_ids = raw_item.get("message_ids", [])
            if source_channel is None or bus_type in (None, ""):
                continue
            try:
                normalized_channel = int(source_channel)
                normalized_count = int(frame_count or 0)
                normalized_message_ids = sorted(
                    {
                        int(message_id)
                        for message_id in raw_message_ids
                        if message_id not in (None, "")
                    }
                )
            except (TypeError, ValueError):
                continue
            items.append(
                {
                    "source_channel": normalized_channel,
                    "bus_type": str(bus_type),
                    "frame_count": normalized_count,
                    "message_ids": normalized_message_ids,
                }
            )
        return sorted(items, key=lambda item: (item["source_channel"], item["bus_type"]))

    def _update_trace_metadata(self, trace_id: str, metadata: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE trace_files
                SET metadata_json = ?
                WHERE trace_id = ?
                """,
                (
                    json.dumps(metadata, ensure_ascii=True),
                    trace_id,
                ),
            )

    def delete_trace(self, trace_id: str) -> DeleteTraceResult:
        record = self.get_trace_file(trace_id)
        if record is None:
            raise FileNotFoundError(trace_id)
        referenced_by = [scenario.name for scenario in self.find_scenarios_referencing_trace(trace_id)]
        deleted_library_file = self._unlink_if_exists(record.library_path)
        deleted_cache_file = self._unlink_if_exists(record.metadata.get("cache_path", ""))
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM trace_files WHERE trace_id = ?",
                (trace_id,),
            )
        return DeleteTraceResult(
            trace_id=trace_id,
            name=record.name,
            deleted_library_file=deleted_library_file,
            deleted_cache_file=deleted_cache_file,
            referenced_by=referenced_by,
        )

    def save_scenario(self, scenario: ScenarioSpec) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT scenario_id FROM scenarios WHERE scenario_id = ?",
                (scenario.scenario_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO scenarios (scenario_id, name, body_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        scenario.scenario_id,
                        scenario.name,
                        json.dumps(scenario.to_dict(), ensure_ascii=True, indent=2),
                        now,
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE scenarios
                    SET name = ?, body_json = ?, updated_at = ?
                    WHERE scenario_id = ?
                    """,
                    (
                        scenario.name,
                        json.dumps(scenario.to_dict(), ensure_ascii=True, indent=2),
                        now,
                        scenario.scenario_id,
                    ),
                )

    def delete_scenario(self, scenario_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM scenarios WHERE scenario_id = ?",
                (scenario_id,),
            )
            if cursor.rowcount == 0:
                raise FileNotFoundError(scenario_id)

    def list_scenarios(self) -> List[ScenarioSpec]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT body_json FROM scenarios ORDER BY updated_at DESC, rowid DESC"
            ).fetchall()
        return [ScenarioSpec.from_dict(json.loads(row[0])) for row in rows]

    def load_scenario(self, scenario_id: str) -> ScenarioSpec:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT body_json FROM scenarios WHERE scenario_id = ?",
                (scenario_id,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(scenario_id)
        return ScenarioSpec.from_dict(json.loads(row[0]))

    @staticmethod
    def _unlink_if_exists(raw_path: str) -> bool:
        if not raw_path:
            return False
        path = Path(raw_path)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True
