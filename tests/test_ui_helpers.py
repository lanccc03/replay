from __future__ import annotations

import unittest

from replay_platform.ui.main_window import (
    _parse_bool_text,
    _parse_hex_bytes_text,
    _parse_int_text,
    _parse_json_object_text,
    _parse_scalar_text,
    _plan_log_refresh,
)


class MainWindowHelperTests(unittest.TestCase):
    def test_parse_int_supports_hex(self) -> None:
        self.assertEqual(_parse_int_text("0x7E0", "tx_id"), 0x7E0)
        self.assertEqual(_parse_int_text("42", "port"), 42)

    def test_parse_bool_supports_common_values(self) -> None:
        self.assertTrue(_parse_bool_text("true", "enabled"))
        self.assertTrue(_parse_bool_text("是", "enabled"))
        self.assertFalse(_parse_bool_text("0", "enabled"))
        self.assertFalse(_parse_bool_text("", "enabled"))

    def test_parse_json_object_defaults_to_empty_dict(self) -> None:
        self.assertEqual(_parse_json_object_text("", "metadata"), {})
        self.assertEqual(_parse_json_object_text('{"ip":"192.168.0.10"}', "network"), {"ip": "192.168.0.10"})

    def test_parse_scalar_keeps_plain_text_and_numbers(self) -> None:
        self.assertEqual(_parse_scalar_text("12"), 12)
        self.assertEqual(_parse_scalar_text("12.5"), 12.5)
        self.assertEqual(_parse_scalar_text("0x123"), 0x123)
        self.assertEqual(_parse_scalar_text("vehicle_speed"), "vehicle_speed")

    def test_parse_hex_bytes_normalizes_spacing(self) -> None:
        self.assertEqual(_parse_hex_bytes_text("10 03", "payload"), "1003")
        self.assertEqual(_parse_hex_bytes_text("", "payload"), "")

    def test_plan_log_refresh_requests_reset_when_cursor_falls_behind_buffer(self) -> None:
        self.assertEqual(("reset", 0), _plan_log_refresh(4, 5, 2000))

    def test_plan_log_refresh_appends_from_cursor_offset(self) -> None:
        self.assertEqual(("append", 3), _plan_log_refresh(8, 5, 10))


if __name__ == "__main__":
    unittest.main()
