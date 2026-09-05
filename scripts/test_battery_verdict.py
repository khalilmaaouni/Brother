"""test_battery_verdict.py: drives scripts/battery_verdict.py BACKWARDS with
fixture check_all outputs (A6, docs/plan/PRODUCTIZATION-DIRECTIVE-2026-08-31.md).

Every fixture is a real check_all.sh-shaped text block, run through the
script's own CLI as a subprocess (never by importing internals and calling a
function), because the contract this tool exists to hold is the CLI's exit
code and its printed JSON, not an internal helper's return value.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "battery_verdict.py")

EXPECTATIONS = {
    "checks": {
        "product-acceptance-self": {
            "class": "expected_unavailable",
            "reason": "test fixture: the pre-existing acceptance-family flake",
            "recorded": "2026-08-31",
            "review_by": "2026-09-07"
        },
        "negative-space-audit": {
            "class": "known_no_data",
            "reason": "test fixture: honest open findings by design",
            "recorded": "2026-08-31",
            "review_by": "2026-09-14"
        },
        "future-mechanism": {
            "class": "not_applicable",
            "reason": "test fixture: mechanism exists, nothing applies today",
            "recorded": "2026-08-31"
        },
        "product-suite": {
            "class": "expected_unavailable",
            "reason": "test fixture: a product battery whose failing tests are declared by name",
            "recorded": "2026-09-03",
            "review_by": "2026-09-30",
            "removal_condition": "test fixture: shared by every declared name",
            "failing_tests": {
                "test_docs.py": {
                    "test_declared_one": {"reason": "test fixture: AssertionError one"},
                    "test_declared_two": {"reason": "test fixture: AssertionError two"}
                }
            }
        },
        "count-only-suite": {
            "class": "expected_unavailable",
            "reason": "test fixture: a suite-level blanket, failures=2 and no names",
            "recorded": "2026-09-03",
            "review_by": "2026-09-30"
        }
    }
}

# Two check_all.sh files of our own, so WHICH checks run unittest suites is
# driven here rather than inherited from the real one. The verdict reads a
# check_all.sh too (an expectations file that fails its own schema shelters
# nothing), so a fixture expectations file has to be paired with a fixture
# check_all.sh or every verdict test below measures the real repository's
# registrations instead of the ones it wrote.
#
# STRICT registers count-only-suite as a unittest suite, which makes the
# EXPECTATIONS above malformed against it on purpose: that is the schema
# check's own subject, and the input the verdict must refuse.
CHECK_ALL_FIXTURE = """#!/bin/sh
run_check "product-suite"    sh -c 'cd products/x && python3 tools/test_all.py'
run_check "count-only-suite" python3 -m unittest -v tests/test_count.py
run_check "plain-script"     python3 scripts/plain.py
run_check "probe-script"     python3 scripts/test_probe.py
"""

# CLEAN registers the same names in shapes the EXPECTATIONS above satisfy, so
# the classification tests measure classify() and not the schema gate in front
# of it. count-only-suite is a plain script here ON PURPOSE: check_all.sh
# cannot tell that its output names tests, and the LOG still can, which is the
# case that proves classify blocks on the run's own evidence rather than only
# on what the registration promised.
CHECK_ALL_CLEAN = """#!/bin/sh
run_check "product-suite"           sh -c 'cd products/x && python3 tools/test_all.py'
run_check "product-acceptance-self" python3 scripts/acceptance_probe.py
run_check "count-only-suite"        python3 scripts/count_probe.py
"""


def check_all_line(verdict, code, name, detail="ok"):
    return "%-7s exit %-3s %-34s %s" % (verdict, code, name, detail)


def test_line(test, suite="test_docs"):
    """The line run_check copies under a FAIL verdict: unittest's own failure
    header, indented, with the suite module in place of __main__."""
    return "        FAIL: %s (%s.TestCase.%s)" % (test, suite, test)


class BatteryVerdictTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.expectations_path = os.path.join(self.tmpdir, "expectations.json")
        with open(self.expectations_path, "w", encoding="utf-8") as fh:
            json.dump(EXPECTATIONS, fh)
        self.check_all_clean = self.write_check_all("clean.sh", CHECK_ALL_CLEAN)
        self.check_all_strict = self.write_check_all("strict.sh",
                                                     CHECK_ALL_FIXTURE)

    def write_check_all(self, name, text):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def run_verdict(self, lines, expectations_path=None, extra_args=None,
                     commit_header=None, check_all=None):
        input_path = os.path.join(self.tmpdir, "check_all_output.txt")
        with open(input_path, "w", encoding="utf-8") as fh:
            if commit_header is not None:
                fh.write(commit_header + "\n\n")
            fh.write("Brother: every shipped check, each reporting its own exit code\n\n")
            fh.write("\n".join(lines) + "\n\n")
            fh.write("pass 1   fail 0   no-data 0\n")
        args = [sys.executable, SCRIPT, input_path,
                "--expectations", expectations_path or self.expectations_path,
                "--check-all", check_all or self.check_all_clean]
        if extra_args:
            args += extra_args
        proc = subprocess.run(args, capture_output=True, text=True)
        return proc

    def test_unexpected_failure_blocks(self):
        proc = self.run_verdict([
            check_all_line("PASS", "0", "surface"),
            check_all_line("FAIL", "1", "some-new-check", "AssertionError boom"),
        ])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("some-new-check", out["unexpected_failures"])
        self.assertIn("some-new-check", out["blocking_failures"])
        self.assertEqual(out["product"], "FAIL")
        self.assertEqual(out["release_candidate"], "FAIL")

    def test_expected_unavailable_failure_is_clean(self):
        proc = self.run_verdict([
            check_all_line("PASS", "0", "surface"),
            check_all_line("FAIL", "1", "product-acceptance-self", "known flake"),
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("product-acceptance-self", out["expected_unavailable"])
        self.assertEqual(out["unexpected_failures"], [])
        self.assertEqual(out["blocking_failures"], [])
        self.assertEqual(out["product"], "PASS")
        self.assertEqual(out["release_candidate"], "PASS")

    def test_undeclared_no_data_blocks(self):
        proc = self.run_verdict([
            check_all_line("PASS", "0", "surface"),
            check_all_line("NO-DATA", "2", "some-new-audit", "42 cells unknown"),
        ])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("some-new-audit", out["blocking_failures"])
        self.assertEqual(out["product"], "FAIL")

    def test_declared_no_data_is_clean(self):
        proc = self.run_verdict([
            check_all_line("PASS", "0", "surface"),
            check_all_line("NO-DATA", "2", "negative-space-audit", "3 cells no-data"),
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("negative-space-audit", out["known_no_data"])
        self.assertEqual(out["blocking_failures"], [])
        self.assertEqual(out["product"], "PASS")

    def test_not_applicable_never_blocks_whatever_its_verdict(self):
        proc = self.run_verdict([
            check_all_line("FAIL", "1", "future-mechanism", "irrelevant today"),
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("future-mechanism", out["not_applicable"])
        self.assertEqual(out["blocking_failures"], [])
        self.assertEqual(out["unexpected_failures"], [])

    def test_unreadable_input_is_no_data(self):
        args = [sys.executable, SCRIPT,
                os.path.join(self.tmpdir, "does-not-exist.txt"),
                "--expectations", self.expectations_path]
        proc = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_unreadable_expectations_is_no_data(self):
        proc = self.run_verdict(
            [check_all_line("PASS", "0", "surface")],
            expectations_path=os.path.join(self.tmpdir, "no-such-expectations.json"))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_no_run_check_lines_is_no_data(self):
        input_path = os.path.join(self.tmpdir, "empty.txt")
        with open(input_path, "w", encoding="utf-8") as fh:
            fh.write("nothing here looks like a run_check line\n")
        args = [sys.executable, SCRIPT, input_path,
                "--expectations", self.expectations_path]
        proc = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_no_input_and_no_run_is_no_data(self):
        args = [sys.executable, SCRIPT, "--expectations", self.expectations_path]
        proc = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_recovered_expectation_reported_by_name(self):
        proc = self.run_verdict([
            check_all_line("PASS", "0", "surface"),
            check_all_line("PASS", "0", "product-acceptance-self", "flake did not fire this time"),
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("product-acceptance-self", out["recovered"])
        self.assertNotIn("product-acceptance-self", out["expected_unavailable"])
        self.assertEqual(out["product"], "PASS")

    def test_an_exception_past_its_review_date_turns_blocking(self):
        # Red-team item 6: --today after review_by (2026-09-07) means the
        # sheltered flake is no longer sheltered and blocks.
        proc = self.run_verdict([
            check_all_line("FAIL", "1", "product-acceptance-self", "still flaking"),
        ], extra_args=["--today", "2026-09-08"])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("product-acceptance-self", out["expired_exceptions"])
        self.assertIn("product-acceptance-self", out["blocking_failures"])
        self.assertEqual(out["product"], "FAIL")

    def test_the_same_exception_before_its_review_date_still_shelters(self):
        proc = self.run_verdict([
            check_all_line("FAIL", "1", "product-acceptance-self", "still flaking"),
        ], extra_args=["--today", "2026-09-01"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["expired_exceptions"], [])
        self.assertIn("product-acceptance-self", out["expected_unavailable"])
        self.assertEqual(out["product"], "PASS")

    def test_a_recovered_check_never_blocks_even_when_past_review(self):
        proc = self.run_verdict([
            check_all_line("PASS", "0", "product-acceptance-self", "fixed"),
        ], extra_args=["--today", "2026-09-08"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("product-acceptance-self", out["recovered"])
        self.assertEqual(out["expired_exceptions"], [])

    def test_declared_no_data_that_actually_fails_is_worse_not_free(self):
        proc = self.run_verdict([
            check_all_line("FAIL", "1", "negative-space-audit", "crashed, not no-data"),
        ])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("negative-space-audit", out["unexpected_failures"])
        self.assertIn("negative-space-audit", out["blocking_failures"])

    def test_the_commit_header_is_read_into_its_own_field(self):
        # A6 amendment: a saved log with no 40-hex SHA anywhere in it cannot be
        # tied back to the revision it measured. check_all.sh's own header
        # line is the fix; this proves the field the critic actually reads.
        proc = self.run_verdict(
            [check_all_line("PASS", "0", "surface")],
            commit_header="Brother: measuring commit "
                           "1234567890abcdef1234567890abcdef12345678 "
                           "(v1.2.3-4-g1234567) +dirty")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["commit"]["sha"],
                          "1234567890abcdef1234567890abcdef12345678")
        self.assertEqual(out["commit"]["describe"], "v1.2.3-4-g1234567")
        self.assertTrue(out["commit"]["dirty"])

    def test_a_clean_tree_carries_no_dirty_marker(self):
        proc = self.run_verdict(
            [check_all_line("PASS", "0", "surface")],
            commit_header="Brother: measuring commit "
                           "abcabcabcabcabcabcabcabcabcabcabcabcabc (main)")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertFalse(out["commit"]["dirty"])

    def test_a_log_with_no_commit_header_reports_no_data_not_a_parse_failure(self):
        # An old log saved before this header existed must still parse: the
        # field itself carries NO-DATA rather than raising or being absent.
        proc = self.run_verdict([check_all_line("PASS", "0", "surface")])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["commit"], "NO-DATA")

    # -- test granularity (2026-09-03) ------------------------------------

    def test_an_undeclared_failing_test_blocks_by_name(self):
        proc = self.run_verdict([
            check_all_line("FAIL", "1", "product-suite", "2 SUITE(S) FAILED"),
            test_line("test_declared_one"),
            test_line("test_declared_two"),
            test_line("test_new_one"),
        ])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("product-suite: test_new_one", out["unexpected_failures"])
        self.assertIn("product-suite", out["blocking_failures"])
        self.assertNotIn("product-suite", out["expected_unavailable"])
        self.assertEqual(out["product"], "FAIL")

    def test_a_declared_test_that_no_longer_fails_is_recovered_by_name(self):
        proc = self.run_verdict([
            check_all_line("FAIL", "1", "product-suite", "1 SUITE(S) FAILED"),
            test_line("test_declared_one"),
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("product-suite: test_declared_two", out["recovered"])
        self.assertIn("product-suite", out["expected_unavailable"])
        self.assertEqual(out["unexpected_failures"], [])
        self.assertEqual(out["blocking_failures"], [])
        self.assertEqual(out["product"], "PASS")

    def test_every_failing_test_declared_is_sheltered(self):
        proc = self.run_verdict([
            check_all_line("FAIL", "1", "product-suite", "1 SUITE(S) FAILED"),
            test_line("test_declared_one"),
            test_line("test_declared_two"),
        ])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("product-suite", out["expected_unavailable"])
        self.assertEqual(out["recovered"], [])
        self.assertEqual(out["granularity_violations"], [])

    def test_a_suite_level_entry_is_refused_once_the_log_names_tests(self):
        # The 2026-09-03 finding: an entry declared for a whole check read a
        # run with eight undeclared failures as expected. Names in the log
        # plus no names in the entry is a violation, not a shelter.
        proc = self.run_verdict([
            check_all_line("FAIL", "1", "count-only-suite", "FAILED (failures=2)"),
            test_line("test_one", suite="test_count"),
            test_line("test_two", suite="test_count"),
        ])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertTrue(any(v.startswith("count-only-suite:") and
                            "declare at test granularity" in v
                            for v in out["granularity_violations"]),
                        out["granularity_violations"])
        self.assertIn("count-only-suite", out["blocking_failures"])
        self.assertNotIn("count-only-suite", out["expected_unavailable"])

    def test_names_declared_but_a_log_without_names_cannot_shelter(self):
        # A log saved before run_check copied the headers (round 11 and
        # earlier) cannot prove WHICH tests failed, so it blocks and says so.
        proc = self.run_verdict([
            check_all_line("FAIL", "1", "product-suite", "2 SUITE(S) FAILED"),
        ])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertTrue(any(v.startswith("product-suite:") and
                            "cannot be verified" in v
                            for v in out["granularity_violations"]),
                        out["granularity_violations"])
        self.assertIn("product-suite", out["blocking_failures"])

    def run_schema_check(self, checks, check_all_text=CHECK_ALL_FIXTURE,
                         today=None):
        exp_path = os.path.join(self.tmpdir, "schema-expectations.json")
        with open(exp_path, "w", encoding="utf-8") as fh:
            json.dump({"checks": checks}, fh)
        ca_path = self.write_check_all("check_all.sh", check_all_text)
        args = [sys.executable, SCRIPT, "--check-expectations",
                exp_path, "--check-all", ca_path]
        if today:
            args += ["--today", today]
        return subprocess.run(args, capture_output=True, text=True), ca_path

    def test_the_schema_check_accepts_names_and_rejects_a_count(self):
        checks = dict(EXPECTATIONS["checks"])
        checks["plain-script"] = {
            "class": "expected_unavailable",
            "reason": "test fixture: a plain script, no unittest output",
            "recorded": "2026-09-03", "review_by": "2026-09-30"}
        checks["probe-script"] = {
            "class": "expected_unavailable",
            "reason": "test fixture: a test_*.py that runs a probe, not unittest",
            "recorded": "2026-09-03", "review_by": "2026-09-30",
            "failing_tests": "none: prints one verdict line and exits 1 by design"}
        proc, _ = self.run_schema_check(checks)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        fails = [ln for ln in proc.stdout.splitlines() if ln.startswith("FAIL ")]
        self.assertEqual(len(fails), 1, proc.stdout)
        self.assertIn("count-only-suite", fails[0])
        self.assertIn("declare at test granularity", fails[0])
        del checks["count-only-suite"]
        proc, _ = self.run_schema_check(checks)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK:", proc.stdout)

    def test_the_schema_check_rejects_a_suite_with_no_test_names(self):
        checks = {"product-suite": {
            "class": "expected_unavailable",
            "reason": "test fixture: suite named, tests not",
            "recorded": "2026-09-03", "review_by": "2026-09-30",
            "failing_tests": {"test_docs.py": {}}}}
        proc, _ = self.run_schema_check(checks)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("declare at test granularity", proc.stdout)

    def test_the_schema_check_rejects_a_review_by_already_past(self):
        # S6 / the estate's own lesson (a standing exception often excuses a
        # stale test): a renewal that leaves review_by behind today's date is
        # refused at schema time, not only discovered on the next real run.
        checks = {"stale-thing": {
            "class": "known_no_data",
            "reason": "test fixture: an exception nobody re-reviewed",
            "recorded": "2020-01-01", "review_by": "2020-01-02"}}
        proc, _ = self.run_schema_check(checks, check_all_text="#!/bin/sh\n",
                                        today="2026-09-05")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("stale-thing", proc.stdout)
        self.assertIn("already passed", proc.stdout)

    def test_the_schema_check_still_accepts_a_review_by_not_yet_past(self):
        # The other side of the same test: an entry whose review_by has not
        # arrived yet keeps its contract, so the new rule only catches the
        # actually-expired case.
        checks = {"fresh-thing": {
            "class": "known_no_data",
            "reason": "test fixture: an exception still within its window",
            "recorded": "2020-01-01", "review_by": "2099-01-01"}}
        proc, _ = self.run_schema_check(checks, check_all_text="#!/bin/sh\n",
                                        today="2026-09-05")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK:", proc.stdout)

    def test_a_verdict_refuses_an_expectations_file_that_fails_its_schema(self):
        # count-only-suite runs "-m unittest" in the fixture check_all.sh, so
        # the real EXPECTATIONS fixture is malformed against it: the verdict
        # must read NO-DATA rather than shelter anything.
        proc = self.run_verdict([check_all_line("PASS", "0", "surface")],
                                check_all=self.check_all_strict)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("declare at test granularity", proc.stdout)

    def test_the_real_expectations_file_keeps_its_contract(self):
        """Every entry declares a class the verdict understands, and carries the
        reason and date that make it reviewable.

        This asserted two entries BY NAME until 2026-09-02, and broke the night
        two of them were legitimately retired because their checks started
        passing. Pinning an id here made the file harder to shrink, which is the
        opposite of what an exceptions list should encourage: the good direction
        for this file is fewer entries, and a test that fails when an exception
        is removed quietly argues for keeping it. What matters is the CONTRACT,
        which holds at any size, including empty."""
        real_path = os.path.join(HERE, "..", "docs", "plan", "BATTERY-EXPECTATIONS.json")
        with open(real_path, encoding="utf-8") as fh:
            data = json.load(fh)
        checks = data["checks"]
        known_classes = {"expected_unavailable", "known_no_data", "not_applicable"}
        for name, entry in checks.items():
            self.assertIn(entry.get("class"), known_classes,
                          "%s declares a class the verdict cannot read: %r"
                          % (name, entry.get("class")))
            self.assertIn("reason", entry, name)
            self.assertIn("recorded", entry, name)
            self.assertTrue(str(entry.get("reason") or "").strip(),
                            "%s carries an empty reason, which is an exception "
                            "nobody can review" % name)
        # and the script's own schema check, against the real check_all.sh,
        # which is what demands test granularity where the output names tests
        proc = subprocess.run([sys.executable, SCRIPT, "--check-expectations",
                               real_path], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
