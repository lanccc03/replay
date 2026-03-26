import tempfile
import unittest
from pathlib import Path

import tests.bootstrap  # noqa: F401

from replay_platform.core import ScenarioSpec
from replay_platform.paths import AppPaths
from replay_platform.services.library import FileLibraryService
from replay_platform.services.trace_loader import BINARY_CACHE_FORMAT, TraceLoader


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

    def test_import_trace_writes_binary_cache(self):
        fixture = Path(__file__).parent / "fixtures" / "sample.asc"
        with tempfile.TemporaryDirectory() as tmp:
            service = FileLibraryService(AppPaths(Path(tmp)), TraceLoader())
            record = service.import_trace(str(fixture))

            self.assertEqual(BINARY_CACHE_FORMAT, record.metadata["cache_format"])
            self.assertTrue(Path(record.metadata["cache_path"]).exists())
            self.assertEqual(".rplbin", Path(record.metadata["cache_path"]).suffix)

    def test_load_trace_events_migrates_legacy_json_cache(self):
        fixture = Path(__file__).parent / "fixtures" / "sample.asc"
        with tempfile.TemporaryDirectory() as tmp:
            service = FileLibraryService(AppPaths(Path(tmp)), TraceLoader())
            record = service.import_trace(str(fixture))
            library_path = Path(record.library_path)
            legacy_events = service.trace_loader.load(str(library_path))
            legacy_cache_path = service.paths.cache_dir / f"{record.trace_id}.json"
            service.trace_loader.write_cache(legacy_cache_path, legacy_events)
            Path(record.metadata["cache_path"]).unlink()
            with service._connect() as connection:
                connection.execute(
                    "UPDATE trace_files SET metadata_json = ? WHERE trace_id = ?",
                    (f'{{"cache_path":"{legacy_cache_path.as_posix()}"}}', record.trace_id),
                )

            loaded = service.load_trace_events(record.trace_id)
            migrated = service.get_trace_file(record.trace_id)

            self.assertEqual(2, len(loaded))
            assert migrated is not None
            self.assertEqual(BINARY_CACHE_FORMAT, migrated.metadata["cache_format"])
            self.assertTrue(Path(migrated.metadata["cache_path"]).exists())
            self.assertEqual(".rplbin", Path(migrated.metadata["cache_path"]).suffix)

    def test_delete_trace_removes_record_library_file_and_cache(self):
        fixture = Path(__file__).parent / "fixtures" / "sample.asc"
        with tempfile.TemporaryDirectory() as tmp:
            service = FileLibraryService(AppPaths(Path(tmp)), TraceLoader())
            record = service.import_trace(str(fixture))

            result = service.delete_trace(record.trace_id)

            self.assertEqual(record.name, result.name)
            self.assertTrue(result.deleted_library_file)
            self.assertTrue(result.deleted_cache_file)
            self.assertEqual([], result.referenced_by)
            self.assertIsNone(service.get_trace_file(record.trace_id))
            self.assertFalse(Path(record.library_path).exists())
            self.assertFalse(Path(record.metadata["cache_path"]).exists())

    def test_delete_trace_ignores_missing_files_and_reports_references(self):
        fixture = Path(__file__).parent / "fixtures" / "sample.asc"
        with tempfile.TemporaryDirectory() as tmp:
            service = FileLibraryService(AppPaths(Path(tmp)), TraceLoader())
            record = service.import_trace(str(fixture))
            scenario = ScenarioSpec(
                scenario_id="scenario-1",
                name="Smoke Scenario",
                trace_file_ids=[record.trace_id],
            )
            service.save_scenario(scenario)
            Path(record.library_path).unlink()
            Path(record.metadata["cache_path"]).unlink()

            result = service.delete_trace(record.trace_id)

            self.assertFalse(result.deleted_library_file)
            self.assertFalse(result.deleted_cache_file)
            self.assertEqual(["Smoke Scenario"], result.referenced_by)
            self.assertIsNone(service.get_trace_file(record.trace_id))

    def test_find_scenarios_referencing_trace_returns_matches_only(self):
        fixture = Path(__file__).parent / "fixtures" / "sample.asc"
        with tempfile.TemporaryDirectory() as tmp:
            service = FileLibraryService(AppPaths(Path(tmp)), TraceLoader())
            record_a = service.import_trace(str(fixture))
            record_b = service.import_trace(str(fixture))
            service.save_scenario(ScenarioSpec(scenario_id="scenario-1", name="A", trace_file_ids=[record_a.trace_id]))
            service.save_scenario(ScenarioSpec(scenario_id="scenario-2", name="B", trace_file_ids=[record_a.trace_id, record_b.trace_id]))
            service.save_scenario(ScenarioSpec(scenario_id="scenario-3", name="C", trace_file_ids=[]))

            scenarios = service.find_scenarios_referencing_trace(record_a.trace_id)

            self.assertEqual(["B", "A"], [scenario.name for scenario in scenarios])

    def test_delete_scenario_removes_only_target_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = FileLibraryService(AppPaths(Path(tmp)), TraceLoader())
            service.save_scenario(ScenarioSpec(scenario_id="scenario-1", name="A"))
            service.save_scenario(ScenarioSpec(scenario_id="scenario-2", name="B"))

            service.delete_scenario("scenario-1")

            self.assertEqual(["B"], [scenario.name for scenario in service.list_scenarios()])
            with self.assertRaises(FileNotFoundError):
                service.load_scenario("scenario-1")


if __name__ == "__main__":
    unittest.main()
