from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from replay_tool.adapters.mock import MockDevice
from replay_tool.adapters.tongxing import TongxingDevice
from replay_tool.domain import DeviceConfig, ReplayScenario
from replay_tool.planning import ReplayPlan, ReplayPlanner
from replay_tool.ports.registry import DeviceRegistry
from replay_tool.runtime import ReplayRuntime
from replay_tool.storage import AscTraceReader


def build_default_registry() -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register("mock", lambda config: MockDevice(config))
    registry.register("tongxing", lambda config: TongxingDevice(config))
    return registry


class ReplayApplication:
    def __init__(
        self,
        *,
        registry: DeviceRegistry | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.registry = registry or build_default_registry()
        self.logger = logger or (lambda _message: None)
        self.trace_reader = AscTraceReader()
        self.planner = ReplayPlanner(self.trace_reader)

    def load_scenario(self, path: str | Path) -> ReplayScenario:
        scenario_path = Path(path)
        payload = json.loads(scenario_path.read_text(encoding="utf-8"))
        return ReplayScenario.from_dict(payload)

    def compile_plan(self, scenario_path: str | Path) -> ReplayPlan:
        path = Path(scenario_path)
        scenario = self.load_scenario(path)
        return self.planner.compile(scenario, base_dir=path.parent)

    def validate(self, scenario_path: str | Path) -> ReplayPlan:
        return self.compile_plan(scenario_path)

    def run(self, scenario_path: str | Path) -> ReplayRuntime:
        plan = self.compile_plan(scenario_path)
        runtime = ReplayRuntime(self.registry, logger=self.logger)
        runtime.configure(plan)
        runtime.start()
        runtime.wait()
        return runtime

    def create_device(self, config: DeviceConfig):
        return self.registry.create(config)
