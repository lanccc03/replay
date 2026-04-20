import unittest
from pathlib import Path

import tests.bootstrap  # noqa: F401

from replay_platform.core import BusType, FrameEvent, SignalOverride
from replay_platform.services.signal_catalog import (
    MessageCatalogEntry,
    SignalOverrideService,
    SignalCatalogEntry,
    StaticMessageCodec,
    StaticMessageDefinition,
)


class SignalOverrideTests(unittest.TestCase):
    @staticmethod
    def _fixture_path(name: str) -> str:
        return str(Path(__file__).resolve().parent / "fixtures" / name)

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

    def test_catalog_query_helpers_return_message_and_signal_metadata(self):
        try:
            import cantools  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("cantools 未安装，跳过真实 DBC 解析测试")
        service = SignalOverrideService()

        service.load_database(0, self._fixture_path("sample_vehicle.dbc"), format="dbc")

        self.assertEqual(
            [
                MessageCatalogEntry(message_id=0x123, message_name="VehicleStatus"),
                MessageCatalogEntry(message_id=0x200, message_name="BodyState"),
            ],
            service.list_messages(0),
        )
        self.assertEqual(
            [
                SignalCatalogEntry(
                    message_id=0x200,
                    signal_name="LightState",
                    unit="",
                    minimum=0,
                    maximum=3,
                    choices={0: "Off", 1: "LowBeam", 2: "HighBeam", 3: "Hazard"},
                ),
                SignalCatalogEntry(
                    message_id=0x200,
                    signal_name="BrakePressed",
                    unit="",
                    minimum=0,
                    maximum=1,
                    choices={},
                ),
            ],
            service.list_signals(0, 0x200),
        )
        self.assertEqual("VehicleStatus", service.message_name(0, 0x123))

    def test_load_database_rejects_unsupported_format(self):
        service = SignalOverrideService()

        with self.assertRaisesRegex(ValueError, "仅支持 dbc"):
            service.load_database(0, self._fixture_path("sample_vehicle.dbc"), format="arxml")

    def test_clear_codecs_only_resets_database_catalog(self):
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
        service.set_override(
            SignalOverride(
                logical_channel=0,
                message_id_or_pgn=0x123,
                signal_name="vehicle_speed",
                value=100,
            )
        )

        service.clear_codecs()

        self.assertEqual([], service.list_message_ids(0))
        self.assertEqual(1, len(service.list_overrides()))

    def test_static_codec_signal_catalog_uses_metadata(self):
        service = SignalOverrideService()
        codec = StaticMessageCodec(
            {
                0x123: StaticMessageDefinition(
                    name="VehicleState",
                    signal_bytes={"vehicle_speed": 0},
                    signal_metadata={
                        "vehicle_speed": {
                            "unit": "km/h",
                            "minimum": 0,
                            "maximum": 250,
                            "choices": {0: "Stop"},
                        }
                    },
                )
            }
        )
        service.bind_codec(0, codec)

        self.assertEqual(
            [
                SignalCatalogEntry(
                    message_id=0x123,
                    signal_name="vehicle_speed",
                    unit="km/h",
                    minimum=0,
                    maximum=250,
                    choices={0: "Stop"},
                )
            ],
            service.list_signals(0, 0x123),
        )


if __name__ == "__main__":
    unittest.main()
