"""What scripts/lesson_repeat_trial.py must keep true.

Mirrors scripts/test_repeat_control.py and scripts/test_e53_lesson_ab.py:
unittest, tempfile fixtures in the real shapes, subprocess against the real
script so its argparse and its exit codes are proven too. Nothing here touches
the real evidence store, the real lesson store, or the real tree.

The three claims the trial rests on are driven directly:
  * the counting: a fixture log of twelve commands, four of them repeating a
    recorded lesson's failure, reads 4 of 4 in arm A;
  * the recorded_at ordering: the same log against a store whose lessons were
    all written the day AFTER reads 0 of 4, so a lesson cannot be credited to a
    command that ran before it existed;
  * the empty-store arm: 0 of n, and never a crash or a NO-DATA.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lesson_repeat_trial as T  # noqa: E402

SCRIPT = os.path.join(HERE, "lesson_repeat_trial.py")

#: Twelve commands. Four of them (F1 to F4) fail in a way one of the fixture
#: lessons below describes; four fail in a way none of them describes; four
#: succeed. So the described-failure population is 4, not 8 and not 12.
LOG_ROWS = [
    {"command": "sh gate.sh 2>&1 | tail -4; echo $?", "exit_code": 1, "at": "2026-08-20"},
    {"command": "git reset --hard HEAD~1", "exit_code": 1, "at": "2026-08-21"},
    {"command": "ps -ef | grep watchdog | wc -l", "exit_code": 2, "at": "2026-08-22"},
    {"command": "grep -c DONE claims.json || echo 0", "exit_code": 1, "at": "2026-08-23"},
    {"command": "python3 build.py --release", "exit_code": 1, "at": "2026-08-20"},
    {"command": "make install", "exit_code": 2, "at": "2026-08-21"},
    {"command": "curl https://example.invalid", "exit_code": 6, "at": "2026-08-22"},
    {"command": "cat missing-file.txt", "exit_code": 1, "at": "2026-08-23"},
    {"command": "echo ok", "exit_code": 0, "at": "2026-08-20"},
    {"command": "git reset --hard HEAD~1", "exit_code": 0, "at": "2026-08-21"},
    {"command": "ls -la", "exit_code": 0, "at": "2026-08-22"},
    {"command": "python3 -m unittest", "exit_code": 0, "at": "2026-08-23"},
]

TRIGGERS = ["| tail", "reset --hard", "ps -", "grep -c"]


def write_log(path, rows=None):
    with open(path, "w", encoding="utf-8") as fh:
        for row in (LOG_ROWS if rows is None else rows):
            fh.write(json.dumps(row) + "\n")
    return path


def write_lessons(path, recorded):
    with open(path, "w", encoding="utf-8") as fh:
        for i, trig in enumerate(TRIGGERS):
            fh.write(json.dumps({"trigger": trig, "recorded": recorded,
                                 "note": "fixture lesson %d" % i}) + "\n")
    return path


class Counting(unittest.TestCase):
    def test_four_of_the_twelve_failures_are_described_by_a_lesson(self):
        lessons = [{"id": "L01", "trigger": t, "recorded": "2026-08-01",
                    "recorded_date": T.datetime.date(2026, 8, 1)} for t in TRIGGERS]
        rows = T.replay(LOG_ROWS, lessons)
        self.assertEqual(len(rows), 4, [r["command"] for r in rows])
        self.assertEqual(sum(1 for r in rows if r["lesson"]), 4)

    def test_a_success_is_never_counted_even_when_a_lesson_describes_it(self):
        lessons = [{"id": "L01", "trigger": "reset --hard", "recorded": "2026-08-01",
                    "recorded_date": T.datetime.date(2026, 8, 1)}]
        rows = T.replay(LOG_ROWS, lessons)
        self.assertEqual([r["exit_code"] for r in rows], [1])

    def test_failed_reads_the_exit_code_and_nothing_else(self):
        self.assertTrue(T.failed({"exit_code": 1}))
        self.assertTrue(T.failed({"exit_code": -9}))
        self.assertFalse(T.failed({"exit_code": 0}))
        self.assertFalse(T.failed({}))
        self.assertFalse(T.failed({"exit_code": None}))


class RecordedAtOrdering(unittest.TestCase):
    def test_a_lesson_written_after_the_command_never_counts_as_shown(self):
        late = [{"id": "L01", "trigger": t, "recorded": "2026-09-01",
                 "recorded_date": T.datetime.date(2026, 9, 1)} for t in TRIGGERS]
        rows = T.replay(LOG_ROWS, late)
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(1 for r in rows if r["lesson"]), 0)
        self.assertTrue(all(r["described_by"] for r in rows))

    def test_a_lesson_written_on_the_same_day_counts(self):
        same = [{"id": "L01", "trigger": "reset --hard", "recorded": "2026-08-21",
                 "recorded_date": T.datetime.date(2026, 8, 21)}]
        rows = T.replay(LOG_ROWS, same)
        self.assertEqual([r["lesson"] for r in rows], ["L01"])

    def test_an_undated_lesson_is_read_as_silent_not_as_older(self):
        undated = [{"id": "L01", "trigger": "reset --hard", "recorded": None,
                    "recorded_date": None}]
        rows = T.replay(LOG_ROWS, undated)
        self.assertEqual([r["lesson"] for r in rows], [None])

    def test_read_lessons_parses_a_date_and_refuses_a_broken_one(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lessons.jsonl")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"trigger": "A", "recorded": "2026-08-01"}) + "\n")
                fh.write(json.dumps({"trigger": "B", "recorded": "not a date"}) + "\n")
                fh.write(json.dumps({"trigger": "", "recorded": "2026-08-01"}) + "\n")
                fh.write("{ torn line\n")
            got = T.read_lessons(p)
            self.assertEqual([l["trigger"] for l in got], ["a", "b"])
            self.assertEqual(got[0]["recorded_date"], T.datetime.date(2026, 8, 1))
            self.assertIsNone(got[1]["recorded_date"])


class EmptyStoreArm(unittest.TestCase):
    def test_the_empty_store_arm_reads_zero_and_has_no_population(self):
        self.assertEqual(T.replay(LOG_ROWS, []), [])

    def test_the_printed_comparison_reads_four_and_zero(self):
        with tempfile.TemporaryDirectory() as d:
            log = write_log(os.path.join(d, "log.jsonl"))
            lessons = write_lessons(os.path.join(d, "lessons.jsonl"), "2026-08-01")
            out = os.path.join(d, "result.json")
            p = subprocess.run([sys.executable, SCRIPT, "trial", "--log", log,
                                "--lessons", lessons, "--out", out],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("shown before the repeat: 4 of 4 failures (A) versus 0 of 4 (B)",
                          p.stdout)
            with open(out, encoding="utf-8") as fh:
                rec = json.load(fh)
            self.assertEqual(rec["arm_a_shown"], 4)
            self.assertEqual(rec["arm_b_shown"], 0)
            self.assertEqual(rec["described_failures"], 4)
            self.assertEqual(rec["failures"], 8)
            self.assertEqual(rec["commands"], 12)

    def test_lessons_written_after_read_zero_of_four_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            log = write_log(os.path.join(d, "log.jsonl"))
            lessons = write_lessons(os.path.join(d, "lessons.jsonl"), "2026-09-01")
            p = subprocess.run([sys.executable, SCRIPT, "trial", "--log", log,
                                "--lessons", lessons,
                                "--out", os.path.join(d, "r.json")],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("shown before the repeat: 0 of 4 failures (A) versus 0 of 4 (B)",
                          p.stdout)
            self.assertIn("SILENT", p.stdout)


class NoData(unittest.TestCase):
    def test_an_absent_lesson_store_is_no_data_not_a_zero_arm(self):
        with tempfile.TemporaryDirectory() as d:
            log = write_log(os.path.join(d, "log.jsonl"))
            p = subprocess.run([sys.executable, SCRIPT, "trial", "--log", log,
                                "--lessons", os.path.join(d, "absent.jsonl")],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn("NO-DATA: no lesson store at", p.stdout)

    def test_failures_no_lesson_describes_are_no_data(self):
        with tempfile.TemporaryDirectory() as d:
            log = write_log(os.path.join(d, "log.jsonl"),
                            [{"command": "make install", "exit_code": 2,
                              "at": "2026-08-21"}])
            lessons = write_lessons(os.path.join(d, "lessons.jsonl"), "2026-08-01")
            p = subprocess.run([sys.executable, SCRIPT, "trial", "--log", log,
                                "--lessons", lessons],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn("there is nothing to compare", p.stdout)

    def test_an_absent_evidence_store_is_no_data(self):
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.run([sys.executable, SCRIPT, "log",
                                "--store", os.path.join(d, "absent"),
                                "--out", os.path.join(d, "log.jsonl")],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn("NO-DATA: no evidence store at", p.stdout)

    def test_an_empty_evidence_store_is_no_data(self):
        with tempfile.TemporaryDirectory() as d:
            store = os.path.join(d, "evidence")
            os.makedirs(store)
            with open(os.path.join(store, "not-a-capture.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("just some notes\n")
            p = subprocess.run([sys.executable, SCRIPT, "log", "--store", store,
                                "--out", os.path.join(d, "log.jsonl")],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn("holds no run_evidence capture", p.stdout)


class CaptureReader(unittest.TestCase):
    def _capture(self, d, name, body):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        return p

    def test_a_real_capture_header_is_read_into_a_row(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._capture(d, "1787997084-92065-sh--c-exit-7.txt",
                              "$ sh -c exit 7\n[exit 7 after 0.0s]\n\n--- stdout ---\n")
            row = T.read_capture(p)
            self.assertEqual(row["command"], "sh -c exit 7")
            self.assertEqual(row["exit_code"], 7)
            self.assertEqual(row["at"],
                             T.datetime.date.fromtimestamp(1787997084).isoformat())

    def test_a_file_that_is_not_a_capture_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._capture(d, "1787997084-1-notes.txt", "hello\nworld\n")
            self.assertIsNone(T.read_capture(p))

    def test_the_log_verb_writes_the_rows_it_counts(self):
        with tempfile.TemporaryDirectory() as d:
            store = os.path.join(d, "evidence")
            os.makedirs(store)
            self._capture(store, "1787997084-1-a.txt", "$ true\n[exit 0 after 0.0s]\n")
            self._capture(store, "1787997085-2-b.txt", "$ false\n[exit 1 after 0.0s]\n")
            out = os.path.join(d, "log.jsonl")
            p = subprocess.run([sys.executable, SCRIPT, "log", "--store", store,
                                "--out", out], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("log: 2 command(s), 1 failure(s)", p.stdout)
            self.assertEqual(len(T.read_log(out)), 2)


class Cli(unittest.TestCase):
    def test_help_names_both_verbs(self):
        p = subprocess.run([sys.executable, SCRIPT, "--help"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("log", p.stdout)
        self.assertIn("trial", p.stdout)

    def test_no_verb_is_refused(self):
        p = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
