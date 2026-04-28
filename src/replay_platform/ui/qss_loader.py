from __future__ import annotations

from importlib import resources


def load_qss(filename: str) -> str:
    return resources.files("replay_platform.ui").joinpath("qss", filename).read_text(encoding="utf-8")
