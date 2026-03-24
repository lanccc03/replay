import pathlib
import unittest

import tests.bootstrap  # noqa: F401

from replay_platform.services.trace_loader import TraceLoader


class TraceLoaderTests(unittest.TestCase):
    def test_load_sample_asc(self):
        path = pathlib.Path(__file__).parent / "fixtures" / "sample.asc"
        loader = TraceLoader()
        events = loader.load(str(path))
        self.assertEqual(2, len(events))
        self.assertEqual(0, events[0].channel)
        self.assertTrue(events[0].message_id & (1 << 31))
        self.assertEqual(bytes.fromhex("0102030405060708"), events[1].payload)


if __name__ == "__main__":
    unittest.main()
