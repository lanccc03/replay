import unittest

import tests.bootstrap  # noqa: F401

from replay_platform.core import BusType, FrameEvent, SignalOverride
from replay_platform.services.signal_catalog import (
    SignalOverrideService,
    StaticMessageCodec,
    StaticMessageDefinition,
)


class SignalOverrideTests(unittest.TestCase):
    def test_apply_override(self):
        service = SignalOverrideService()
        codec = StaticMessageCodec(
            {
                0x123: StaticMessageDefinition(
                    name="VehicleState",
                    signal_bytes={"vehicle_speed": 0, "gear": 1},
                )
            }
        )
        service.bind_codec(0, codec)
        service.set_override(
            SignalOverride(
                logical_channel=0,
                message_id_or_pgn=0x123,
                signal_name="vehicle_speed",
                value=120,
            )
        )
        event = FrameEvent(
            ts_ns=0,
            bus_type=BusType.CAN,
            channel=0,
            message_id=0x123,
            payload=bytes([10, 2, 0, 0, 0, 0, 0, 0]),
            dlc=8,
        )
        patched = service.apply(event)
        self.assertEqual(120, patched.payload[0])
        self.assertEqual(2, patched.payload[1])


if __name__ == "__main__":
    unittest.main()
