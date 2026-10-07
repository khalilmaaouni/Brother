#!/usr/bin/env python3
"""An unknown outcome is never scored as a resolved one, in either direction.

WHY THIS FILE. scripts/prediction_ledger.py harvests build outcomes into calibration data that
later decides which predictor on this estate is trustworthy. Two defects were reported against it
and they point OPPOSITE ways:

  READY-UNPROBED scored as a PASS   an unprobed build, which nobody proved, counted as a success
  RUNNING        scored as a FAIL   a build still in flight counted as the predictor being wrong

Measured 2026-09-21 against the file as it stood, one run folder per status word, the first was
already fixed and the second was real and WIDER than reported: RUNNING, WITHHELD, BLOCKED and a
deliberate typo all recorded `fail`, because the harvester ended in a catch-all
`"pass" if head == "READY" else "fail"`.

Opposite directions out of one function is the signature of a MISSING STATE. The module now has a
third outcome, UNRESOLVED, and one table, STATUS_OUTCOME, with no catch-all. These tests pin the
shape rather than the two symptoms: every word that is not a verdict declines, and the two words
that ARE verdicts still produce them, so none of the refusals can be satisfied by a harvester that
refuses everything.

FIXTURE RULE. One condition per fixture. A fixture that trips two guards proves neither, so the
RUNNING case and the unprobed case never share a ledger row, a run folder or a sub unit id.

Run: python3 scripts/test_prediction_ledger_fail_direction.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "prediction_ledger.py")
sys.path.insert(0, HERE)
import prediction_ledger as L  # noqa: E402


def child_env(home):
    """A child process with HOME pointed at the fixture and no inherited git state.

    GIT_ vars are stripped because anything launched from a git hook inherits GIT_DIR, and a
    subprocess that picks it up reads a repository the fixture never created."""
    env = dict(os.environ, HOME=home, PYTHONDONTWRITEBYTECODE="1")
    for k in [k for k in env if k.startswith("GIT_")]:
        env.pop(k)
    return env


class HarvestFailDirection(unittest.TestCase):
    """One status word, one run folder, one prediction, one assertion."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pred-faildir-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(self.home, ".claude", "evidence"))

    def run_dir(self, sub, stamp="120000"):
        d = os.path.join(self.home, ".claude", "evidence", "unit-runs", "%s-%s" % (sub, stamp))
        os.makedirs(d, exist_ok=True)
        return d

    def harvest(self, path):
        """harvest() expands the runs directory from HOME at call time, so HOME is swapped around
        the call itself and always restored."""
        real = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        try:
            return L.harvest(path=path)
        finally:
            if real is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = real

    def outcome(self, sub, status_text):
        """The actual recorded for ONE pending prediction about ONE sub unit, or None if declined.

        Each call gets its own sub unit id and its own ledger file, so no two conditions in this
        class can mask one another."""
        d = self.run_dir(sub)
        if status_text is not None:
            with open(os.path.join(d, "STATUS"), "w", encoding="utf-8") as fh:
                fh.write(status_text)
        path = os.path.join(self.dir, "%s.jsonl" % sub)
        L.predict("grade", sub, "pass", confidence=0.9, question="buildable", path=path, now=1)
        self.harvest(path)
        rows = [r for r in L.load(path) if r.get("kind") == "resolve"]
        return rows[0]["actual"] if rows else None

    # --- the two words that ARE verdicts. Without these the refusals below are satisfied by a
    # --- harvester that resolves nothing at all, which would be a green over a dead function.
    def test_ready_still_resolves_as_a_pass(self):
        self.assertEqual(self.outcome("FDA.1", "READY /b/x.json\n"), "pass")

    def test_exhausted_still_resolves_as_a_fail(self):
        self.assertEqual(self.outcome("FDB.1", "EXHAUSTED after 3 rounds\n"), "fail")

    # --- one unknown per fixture, no two sharing a row
    def test_running_is_not_a_failure(self):
        # the reported symptom A. Still in flight is not yet known
        self.assertIsNone(self.outcome("FDC.1", "RUNNING\n"))

    def test_ready_unprobed_is_not_a_pass(self):
        # the reported symptom B, pointing the other way. Not proven is not proven good
        self.assertIsNone(self.outcome("FDD.1", "READY-UNPROBED /b/x.json\n"))

    def test_withheld_is_neither(self):
        # stopped waiting on a human fact, so the predictor was never actually tested
        self.assertIsNone(self.outcome("FDE.1", "WITHHELD needs a fact\n"))

    def test_blocked_is_neither(self):
        # no eligible model, so no build was ever produced to judge
        self.assertIsNone(self.outcome("FDF.1", "BLOCKED no bridge model is eligible\n"))

    def test_an_unrecognised_word_is_neither(self):
        # THE ROOT PROPERTY. A word no writer on this estate has invented yet must not acquire a
        # verdict from whichever branch happened to be the `else`.
        self.assertIsNone(self.outcome("FDG.1", "SOME-FUTURE-WORD /b/x.json\n"))

    def test_an_empty_status_file_is_neither(self):
        self.assertIsNone(self.outcome("FDH.1", ""))

    def test_a_missing_status_file_is_neither(self):
        self.assertIsNone(self.outcome("FDI.1", None))

    def test_a_truncated_binary_status_is_neither(self):
        # not decodable as text: the read raises UnicodeDecodeError and the row stays pending
        sub = "FDJ.1"
        with open(os.path.join(self.run_dir(sub), "STATUS"), "wb") as fh:
            fh.write(b"\xff\xfe\x00READY")
        path = os.path.join(self.dir, "%s.jsonl" % sub)
        L.predict("grade", sub, "pass", path=path, now=1)
        self.harvest(path)
        self.assertEqual([r for r in L.load(path) if r.get("kind") == "resolve"], [])

    def test_a_duplicated_build_id_resolves_the_same_way_twice(self):
        # Two run folders for one sub unit. The rule is the newest, and on an mtime tie the later
        # basename, which is the same rule brother_pass.newest_status uses. Before the glob was
        # sorted this was os.scandir order, so the same ledger could read READY on one pass and
        # EXHAUSTED on the next with nothing on disk having changed.
        sub = "FDK.1"
        for stamp, text in (("120000", "EXHAUSTED after 3 rounds\n"), ("130000", "READY /b/x.json\n")):
            with open(os.path.join(self.run_dir(sub, stamp), "STATUS"), "w", encoding="utf-8") as fh:
                fh.write(text)
        seen = set()
        for i in range(5):
            path = os.path.join(self.dir, "%s-%d.jsonl" % (sub, i))
            L.predict("grade", sub, "pass", path=path, now=1)
            self.harvest(path)
            seen.add(tuple(r["actual"] for r in L.load(path) if r.get("kind") == "resolve"))
        self.assertEqual(seen, {("pass",)}, "the newest run folder must win every time")

    def test_harvesting_twice_does_not_write_a_second_outcome(self):
        # arrives twice: the second harvest must not reopen a row it already answered
        sub = "FDL.1"
        with open(os.path.join(self.run_dir(sub), "STATUS"), "w", encoding="utf-8") as fh:
            fh.write("READY /b/x.json\n")
        path = os.path.join(self.dir, "%s.jsonl" % sub)
        L.predict("grade", sub, "pass", path=path, now=1)
        self.assertEqual(self.harvest(path), 1)
        self.assertEqual(self.harvest(path), 0, "a resolved row must not be harvested again")
        self.assertEqual(len([r for r in L.load(path) if r.get("kind") == "resolve"]), 1)


class ReportSeparatesResolvedFromUnresolved(unittest.TestCase):
    """Driven through main(), at the process boundary, because the report IS the control.

    A selftest that only calls score() leaves the printing path, which is the thing a human reads a
    number off, untested. The rule on this estate is that a control is tested at its entry point."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pred-report-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(self.home, ".claude", "evidence"))

    def write_ledger(self, rows):
        p = os.path.join(self.home, ".claude", "evidence", "brother-predictions.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        return p

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", LEDGER] + list(args),
                              cwd=self.dir, env=child_env(self.home),
                              capture_output=True, text=True, timeout=120)

    def test_an_empty_ledger_is_no_data_and_not_a_zero(self):
        r = self.cli("score")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("NO-DATA", r.stdout)

    def test_resolved_and_unresolved_are_printed_as_separate_columns(self):
        # four predictions about one predictor: 1 right, 1 wrong, 1 never answerable, 1 not yet
        # answered. A single n with an accuracy beside it would hide three different things.
        rows = []
        for i, (ans, act) in enumerate((("pass", "pass"), ("pass", "fail"),
                                        ("pass", "unresolvable"), ("pass", None))):
            pid = "grade:S%d:-:-" % i
            rows.append({"at": 2 * i + 1, "kind": "predict", "id": pid, "predictor": "grade",
                         "subject": "S%d" % i, "question": None, "answer": ans, "confidence": None})
            if act is not None:
                rows.append({"at": 2 * i + 2, "kind": "resolve", "id": pid, "actual": act,
                             "source": "fixture"})
        self.write_ledger(rows)
        r = self.cli("score")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # THE HEADER LINE SPECIFICALLY, not `assertIn` over the whole page. A mutation that renamed
        # the column header survived the first version of this assertion, because the explanatory
        # paragraph under the table also contains the word "unresolved". A guard masked by unrelated
        # text on the same page proves nothing.
        header = [l for l in r.stdout.splitlines() if l.startswith("predictor")][0]
        self.assertEqual(header.split(),
                         ["predictor", "n", "resolved", "unresolved", "pending", "coverage",
                          "base", "brier", "skill", "trustworthy"], header)
        line = [l for l in r.stdout.splitlines() if l.startswith("grade")][0]
        # n=4 resolved=2 unresolved=1 pending=1, and the counters must partition n
        self.assertEqual(line.split()[1:5], ["4", "2", "1", "1"], line)
        # coverage is resolved over the KNOWABLE rows (4 - 1 unresolvable = 3), never 2/4
        self.assertIn("67%", line, line)


class TheSelftestCanActuallyGoRed(unittest.TestCase):
    """The module's own --selftest must exit NONZERO when a case fails.

    WHY THIS IS HERE. The estate's registered mutation sweep flipped `return 1 if bad else 0` to
    `return 0 if bad else 0` in selftest() and every check stayed green: the selftest printed its
    failures and exited 0 anyway, so anything running it as a done check would have read a pass off
    a broken module. A check that cannot go red is not evidence, and nothing was asking whether this
    one could. This asks, at the process boundary, by running a deliberately broken COPY."""

    def test_a_broken_copy_exits_nonzero_and_says_failed(self):
        d = tempfile.mkdtemp(prefix="pred-canfail-")
        self.addCleanup(shutil.rmtree, d, True)
        broken = os.path.join(d, "prediction_ledger.py")
        src = io.open(LEDGER, encoding="utf-8").read()
        # one surgical break: the table's default becomes a verdict again, which is the exact defect
        # this module was repaired for, so several selftest cases must go red
        old = 'return STATUS_OUTCOME.get(head.strip(), UNRESOLVED)'
        self.assertEqual(src.count(old), 1, "the mutation anchor moved; this test is now blind")
        io.open(broken, "w", encoding="utf-8").write(src.replace(old,
                'return STATUS_OUTCOME.get(head.strip(), FAIL)'))
        r = subprocess.run([sys.executable, "-B", broken, "--selftest"], cwd=d,
                           env=child_env(d), capture_output=True, text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0, "a failing selftest exited 0: " + r.stdout + r.stderr)
        self.assertIn("FAILED", r.stdout, r.stdout)

    def test_the_unbroken_module_exits_zero(self):
        # the refusal above must not be satisfied by a selftest that always fails
        r = subprocess.run([sys.executable, "-B", LEDGER, "--selftest"], cwd=HERE,
                           env=child_env(os.path.expanduser("~")), capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stdout)


class LoadDropsWhatWouldMisScore(unittest.TestCase):
    def test_a_predict_row_with_no_predictor_is_dropped_not_crashed_on(self):
        d = tempfile.mkdtemp(prefix="pred-load-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "p.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": 1, "kind": "predict", "id": "x", "answer": "pass"}) + "\n")
        self.assertEqual(L.load(p), [])
        self.assertEqual(L.score(L.load(p)), {})

    def test_a_row_stamped_at_epoch_zero_keeps_that_time(self):
        # epoch 0 is falsy, and a falsy timestamp read as "missing" has already produced a real
        # defect in a sibling module on this estate. predict() must test `now is not None`.
        d = tempfile.mkdtemp(prefix="pred-epoch-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "p.jsonl")
        L.predict("jev", "E", "pass", path=p, now=0)
        self.assertEqual(L.load(p)[0]["at"], 0)



class ALostWriteIsNeverRecorded(unittest.TestCase):
    """2026-09-30: _append swallowed OSError and returned the row, so a lost prediction read as recorded."""

    def test_predict_into_a_ledger_that_cannot_be_written_raises_write_failed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            os.makedirs(path)  # the ledger path is a directory: the append open fails, nothing else does
            with self.assertRaises(L.LedgerError) as caught:
                L.predict("grade", "U1", "pass", path=path, now=1)
            self.assertEqual(caught.exception.code, "write_failed")

if __name__ == "__main__":
    unittest.main()
