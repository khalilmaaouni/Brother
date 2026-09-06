#!/usr/bin/env python3
"""Tests for the JBEQ-MDM mutation seam: fail loud, fail closed.

WHAT THIS DRIVES BACKWARDS (hub PR 386 security finding, 2026-09-06):
JBEQ_DECIDE_DISABLE_RULES in scripts/jbeq_decide.py is a fail-open test
hook the directive's own mutation tests need (section 20): any process
that sets it silently runs the MDM decision engine with safety rules off,
and nothing downstream could tell a mutated run from a real one. This file
proves the fix on the four fronts the fix directive names:

  * LOUD: decide() marks every result "mutation": {"disabled": [...]} with
    a "why" prefixed "MUTATION SEAM ACTIVE (rules disabled: ...): " while a
    seam is active, and carries neither key otherwise; the decide CLI
    prints one stderr banner per run, and the same marker rides along on
    every decisions.jsonl row and on the answers.json file itself.
  * CLOSED AT THE SCORER: scripts/jbeq_mdm.py score refuses (exit 3) an
    answers file carrying the marker, whether the marker lives in the
    file itself or in a decisions.jsonl beside it, unless --mutation-report
    is given, in which case it scores anyway under a "MUTATION REPORT, not
    a record" header and writes no file either way.
  * CLOSED AT THE RECORD: both jbeq_decide.py decide and jbeq_mdm.py
    extract refuse (exit 3, naming the seam) to write into
    benchmarks/jbeq/mdm/runs/ while a seam is active, before touching
    disk, so a mutated run can never become a committed record by
    accident. A control test proves the SAME destination works fine with
    no seam active, so the refusal is about the seam, not the path.

The finer-grained per-rule mutation proofs (each safety rule breaks its
own protected case when disabled) live in scripts/test_jbeq_decide.py's
TestPerRuleMutation, which now also asserts the marker on every one of its
calls; this file does not repeat that per-rule ground.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jbeq_decide  # noqa: E402
import jbeq_mdm  # noqa: E402
from test_jbeq_decide import sheet  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECIDE_SCRIPT = os.path.join(REPO, "scripts", "jbeq_decide.py")
MDM_SCRIPT = os.path.join(REPO, "scripts", "jbeq_mdm.py")
RUNS_DIR = os.path.join(REPO, "benchmarks", "jbeq", "mdm", "runs")
SEED_PATH = os.path.join(REPO, "benchmarks", "jbeq", "mdm", "seed-2026-09-05.json")


def _set_seam(value):
    old = os.environ.get("JBEQ_DECIDE_DISABLE_RULES")
    if value is None:
        os.environ.pop("JBEQ_DECIDE_DISABLE_RULES", None)
    else:
        os.environ["JBEQ_DECIDE_DISABLE_RULES"] = value
    return old


def _restore_seam(old):
    if old is None:
        os.environ.pop("JBEQ_DECIDE_DISABLE_RULES", None)
    else:
        os.environ["JBEQ_DECIDE_DISABLE_RULES"] = old


class TestMarkerInProcess(unittest.TestCase):
    """decide() itself: the marker appears when a rule is disabled and is
    absent otherwise."""

    def test_marker_absent_when_no_rule_disabled(self):
        old = _set_seam(None)
        try:
            result = jbeq_decide.decide(sheet())
        finally:
            _restore_seam(old)
        self.assertNotIn("mutation", result)
        self.assertFalse(result["why"].startswith("MUTATION SEAM ACTIVE"))

    def test_marker_present_when_one_rule_is_disabled(self):
        s = sheet(tenant_boundary="stated", evidence_strength="strong")
        baseline = jbeq_decide.decide(s)
        self.assertEqual(baseline["answer"], "REJECT MATCH")
        self.assertNotIn("mutation", baseline)

        old = _set_seam("C")
        try:
            result = jbeq_decide.decide(s)
        finally:
            _restore_seam(old)
        self.assertNotEqual(result["answer"], "REJECT MATCH",
                            "disabling rule C must flip the answer, or this "
                            "test proves nothing about a real mutation")
        self.assertEqual(result["mutation"], {"disabled": ["C"]})
        self.assertTrue(
            result["why"].startswith("MUTATION SEAM ACTIVE (rules disabled: C): ")
        )

    def test_marker_present_under_the_bare_disable_everything_sentinel(self):
        old = _set_seam("1")
        try:
            result = jbeq_decide.decide(sheet())
        finally:
            _restore_seam(old)
        self.assertEqual(result["answer"], "NO-DATA")
        self.assertEqual(result["rule_fired"], "rules-disabled-for-test")
        self.assertEqual(result["mutation"], {"disabled": ["*"]})
        self.assertTrue(
            result["why"].startswith("MUTATION SEAM ACTIVE (rules disabled: *): ")
        )


class TestCLIBannerAndRecordFields(unittest.TestCase):
    """decide's CLI: one stderr banner per run, and the marker rides along
    on decisions.jsonl rows and the answers.json file itself."""

    def _run_decide(self, sheets, out_path, decisions_path, seam):
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump(sheets, fh)
            env = dict(os.environ)
            if seam is None:
                env.pop("JBEQ_DECIDE_DISABLE_RULES", None)
            else:
                env["JBEQ_DECIDE_DISABLE_RULES"] = seam
            return subprocess.run(
                [sys.executable, DECIDE_SCRIPT, "decide", sheets_path,
                 "--out", out_path, "--decisions", decisions_path],
                capture_output=True, text=True, env=env,
            )

    def test_seam_active_prints_banner_and_marks_every_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "answers.json")
            decisions_path = os.path.join(tmp, "decisions.jsonl")
            proc = self._run_decide({"X-01": sheet()}, out_path, decisions_path, "C")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("MUTATION SEAM ACTIVE (rules disabled: C)", proc.stderr)

            with open(out_path, encoding="utf-8") as fh:
                answers = json.load(fh)
            self.assertEqual(answers.get("_mutation"), {"disabled": ["C"]})

            with open(decisions_path, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].get("mutation"), {"disabled": ["C"]})
            self.assertTrue(rows[0]["why"].startswith(
                "MUTATION SEAM ACTIVE (rules disabled: C): "
            ))

    def test_no_seam_prints_no_banner_and_marks_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "answers.json")
            decisions_path = os.path.join(tmp, "decisions.jsonl")
            proc = self._run_decide({"X-01": sheet()}, out_path, decisions_path, None)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("MUTATION SEAM ACTIVE", proc.stderr)

            with open(out_path, encoding="utf-8") as fh:
                answers = json.load(fh)
            self.assertNotIn("_mutation", answers)

            with open(decisions_path, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            self.assertNotIn("mutation", rows[0])


class TestScorerRefusesAMutatedRecord(unittest.TestCase):
    """Closed at the scorer: a marked answers file refuses; an unmarked one
    scores normally; --mutation-report scores anyway and writes nothing."""

    def setUp(self):
        with open(SEED_PATH, encoding="utf-8") as fh:
            self.seed = json.load(fh)
        self.first = self.seed["cases"][0]

    def _run_score(self, answers, extra_args=(), decisions_rows=None):
        with tempfile.TemporaryDirectory() as tmp:
            answers_path = os.path.join(tmp, "answers.json")
            with open(answers_path, "w", encoding="utf-8") as fh:
                json.dump(answers, fh)
            if decisions_rows is not None:
                decisions_path = os.path.join(tmp, "decisions.jsonl")
                with open(decisions_path, "w", encoding="utf-8") as fh:
                    for row in decisions_rows:
                        fh.write(json.dumps(row) + "\n")
            before = set(os.listdir(tmp))
            proc = subprocess.run(
                [sys.executable, MDM_SCRIPT, "score", answers_path] + list(extra_args),
                capture_output=True, text=True,
            )
            after = set(os.listdir(tmp))
            return proc, before, after

    def test_refuses_a_marked_answers_file(self):
        answers = {self.first["id"]: self.first["expected"],
                   "_mutation": {"disabled": ["C"]}}
        proc, before, after = self._run_score(answers)
        self.assertEqual(proc.returncode, jbeq_mdm.EXIT_NODATA,
                         proc.stdout + proc.stderr)
        self.assertIn("REFUSED", proc.stdout + proc.stderr)
        self.assertIn("mutation seam", proc.stdout + proc.stderr)
        self.assertEqual(before, after, "a refused run writes no file")

    def test_refuses_when_only_the_decisions_file_beside_it_carries_the_marker(self):
        answers = {self.first["id"]: self.first["expected"]}
        decisions_rows = [{
            "case_id": self.first["id"], "answer": self.first["expected"],
            "rule_fired": "C",
            "why": "MUTATION SEAM ACTIVE (rules disabled: C): x",
            "mutation": {"disabled": ["C"]},
        }]
        proc, before, after = self._run_score(answers, decisions_rows=decisions_rows)
        self.assertEqual(proc.returncode, jbeq_mdm.EXIT_NODATA,
                         proc.stdout + proc.stderr)
        self.assertIn("REFUSED", proc.stdout + proc.stderr)

    def test_accepts_an_unmarked_answers_file(self):
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        proc, _, _ = self._run_score(answers)
        self.assertNotEqual(proc.returncode, jbeq_mdm.EXIT_NODATA,
                            proc.stdout + proc.stderr)
        self.assertNotIn("REFUSED", proc.stdout)
        self.assertIn("JBEQ-MDM SEED: %d of %d"
                      % (len(self.seed["cases"]), len(self.seed["cases"])),
                      proc.stdout)

    def test_mutation_report_scores_anyway_and_writes_no_file(self):
        answers = {self.first["id"]: self.first["expected"],
                   "_mutation": {"disabled": ["C"]}}
        proc, before, after = self._run_score(answers, extra_args=["--mutation-report"])
        self.assertNotEqual(proc.returncode, jbeq_mdm.EXIT_NODATA,
                            proc.stdout + proc.stderr)
        self.assertIn("MUTATION REPORT, not a record", proc.stdout)
        self.assertIn("rules disabled: C", proc.stdout)
        self.assertEqual(before, after, "--mutation-report must write no record file")

    def test_mutation_report_is_inert_on_an_unmarked_file(self):
        answers = {c["id"]: c["expected"] for c in self.seed["cases"]}
        proc, _, _ = self._run_score(answers, extra_args=["--mutation-report"])
        self.assertNotIn("MUTATION REPORT", proc.stdout)
        self.assertIn("JBEQ-MDM SEED: %d of %d"
                      % (len(self.seed["cases"]), len(self.seed["cases"])),
                      proc.stdout)


class TestRunsDirectoryRefusal(unittest.TestCase):
    """Closed at the record: decide and extract both refuse to write into
    benchmarks/jbeq/mdm/runs/ while a seam is active, before touching
    disk. Every leaf name here is unique to this test file so a run never
    collides with a real committed record."""

    def _leaf(self, name):
        return os.path.join(RUNS_DIR, name)

    def test_decide_refuses_an_out_path_under_runs_dir(self):
        target_dir = self._leaf("mutation-seam-refusal-decide-out")
        self.assertFalse(os.path.exists(target_dir))
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump({"X-01": sheet()}, fh)
            env = dict(os.environ)
            env["JBEQ_DECIDE_DISABLE_RULES"] = "C"
            proc = subprocess.run(
                [sys.executable, DECIDE_SCRIPT, "decide", sheets_path,
                 "--out", os.path.join(target_dir, "answers.json"),
                 "--decisions", os.path.join(tmp, "decisions.jsonl")],
                capture_output=True, text=True, env=env,
            )
        self.assertEqual(proc.returncode, jbeq_decide.EXIT_NODATA, proc.stderr)
        self.assertIn("REFUSED", proc.stderr)
        self.assertIn("runs/", proc.stderr)
        self.assertFalse(os.path.exists(target_dir),
                         "the refusal must fire before any write")

    def test_decide_refuses_a_decisions_path_under_runs_dir(self):
        target_dir = self._leaf("mutation-seam-refusal-decide-decisions")
        self.assertFalse(os.path.exists(target_dir))
        with tempfile.TemporaryDirectory() as tmp:
            sheets_path = os.path.join(tmp, "fact-sheets.json")
            with open(sheets_path, "w", encoding="utf-8") as fh:
                json.dump({"X-01": sheet()}, fh)
            out_path = os.path.join(tmp, "answers.json")
            env = dict(os.environ)
            env["JBEQ_DECIDE_DISABLE_RULES"] = "C"
            proc = subprocess.run(
                [sys.executable, DECIDE_SCRIPT, "decide", sheets_path,
                 "--out", out_path,
                 "--decisions", os.path.join(target_dir, "decisions.jsonl")],
                capture_output=True, text=True, env=env,
            )
            self.assertFalse(os.path.exists(out_path),
                             "neither output is written once either target is under runs/")
        self.assertEqual(proc.returncode, jbeq_decide.EXIT_NODATA, proc.stderr)
        self.assertIn("REFUSED", proc.stderr)
        self.assertFalse(os.path.exists(target_dir))

    def test_decide_writes_normally_under_runs_dir_with_no_seam_active(self):
        # Control: the refusal is about the seam, never about the
        # destination. Cleans up the directory it creates.
        target_dir = self._leaf("mutation-seam-control-no-seam")
        self.assertFalse(os.path.exists(target_dir))
        os.makedirs(target_dir)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                sheets_path = os.path.join(tmp, "fact-sheets.json")
                with open(sheets_path, "w", encoding="utf-8") as fh:
                    json.dump({"X-01": sheet()}, fh)
                env = dict(os.environ)
                env.pop("JBEQ_DECIDE_DISABLE_RULES", None)
                proc = subprocess.run(
                    [sys.executable, DECIDE_SCRIPT, "decide", sheets_path,
                     "--out", os.path.join(target_dir, "answers.json"),
                     "--decisions", os.path.join(target_dir, "decisions.jsonl")],
                    capture_output=True, text=True, env=env,
                )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(os.path.exists(os.path.join(target_dir, "answers.json")))
        finally:
            shutil.rmtree(target_dir, ignore_errors=True)

    def test_extract_refuses_an_out_dir_under_runs_dir_when_seam_active(self):
        target_dir = self._leaf("mutation-seam-refusal-extract")
        self.assertFalse(os.path.exists(target_dir))
        env = dict(os.environ)
        env["JBEQ_DECIDE_DISABLE_RULES"] = "C"
        proc = subprocess.run(
            [sys.executable, MDM_SCRIPT, "extract", "--model-cmd", "true",
             "--out", target_dir],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, jbeq_mdm.EXIT_NODATA, proc.stderr)
        self.assertIn("REFUSED", proc.stderr)
        self.assertFalse(os.path.exists(target_dir),
                         "the refusal must fire before any model call or write")


if __name__ == "__main__":
    unittest.main()
