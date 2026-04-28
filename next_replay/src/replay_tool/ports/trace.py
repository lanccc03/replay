from __future__ import annotations

from typing import Protocol

from replay_tool.domain import Frame


class TraceReader(Protocol):
    def read(self, path: str) -> list[Frame]:
        ...
