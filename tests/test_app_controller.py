from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import tests.bootstrap  # noqa: F401

from replay_platform.app_controller import LOG_BUFFER_LIMIT, ReplayApplication
from replay_platform.ui.main_window import _plan_log_refresh


class ReplayApplicationLogTests(unittest.TestCase):
    def test_log_buffer_keeps_recent_entries_and_cursor_continues_after_trim(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            app = ReplayApplication(Path(workspace))
            for index in range(LOG_BUFFER_LIMIT + 5):
                app.log(f"log {index}")

            base_index, entries = app.log_snapshot()
            self.assertEqual(5, base_index)
            self.assertEqual(LOG_BUFFER_LIMIT, len(entries))
            self.assertEqual("log 5", entries[0])
            self.assertEqual(f"log {LOG_BUFFER_LIMIT + 4}", entries[-1])

            cursor = base_index + len(entries)
            app.log(f"log {LOG_BUFFER_LIMIT + 5}")
            app.log(f"log {LOG_BUFFER_LIMIT + 6}")

            next_base, next_entries = app.log_snapshot()
            self.assertEqual(7, next_base)
            self.assertEqual(LOG_BUFFER_LIMIT, len(next_entries))

            mode, offset = _plan_log_refresh(cursor, next_base, len(next_entries))
            self.assertEqual("append", mode)
            self.assertEqual(
                [f"log {LOG_BUFFER_LIMIT + 5}", f"log {LOG_BUFFER_LIMIT + 6}"],
                next_entries[offset:],
            )


if __name__ == "__main__":
    unittest.main()
