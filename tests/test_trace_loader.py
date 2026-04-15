import pathlib
import tempfile
import unittest
from unittest.mock import patch

import tests.bootstrap  # noqa: F401

from replay_platform.core import BusType
from replay_platform.services.trace_loader import TraceLoader


class TraceLoaderTests(unittest.TestCase):
    def test_load_sample_asc(self):
        path = pathlib.Path(__file__).parent / "fixtures" / "sample.asc"
        loader = TraceLoader()
        events = loader.load(str(path))
        self.assertEqual(2, len(events))
        self.assertEqual(BusType.CAN, events[0].bus_type)
        self.assertEqual(0, events[0].channel)
        self.assertTrue(events[0].message_id & (1 << 31))
        self.assertEqual(bytes.fromhex("0102030405060708"), events[1].payload)

    def test_load_vector_canfd_asc_with_optional_symbolic_name(self):
        path = pathlib.Path(__file__).parent / "fixtures" / "vector_canfd.asc"
        loader = TraceLoader()
        events = loader.load(str(path))
        self.assertEqual(2, len(events))
        self.assertEqual(BusType.CANFD, events[0].bus_type)
        self.assertEqual(bytes.fromhex("4f20000000000005e10450001a5544950000000000000000"), events[0].payload)
        self.assertEqual({}, events[0].metadata)
        self.assertEqual(0x44D, events[1].message_id)
        self.assertEqual(0xA, events[1].dlc)
        self.assertEqual("MSG_0x44D", events[1].metadata.get("symbolic_name"))
        self.assertEqual(bytes.fromhex("14673fe5da84d1822693080c00000002"), events[1].payload)
        self.assertEqual("Rx", events[1].flags["direction"])
        self.assertTrue(events[1].flags["brs"])
        self.assertFalse(events[1].flags["esi"])

    def test_load_sample_asc_streams_lines_without_read_text(self):
        path = pathlib.Path(__file__).parent / "fixtures" / "sample.asc"
        loader = TraceLoader()

        with patch.object(pathlib.Path, "read_text", side_effect=AssertionError("read_text should not be used for ASC")):
            events = loader.load(str(path))

        self.assertEqual(2, len(events))
        self.assertEqual(BusType.CAN, events[0].bus_type)

    def test_load_asc_keeps_frames_sorted_when_file_is_out_of_order(self):
        loader = TraceLoader()

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "unsorted.asc"
            path.write_text(
                "\n".join(
                    [
                        "date Tue Apr 15 12:00:00.000 2026",
                        "base hex  timestamps absolute",
                        "0.200000 1 123x Rx d 1 02",
                        "0.100000 1 122x Rx d 1 01",
                    ]
                ),
                encoding="utf-8",
            )
            events = loader.load(str(path))

        self.assertEqual([100_000_000, 200_000_000], [event.ts_ns for event in events])
        self.assertEqual([0x80000000 | 0x122, 0x80000000 | 0x123], [event.message_id for event in events])

    def test_binary_cache_round_trip_preserves_frame_fields(self):
        path = pathlib.Path(__file__).parent / "fixtures" / "vector_canfd.asc"
        loader = TraceLoader()
        events = loader.load(str(path))

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = pathlib.Path(tmp) / "trace.rplbin"
            loader.write_binary_cache(cache_path, events)
            loaded = loader.load_binary_cache(cache_path)

        self.assertEqual(events, loaded)
        self.assertEqual(events[1].flags, loaded[1].flags)
        self.assertEqual(events[1].metadata, loaded[1].metadata)
        self.assertEqual(events[1].source_file, loaded[1].source_file)


if __name__ == "__main__":
    unittest.main()
