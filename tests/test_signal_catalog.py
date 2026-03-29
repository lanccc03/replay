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

    def test_catalog_query_helpers_return_known_messages_and_signals(self):
        service = SignalOverrideService()
        codec = StaticMessageCodec(
            {
                0x200: StaticMessageDefinition(
                    name="BodyState",
                    signal_bytes={"light": 0},
                ),
                0x123: StaticMessageDefinition(
                    name="VehicleState",
                    signal_bytes={"vehicle_speed": 0, "gear": 1},
                ),
            }
        )
        service.bind_codec(0, codec)

        self.assertEqual([0x123, 0x200], service.list_message_ids(0))
        self.assertEqual(["vehicle_speed", "gear"], service.list_signal_names(0, 0x123))
        self.assertEqual("VehicleState", service.message_name(0, 0x123))
        self.assertEqual([], service.list_signal_names(1, 0x123))
        self.assertIsNone(service.message_name(0, 0x999))

    def test_apply_multiple_overrides_and_clear_single_signal(self):
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
        service.set_override(
            SignalOverride(
                logical_channel=0,
                message_id_or_pgn=0x123,
                signal_name="gear",
                value=5,
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
        self.assertEqual(bytes([120, 5, 0, 0, 0, 0, 0, 0]), patched.payload)

        service.clear_override(0, 0x123, "gear")
        patched = service.apply(event)
        self.assertEqual(120, patched.payload[0])
        self.assertEqual(2, patched.payload[1])

    def test_overrides_are_isolated_by_channel(self):
        service = SignalOverrideService()
        codec = StaticMessageCodec(
            {
                0x123: StaticMessageDefinition(
                    name="VehicleState",
                    signal_bytes={"vehicle_speed": 0},
                )
            }
        )
        service.bind_codec(0, codec)
        service.bind_codec(1, codec)
        service.set_override(
            SignalOverride(
                logical_channel=1,
                message_id_or_pgn=0x123,
                signal_name="vehicle_speed",
                value=88,
            )
        )
        event = FrameEvent(
            ts_ns=0,
            bus_type=BusType.CAN,
            channel=0,
            message_id=0x123,
            payload=bytes([10, 0, 0, 0, 0, 0, 0, 0]),
            dlc=8,
        )
        patched = service.apply(event)
        self.assertEqual(10, patched.payload[0])

    def test_apply_canfd_override_preserves_wire_dlc(self):
        service = SignalOverrideService()
        codec = StaticMessageCodec(
            {
                0x13B: StaticMessageDefinition(
                    name="VehicleState",
                    signal_bytes={"vehicle_speed": 0},
                )
            }
        )
        service.bind_codec(0, codec)
        service.set_override(
            SignalOverride(
                logical_channel=0,
                message_id_or_pgn=0x13B,
                signal_name="vehicle_speed",
                value=0x55,
            )
        )
        event = FrameEvent(
            ts_ns=0,
            bus_type=BusType.CANFD,
            channel=0,
            message_id=0x13B,
            payload=bytes.fromhex("79E000E000B633CE2F0021150000280E"),
            dlc=0xA,
        )

        patched = service.apply(event)

        self.assertEqual(0x55, patched.payload[0])
        self.assertEqual(16, len(patched.payload))
        self.assertEqual(0xA, patched.dlc)


if __name__ == "__main__":
    unittest.main()
