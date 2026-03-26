import unittest

import tests.bootstrap  # noqa: F401

from replay_platform.core import FrameEnableRule
from replay_platform.services.frame_enable import FrameEnableService


class FrameEnableServiceTests(unittest.TestCase):
    def test_rules_default_to_enabled(self) -> None:
        service = FrameEnableService()

        self.assertTrue(service.is_enabled(0, 0x123))
        self.assertEqual([], service.list_rules())

    def test_disable_and_restore_rule(self) -> None:
        service = FrameEnableService()
        service.set_rule(FrameEnableRule(logical_channel=0, message_id=0x123, enabled=False))

        self.assertFalse(service.is_enabled(0, 0x123))
        self.assertEqual(
            [FrameEnableRule(logical_channel=0, message_id=0x123, enabled=False)],
            service.list_rules(),
        )

        service.set_enabled(0, 0x123, True)

        self.assertTrue(service.is_enabled(0, 0x123))
        self.assertEqual([], service.list_rules())

    def test_rules_are_isolated_by_channel_and_sorted(self) -> None:
        service = FrameEnableService()
        service.set_enabled(1, 0x200, False)
        service.set_enabled(0, 0x300, False)

        self.assertFalse(service.is_enabled(1, 0x200))
        self.assertTrue(service.is_enabled(0, 0x200))
        self.assertEqual(
            [
                FrameEnableRule(logical_channel=0, message_id=0x300, enabled=False),
                FrameEnableRule(logical_channel=1, message_id=0x200, enabled=False),
            ],
            service.list_rules(),
        )


if __name__ == "__main__":
    unittest.main()
