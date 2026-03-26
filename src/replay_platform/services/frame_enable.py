from __future__ import annotations

from typing import Dict, List

from replay_platform.core import FrameEnableRule


class FrameEnableService:
    def __init__(self) -> None:
        self._disabled_rules: Dict[tuple[int, int], FrameEnableRule] = {}

    def set_rule(self, rule: FrameEnableRule) -> None:
        key = (rule.logical_channel, rule.message_id)
        if rule.enabled:
            self._disabled_rules.pop(key, None)
            return
        self._disabled_rules[key] = rule

    def set_enabled(self, logical_channel: int, message_id: int, enabled: bool) -> None:
        self.set_rule(
            FrameEnableRule(
                logical_channel=logical_channel,
                message_id=message_id,
                enabled=enabled,
            )
        )

    def is_enabled(self, logical_channel: int, message_id: int) -> bool:
        return (logical_channel, message_id) not in self._disabled_rules

    def clear_rule(self, logical_channel: int, message_id: int) -> None:
        self._disabled_rules.pop((logical_channel, message_id), None)

    def clear_all(self) -> None:
        self._disabled_rules.clear()

    def list_rules(self) -> List[FrameEnableRule]:
        return sorted(
            self._disabled_rules.values(),
            key=lambda item: (item.logical_channel, item.message_id),
        )
