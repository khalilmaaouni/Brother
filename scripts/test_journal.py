#!/usr/bin/env python3
"""journal.py, driven as its callers drive it and backwards where it matters:
the append really is atomic against another PROCESS appending at the same
time, the id scheme carries what a chain is rebuilt from, a torn line is
skipped rather than fatal, a missing journal reads NO-DATA rather than an
empty run, and a payload too big for the atomicity bound still lands as one
line.

No network, no engine: this drives the module directly, the same way
test_claim_store.py drives the claim store.
"""
import json
import os
import subprocess
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import journal  # noqa: E402


class AppendWritesWholeLines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="journal-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_two_appends_are_two_readable_events_in_order(self):
        first = journal.append(self.tmp, "run.opened", payload={"a": 1})
        second = journal.append(self.tmp, "unit.done", parent_ids=[first],
                                unit_id="U1")
        rows = journal.read(self.tmp)
        self.assertEqual([r["type"] for r in rows],
                         ["run.opened", "unit.done"])
        self.assertEqual(rows[0]["event_id"], first)
        self.assertEqual(rows[1]["parent_ids"], [first])
        self.assertEqual(rows[1]["unit_id"], "U1")
        self.assertEqual(rows[1]["event_id"], second)

    def test_four_processes_appending_at_once_leave_no_torn_line(self):
        """THE ATOMICITY CLAIM, driven the only way it can be: four separate
        PROCESSES appending to one file at the same time. A thread test would
        prove nothing about O_APPEND, and this file's whole reason for
        keeping a line under PIPE_BUF is that two concurrent writers must
        interleave whole lines or the log is unreadable."""
        script = os.path.join(self.tmp, "appender.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("import sys\n"
                     "sys.path.insert(0, %r)\n"
                     "import journal\n"
                     "for i in range(40):\n"
                     "    journal.append(sys.argv[1], 'attempt.traced',\n"
                     "                   unit_id=sys.argv[2],\n"
                     "                   payload={'i': i, 'pad': 'x' * 60})\n"
                     % HERE)
        procs = [subprocess.Popen([sys.executable, script, self.tmp,
                                   "U%d" % n]) for n in range(4)]
        for proc in procs:
            self.assertEqual(proc.wait(), 0)
        with open(os.path.join(self.tmp, journal.JOURNAL_FILENAME),
                  encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 160)
        for line in lines:
            json.loads(line)  # raises here if any line was torn
        ids = {json.loads(line)["event_id"] for line in lines}
        self.assertEqual(len(ids), 160)


class TheIdScheme(unittest.TestCase):
    def setUp(self):
        self.run_dir = os.path.join(tempfile.mkdtemp(prefix="journal-"),
                                    "20260903T190800-a-run")
        os.makedirs(self.run_dir)
        self.addCleanup(shutil.rmtree, os.path.dirname(self.run_dir),
                        ignore_errors=True)

    def test_every_event_carries_the_eight_fields(self):
        journal.append(self.run_dir, "claim.acquired", unit_id="U1",
                       session_id="S9", payload={"owner": "brother-run-1"})
        row = journal.read(self.run_dir)[0]
        self.assertEqual(sorted(row), ["at", "event_id", "parent_ids",
                                       "payload", "run_id", "session_id",
                                       "type", "unit_id"])
        self.assertRegex(row["event_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(row["run_id"], "20260903T190800-a-run")
        self.assertEqual(row["session_id"], "S9")
        self.assertEqual(row["parent_ids"], [])
        self.assertRegex(row["at"], r"^\d{4}-\d{2}-\d{2}T.*\+00:00$")

    def test_previous_is_the_last_event_this_process_wrote(self):
        self.assertEqual(journal.previous(self.run_dir), ())
        first = journal.append(self.run_dir, "run.opened")
        self.assertEqual(journal.previous(self.run_dir), (first,))
        second = journal.append(self.run_dir, "dispatch.round",
                                parent_ids=journal.previous(self.run_dir))
        self.assertEqual(journal.read(self.run_dir)[1]["parent_ids"], [first])
        self.assertEqual(journal.previous(self.run_dir), (second,))

    def test_a_failed_append_returns_none_and_never_raises(self):
        """The availability rule, driven backwards: a run directory that is
        not a directory at all must cost one stderr line, not the run."""
        wall = os.path.join(self.run_dir, "not-a-dir")
        with open(wall, "w", encoding="utf-8") as fh:
            fh.write("x")
        self.assertIsNone(journal.append(os.path.join(wall, "deeper"),
                                         "run.opened"))


class ATornLineIsSkipped(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="journal-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_half_written_last_line_never_hides_the_whole_journal(self):
        journal.append(self.tmp, "run.opened")
        journal.append(self.tmp, "unit.done", unit_id="U1")
        path = os.path.join(self.tmp, journal.JOURNAL_FILENAME)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('{"event_id": "abc", "type": "unit.d')  # a crash mid-append
        rows = journal.read(self.tmp)
        self.assertEqual([r["type"] for r in rows], ["run.opened", "unit.done"])


class NoJournalIsNoData(unittest.TestCase):
    def test_a_missing_run_directory_reads_none_never_an_exception(self):
        self.assertIsNone(journal.read("/no/such/run/dir/anywhere"))

    def test_a_run_directory_with_no_journal_reads_none(self):
        tmp = tempfile.mkdtemp(prefix="journal-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertIsNone(journal.read(tmp))

    def test_an_empty_journal_reads_a_measured_empty_not_none(self):
        """The difference this estate refuses to fold: a file that exists and
        holds nothing was measured, a file that does not exist was not."""
        tmp = tempfile.mkdtemp(prefix="journal-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with open(os.path.join(tmp, journal.JOURNAL_FILENAME), "w",
                  encoding="utf-8"):
            pass
        self.assertEqual(journal.read(tmp), [])

    def test_no_run_directory_writes_nothing_and_returns_none(self):
        self.assertIsNone(journal.append("", "run.opened"))
        self.assertIsNone(journal.append(None, "run.opened"))


class ALongPayloadStaysOneLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="journal-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_payload_over_the_bound_is_truncated_with_its_marker(self):
        journal.append(self.tmp, "attempt.traced", unit_id="U1",
                       payload={"output": "z" * 20000})
        with open(os.path.join(self.tmp, journal.JOURNAL_FILENAME),
                  "rb") as fh:
            raw = fh.read()
        self.assertEqual(raw.count(b"\n"), 1)
        self.assertLessEqual(len(raw), journal.MAX_LINE_BYTES)
        row = json.loads(raw)
        self.assertEqual(row["type"], "attempt.traced")
        self.assertEqual(row["unit_id"], "U1")
        self.assertIn("payload_truncated", row["payload"])
        self.assertIn(journal.NODATA, row["payload"]["payload_truncated"])

    def test_a_payload_under_the_bound_is_kept_whole(self):
        journal.append(self.tmp, "unit.done", payload={"reason": "ok"})
        row = journal.read(self.tmp)[0]
        self.assertEqual(row["payload"], {"reason": "ok"})


class TheRunDirectoryComesFromTheEnvironmentWhenACallerHasNone(
        unittest.TestCase):
    """integrate.py, worktree_lane.py and receipt_door.py's read-time
    projection hold no run directory of their own; brother_run exports one,
    and outside a run they must find none rather than guess."""

    def test_the_env_var_is_read_at_call_time(self):
        self.assertEqual(journal.run_dir_from_env({}), "")
        self.assertEqual(
            journal.run_dir_from_env({journal.RUN_DIR_ENV_VAR: " /a/run "}),
            "/a/run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
