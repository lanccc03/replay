import tempfile
import unittest
from pathlib import Path

import tests.bootstrap  # noqa: F401

from replay_platform.core import ScenarioSpec
from replay_platform.paths import AppPaths
from replay_platform.services.library import FileLibraryService
from replay_platform.services.trace_loader import TraceLoader


class FileLibraryTests(unittest.TestCase):
    def test_import_trace_and_save_scenario(self):
        fixture = Path(__file__).parent / "fixtures" / "sample.asc"
        with tempfile.TemporaryDirectory() as tmp:
            service = FileLibraryService(AppPaths(Path(tmp)), TraceLoader())
            record = service.import_trace(str(fixture))
            self.assertEqual(2, record.event_count)
            scenario = ScenarioSpec(
                scenario_id="scenario-1",
                name="Smoke Scenario",
                trace_file_ids=[record.trace_id],
            )
            service.save_scenario(scenario)
            loaded = service.load_scenario("scenario-1")
            self.assertEqual("Smoke Scenario", loaded.name)
            self.assertEqual([record.trace_id], loaded.trace_file_ids)


if __name__ == "__main__":
    unittest.main()
