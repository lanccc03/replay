from __future__ import annotations

from collections.abc import Callable

from replay_tool.domain import DeviceConfig
from replay_tool.ports.device import BusDevice


DeviceFactory = Callable[[DeviceConfig], BusDevice]


class DeviceRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, DeviceFactory] = {}

    def register(self, driver: str, factory: DeviceFactory) -> None:
        self._factories[str(driver).lower()] = factory

    def create(self, config: DeviceConfig) -> BusDevice:
        driver = config.driver.lower()
        factory = self._factories.get(driver)
        if factory is None:
            raise ValueError(f"Unsupported device driver: {config.driver}")
        return factory(config)

    def drivers(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))
