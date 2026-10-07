#!/usr/bin/env python3
"""Tests for scripts/coe_p0_driver.py (P0.1).

    python3 -B scripts/test_coe_p0_driver.py

The driver is a script, so this suite RUNS it and never imports it as a
module: runpy runs the file the way the command runs it and hands back the
script's own namespace. That keeps this file runnable on the public export
tree, where the driver is a sibling script rather than an installed package.
Every board, lock file and run record here is built in a fresh temporary
directory, so nothing here reads a live repository document, $HOME, the
network, or a private package, and nothing here starts a child process.

If the driver file is absent or cannot be run, every test in this file
fails: the suite is red without the code and green with it.
"""

import json
import os
import runpy
import sys
import tempfile
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_DRIVER_PATH = os.path.join(_HERE, "coe_p0_driver.py")

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _remove_tree(path):
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isdir(full) and not os.path.islink(full):
            _remove_tree(full)
        else:
            os.remove(full)
    os.rmdir(path)


class DriverCase(unittest.TestCase):
    """One temp directory and one freshly run driver per test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="coe_p0_case_")
        self.addCleanup(_remove_tree, self.tmp)
        try:
            self.driver = runpy.run_path(_DRIVER_PATH)
        except Exception as exc:
            # A missing or unloadable driver is a FAILED test, never a
            # skipped one and never a passing one.
            self.fail(
                "scripts/coe_p0_driver.py could not be run as a script: "
                "%s: %s" % (type(exc).__name__, exc)
            )
        self.run_priority_round = self.driver["run_priority_round"]
        self.driver_main = self.driver["driver_main"]
        self.find_violations = self.driver["find_violations"]
        self.repair_board = self.driver["repair_board"]
        self.record_council_run = self.driver["record_council_run"]
        self.refused = self.driver["DriverRefused"]
        self.wip_limit = self.driver["WIP_LIMIT"]
        self.finish_order = list(self.driver["FINISH_ORDER"])
        self.exit_done = self.driver["EXIT_DONE"]
        self.exit_usage = self.driver["EXIT_USAGE"]
        self.exit_refused = self.driver["EXIT_REFUSED"]

    def _path(self, name):
        return os.path.join(self.tmp, name)

    def _write_json(self, name, value):
        path = self._path(name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        return path

    def _write_text(self, name, text):
        path = self._path(name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def _write_bytes(self, name, blob):
        path = self._path(name)
        with open(path, "wb") as handle:
            handle.write(blob)
        return path

    def _read_json(self, path):
        with open(path, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))

    def _board(self, **overrides):
        board = {
            "revision": 5,
            "release_chain": list(self.finish_order),
            "units": [
                {"id": "D3", "state": "active"},
                {"id": "D10", "state": "todo"},
                {"id": "D4", "state": "todo"},
            ],
        }
        board.update(overrides)
        return board

    def _active_ids(self, board):
        return [
            unit["id"]
            for unit in board["units"]
            if unit.get("state") == "active"
        ]


class CleanBoardRunsOneRound(DriverCase):
    """RQ-DRIVER: one command runs one round over a board that already
    obeys the standing order, and records it."""

    def test_clean_board_reports_done_and_records_the_round(self):
        path = self._write_json("board.json", self._board())
        self.assertEqual(self.run_priority_round(path), self.exit_done)
        reloaded = self._read_json(path)
        self.assertEqual(self.find_violations(reloaded), [])
        self.assertEqual(reloaded["council_round"]["outcome"], "PASS")
        self.assertEqual(reloaded["council_round"]["revision"], 5)


class StandingOrderGate(DriverCase):
    """RQ-WIP and RQ-STOP: the limit of 2 on the one landing lane and the
    stop rule for off chain work are repaired when they can be, and refused
    when they cannot."""

    def test_repairable_dirty_board_is_repaired_to_clean(self):
        board = self._board(units=[
            {"id": "D3", "state": "active"},
            {"id": "D10", "state": "active"},
            {"id": "D4", "state": "active"},
            {"id": "D11", "state": "todo"},
        ])
        path = self._write_json("board.json", board)
        self.assertEqual(self.run_priority_round(path), self.exit_done)
        reloaded = self._read_json(path)
        self.assertEqual(self.find_violations(reloaded), [])
        self.assertEqual(len(self._active_ids(reloaded)), self.wip_limit)
        self.assertEqual(sorted(self._active_ids(reloaded)), ["D10", "D3"])

    def test_unrepairable_board_is_refused(self):
        board = self._board(
            revision=2,
            release_chain=["D3", "D10"],
            pinned=["X9"],
            units=[
                {"id": "D3", "state": "active"},
                {"id": "X9", "state": "active"},
            ],
        )
        path = self._write_json("board.json", board)
        self.assertEqual(self.run_priority_round(path), self.exit_refused)
        reloaded = self._read_json(path)
        states = {unit["id"]: unit["state"] for unit in reloaded["units"]}
        self.assertEqual(states["X9"], "active")
        self.assertEqual(states["D3"], "active")

    def test_off_chain_active_without_pin_is_repaired(self):
        board = self._board(units=[
            {"id": "D3", "state": "active"},
            {"id": "X9", "state": "active"},
        ])
        path = self._write_json("board.json", board)
        self.assertEqual(self.run_priority_round(path), self.exit_done)
        reloaded = self._read_json(path)
        self.assertEqual(self.find_violations(reloaded), [])
        self.assertEqual(self._active_ids(reloaded), ["D3"])


class RefusalOnBadInput(DriverCase):
    """A missing, empty, corrupt or wrongly shaped board refuses; it is
    never read as the safe case and never crashes."""

    def test_empty_board_file_is_refused(self):
        path = self._write_text("empty.json", "")
        self.assertEqual(self.run_priority_round(path), self.exit_refused)

    def test_whitespace_only_board_file_is_refused(self):
        path = self._write_text("blank.json", "   \n\t")
        self.assertEqual(self.run_priority_round(path), self.exit_refused)

    def test_corrupt_board_json_is_refused(self):
        path = self._write_text("corrupt.json", "{ this is not json")
        self.assertEqual(self.run_priority_round(path), self.exit_refused)

    def test_board_json_that_is_not_an_object_is_refused(self):
        path = self._write_text("list.json", "[1, 2, 3]")
        self.assertEqual(self.run_priority_round(path), self.exit_refused)

    def test_missing_board_file_is_refused(self):
        self.assertEqual(
            self.run_priority_round(self._path("absent.json")),
            self.exit_refused,
        )

    def test_directory_instead_of_board_file_is_refused(self):
        path = self._path("a_directory")
        os.mkdir(path)
        self.assertEqual(self.run_priority_round(path), self.exit_refused)

    def test_bytes_that_are_not_utf8_are_refused(self):
        path = self._write_bytes("latin1.json", b"\xff\xfe\x00\x00")
        self.assertEqual(self.run_priority_round(path), self.exit_refused)

    def test_hostile_board_path_shapes_are_refused(self):
        for value in (None, True, 0, 2.5, b"board.json", [], {}, ""):
            self.assertEqual(
                self.run_priority_round(value),
                self.exit_refused,
                "board_path %r was not refused" % (value,),
            )

    def test_board_units_that_are_not_a_list_are_refused(self):
        path = self._write_json("board.json", self._board(units="D3, D10"))
        self.assertEqual(self.run_priority_round(path), self.exit_refused)

    def test_unit_with_unhashable_id_is_refused(self):
        board = self._board(units=[{"id": ["D3"], "state": "active"}])
        path = self._write_json("board.json", board)
        self.assertEqual(self.run_priority_round(path), self.exit_refused)

    def test_unit_with_unknown_state_is_refused(self):
        board = self._board(units=[{"id": "D3", "state": "merging"}])
        path = self._write_json("board.json", board)
        self.assertEqual(self.run_priority_round(path), self.exit_refused)

    def test_boolean_revision_is_refused(self):
        path = self._write_json("board.json", self._board(revision=True))
        self.assertEqual(self.run_priority_round(path), self.exit_refused)

    def test_nan_revision_is_refused(self):
        board = self._board(revision=float("nan"))
        path = self._write_json("board.json", board)
        self.assertEqual(self.run_priority_round(path), self.exit_refused)

    def test_a_board_altered_after_it_was_read_is_refused(self):
        board = self._board()
        self.assertEqual(self.find_violations(board), [])
        board["units"][0]["state"] = "merging"
        with self.assertRaises(ValueError):
            self.find_violations(board)


class SerializationAndStaleness(DriverCase):
    """Concurrent runs are serialized by a lock file, a stale board is
    refreshed before the round, and a round already recorded at the current
    revision is reported done without rerunning."""

    def test_lock_file_refuses_a_second_concurrent_run(self):
        path = self._write_json("board.json", self._board())
        lock = path + ".coe_p0.lock"
        with open(lock, "w", encoding="utf-8") as handle:
            handle.write("held")
        try:
            self.assertEqual(self.run_priority_round(path), self.exit_refused)
        finally:
            os.remove(lock)

    def test_stale_board_is_refreshed_before_the_round(self):
        board = self._board(revision=7, stale=True)
        path = self._write_json("board.json", board)
        self.assertEqual(self.run_priority_round(path), self.exit_done)
        reloaded = self._read_json(path)
        self.assertIs(reloaded.get("stale"), False)
        self.assertEqual(reloaded.get("revision"), 8)

    def test_a_recorded_round_at_the_current_revision_is_not_rerun(self):
        board = self._board(revision=5)
        board["council_round"] = {
            "revision": 5,
            "outcome": "PASS",
            "note": "SENTINEL",
        }
        path = self._write_json("board.json", board)
        self.assertEqual(self.run_priority_round(path), self.exit_done)
        reloaded = self._read_json(path)
        self.assertEqual(reloaded["council_round"].get("note"), "SENTINEL")
        self.assertEqual(reloaded["council_round"]["revision"], 5)


class DriverMainCommand(DriverCase):
    """The command line front door: usage for a malformed command, the
    round's own exit code for a board, refusal for a board that refuses."""

    def test_usage_is_returned_for_a_wrong_command(self):
        self.assertEqual(self.driver_main([]), self.exit_usage)
        self.assertEqual(self.driver_main(["round"]), self.exit_usage)
        self.assertEqual(
            self.driver_main(["status", "board.json"]), self.exit_usage
        )
        self.assertEqual(
            self.driver_main(["round", "a", "b"]), self.exit_usage
        )

    def test_hostile_argv_is_usage_not_a_crash(self):
        for value in (None, "round", 42, ["round", None], ["round", 7], {}):
            self.assertEqual(self.driver_main(value), self.exit_usage)

    def test_round_command_runs_the_round(self):
        path = self._write_json("board.json", self._board())
        self.assertEqual(self.driver_main(["round", path]), self.exit_done)

    def test_refused_board_is_refused_from_the_command(self):
        path = self._path("missing.json")
        self.assertEqual(self.driver_main(["round", path]), self.exit_refused)

    def test_empty_board_path_from_the_command_is_refused(self):
        self.assertEqual(self.driver_main(["round", ""]), self.exit_refused)


class RecordCouncilRun(DriverCase):
    """RQ-HERMETIC: the run record goes to a caller named directory or a
    temp file, never to $HOME, and a hostile record request refuses."""

    def test_record_is_written_under_the_named_directory(self):
        target = self._path("records")
        os.mkdir(target)
        written = self.record_council_run(
            self._path("board.json"), self._board(), "PASS", record_dir=target
        )
        self.assertTrue(os.path.isfile(written))
        self.assertEqual(
            os.path.dirname(os.path.abspath(written)), os.path.abspath(target)
        )
        payload = self._read_json(written)
        self.assertEqual(payload["outcome"], "PASS")
        self.assertEqual(payload["wip_limit"], self.wip_limit)

    def test_record_refuses_a_path_that_is_not_a_directory(self):
        with self.assertRaises(ValueError):
            self.record_council_run(
                "board.json",
                self._board(),
                "PASS",
                record_dir=self._path("nope"),
            )

    def test_record_refuses_hostile_arguments(self):
        with self.assertRaises(ValueError):
            self.record_council_run(None, None, None)
        with self.assertRaises(ValueError):
            self.record_council_run("board.json", self._board(), "")
        with self.assertRaises(ValueError):
            self.record_council_run(
                "board.json", self._board(), "PASS", record_dir=7
            )
        with self.assertRaises(ValueError):
            self.record_council_run(
                "board.json", self._board(revision=True), "PASS"
            )


class PublicHelpersRefuseHostileInput(DriverCase):
    """Every public helper refuses a board that is not an object carrying
    readable units, rather than crashing with a raw TypeError."""

    def test_find_violations_refuses_non_objects(self):
        for value in (None, 0, "board", [], 2.5, True):
            with self.assertRaises(ValueError):
                self.find_violations(value)

    def test_repair_board_refuses_non_objects(self):
        for value in (None, 0, "board", [], 2.5, True):
            with self.assertRaises(ValueError):
                self.repair_board(value)


class MissingEngineRefuses(DriverCase):
    """A missing loop engine REFUSES; there is no stand in that approves."""

    def test_a_missing_loop_engine_refuses_instead_of_reporting_done(self):
        namespace = self.run_priority_round.__globals__
        original = namespace["coe_loop"]
        namespace["coe_loop"] = None
        try:
            with self.assertRaises(self.refused):
                self.driver["_run_council_round"](self._board())
        finally:
            namespace["coe_loop"] = original


if __name__ == "__main__":
    unittest.main()
