import unittest

import tests.bootstrap  # noqa: F401

from replay_platform.diagnostics.dtc import DtcDictionary, DtcParser


class DtcParserTests(unittest.TestCase):
    def test_parse_read_dtc_response(self):
        dictionary = DtcDictionary({"123456": "Brake pressure signal invalid"})
        payload = bytes.fromhex("5902FF1234568C")
        records = DtcParser.parse_read_response(payload, dictionary)
        self.assertEqual(1, len(records))
        self.assertEqual("123456", records[0].code)
        self.assertEqual("Brake pressure signal invalid", records[0].description)
        self.assertIn("confirmed_dtc", records[0].status_flags)
        self.assertIn("warning_indicator_requested", records[0].status_flags)


if __name__ == "__main__":
    unittest.main()
