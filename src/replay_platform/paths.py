from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppPaths:
    root: Path

    @property
    def data_dir(self) -> Path:
        return self.root / ".replay_platform"

    @property
    def trace_dir(self) -> Path:
        return self.data_dir / "traces"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def database_dir(self) -> Path:
        return self.data_dir / "databases"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "library.sqlite3"

    def ensure(self) -> None:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.database_dir.mkdir(parents=True, exist_ok=True)

