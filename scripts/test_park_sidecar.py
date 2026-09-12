import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock


MODULE_PATH = os.path.join(os.path.dirname(__file__), "park_sidecar.py")
SPEC = importlib.util.spec_from_file_location("park_sidecar", MODULE_PATH)
park_sidecar = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(park_sidecar)


class ParkSidecarTests(unittest.TestCase):
    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sidecar.json")
            state = {
                "withheld": {
                    "u2": {"id": "u2", "payload": {"n": 2}},
                    "u1": {"id": "u1", "payload": {"n": 1}},
                },
                "parked_until": {"u2": 200.5, "u1": 100.25},
            }

            park_sidecar.save(path, state)
            loaded, problem = park_sidecar.load(path)

            self.assertIsNone(problem)
            self.assertEqual(loaded["withheld"], state["withheld"])
            self.assertEqual(loaded["parked_until"], state["parked_until"])

    def test_save_with_no_withheld_removes_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sidecar.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{}")

            park_sidecar.save(path, {"withheld": {}, "parked_until": {}})

            self.assertFalse(os.path.exists(path))

    def test_replace_failure_leaves_previous_file_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sidecar.json")
            old_state = {
                "withheld": {"u1": {"id": "u1", "v": 1}},
                "parked_until": {"u1": 10.0},
            }
            new_state = {
                "withheld": {"u2": {"id": "u2", "v": 2}},
                "parked_until": {"u2": 20.0},
            }
            park_sidecar.save(path, old_state)

            with mock.patch.object(
                park_sidecar.os, "replace", side_effect=OSError("replace failed")
            ):
                with self.assertRaises(OSError):
                    park_sidecar.save(path, new_state)

            loaded, problem = park_sidecar.load(path)
            self.assertIsNone(problem)
            self.assertEqual(loaded["withheld"], old_state["withheld"])
            self.assertEqual(loaded["parked_until"], old_state["parked_until"])

    def test_load_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "missing.json")
            loaded, problem = park_sidecar.load(path)
            self.assertIsNone(problem)
            self.assertEqual(loaded, {"withheld": {}, "parked_until": {}})

    def test_load_invalid_json_keeps_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sidecar.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json")

            loaded, problem = park_sidecar.load(path)

            self.assertEqual(loaded, {"withheld": {}, "parked_until": {}})
            self.assertIsInstance(problem, str)
            self.assertIn(path, problem)
            self.assertTrue(os.path.exists(path))

    def test_load_json_list_keeps_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sidecar.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([1, 2, 3], handle)

            loaded, problem = park_sidecar.load(path)

            self.assertEqual(loaded, {"withheld": {}, "parked_until": {}})
            self.assertIsInstance(problem, str)
            self.assertIn(path, problem)
            self.assertTrue(os.path.exists(path))

    def test_load_bad_version_keeps_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sidecar.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "version": 2,
                        "withheld": {"u1": {"id": "u1"}},
                        "parked_until": {},
                    },
                    handle,
                )

            loaded, problem = park_sidecar.load(path)

            self.assertEqual(loaded, {"withheld": {}, "parked_until": {}})
            self.assertIsInstance(problem, str)
            self.assertIn(path, problem)
            self.assertTrue(os.path.exists(path))

    def test_restore_into_appends_missing_sorted_and_deduplicates(self):
        rows = [{"id": "b", "v": 2}, {"id": "d", "v": 4}]
        state = {
            "withheld": {
                "c": {"id": "c", "v": 3},
                "a": {"id": "a", "v": 1},
                "b": {"id": "b", "v": 99},
            },
            "parked_until": {},
        }

        new_rows, restored_ids = park_sidecar.restore_into(rows, state)

        self.assertEqual([row["id"] for row in new_rows], ["b", "d", "a", "c"])
        self.assertEqual(restored_ids, ["a", "c"])
        self.assertEqual(new_rows[2], {"id": "a", "v": 1})
        self.assertEqual(new_rows[3], {"id": "c", "v": 3})

    def test_simulated_process_restart_restores_both_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sidecar.json")
            state = {
                "withheld": {
                    "u2": {"id": "u2", "task": "second"},
                    "u1": {"id": "u1", "task": "first"},
                },
                "parked_until": {"u2": 2.0, "u1": 1.0},
            }
            park_sidecar.save(path, state)

            loaded, problem = park_sidecar.load(path)
            self.assertIsNone(problem)

            plan = [{"id": "u3", "task": "third"}]
            new_rows, restored_ids = park_sidecar.restore_into(plan, loaded)

            self.assertEqual(restored_ids, ["u1", "u2"])
            self.assertEqual([row["id"] for row in new_rows], ["u3", "u1", "u2"])
            self.assertEqual(new_rows[1]["task"], "first")
            self.assertEqual(new_rows[2]["task"], "second")


if __name__ == "__main__":
    unittest.main()
