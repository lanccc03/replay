from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from replay_platform.core import ScenarioSpec, TraceFileRecord
from replay_platform.paths import AppPaths
from replay_platform.services.trace_loader import TraceLoader


class FileLibraryService:
    def __init__(self, paths: AppPaths, trace_loader: Optional[TraceLoader] = None) -> None:
        self.paths = paths
        self.trace_loader = trace_loader or TraceLoader()
        self.paths.ensure()
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.paths.sqlite_path)

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
        cache_path = self.paths.cache_dir / f"{trace_id}.json"
        self.trace_loader.write_cache(cache_path, events)
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
            metadata={"cache_path": str(cache_path)},
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

    def load_trace_events(self, trace_id: str):
        record = self.get_trace_file(trace_id)
        if record is None:
            raise FileNotFoundError(trace_id)
        cache_path = Path(record.metadata.get("cache_path", ""))
        if cache_path.exists():
            return self.trace_loader.load_cache(cache_path)
        events = self.trace_loader.load(record.library_path)
        if cache_path:
            self.trace_loader.write_cache(cache_path, events)
        return events

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

    def list_scenarios(self) -> List[ScenarioSpec]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT body_json FROM scenarios ORDER BY updated_at DESC"
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

