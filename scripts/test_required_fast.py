"""test_required_fast.py: drives scripts/required_fast.sh BACKWARDS.

Never runs the real check suites (that would make this test itself minutes
long, defeating its own purpose as a fast pre-merge guard's own regression
test). Instead it builds a TEMP COPY of the script with the real run_check
invocations swapped for stub commands (`true` / `false` / `sh -c "exit 2"`),
reusing the script's own run_check function and summary logic verbatim, and
asserts the summary line and the exit code for three cases: all pass, one
fail, and a mix that includes NO-DATA (exit 2).

This is the same "drive it backwards, never trust a single green" method the
rest of this estate's self-tests use (see scripts/test_battery_verdict.py).
"""
import os
import json
import shutil
import re
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "required_fast.sh")

with open(SCRIPT) as f:
    _SOURCE = f.read()

# The script is HEADER (shebang, comments, cd, counters, the run_check
# function, the two intro echo lines) + a fixed block of real run_check
# calls + FOOTER (the summary block and exit). Split on the script's own
# literal anchors so a stub build never depends on which real checks are
# currently listed.
_CHECKS_START = 'run_check "version-truth"'
_FOOTER_START = '\necho\necho "pass $pass   fail $fail   no-data $nodata"'

_start = _SOURCE.index(_CHECKS_START)
_end = _SOURCE.index(_FOOTER_START)
HEADER = _SOURCE[:_start]
FOOTER = _SOURCE[_end:]

assert HEADER.strip(), "could not locate the header before the first real check"
assert FOOTER.strip(), "could not locate the summary footer"


_FIXTURES = tempfile.TemporaryDirectory(prefix="required-fast-fixtures-")


def install_obligation_fixture(scripts_dir, optional=()):
    shutil.copyfile(os.path.join(HERE, "evidence_obligation.py"),
                    os.path.join(scripts_dir, "evidence_obligation.py"))
    data = {"schema": "brother.gate-obligations/v1",
            "default": "REQUIRED_FOR_MERGE", "checks": {
                name: {"obligation": "OPTIONAL", "reason": "test optional evidence"}
                for name in optional}}
    with open(os.path.join(scripts_dir, "gate_obligations.json"), "w") as handle:
        json.dump(data, handle)


def build_stub_script(stub_lines, optional=()):
    """stub_lines: list of 'run_check "name" <stub command>' strings."""
    body = HEADER + "\n".join(stub_lines) + "\n" + FOOTER
    root = tempfile.mkdtemp(dir=_FIXTURES.name)
    scripts_dir = os.path.join(root, "scripts")
    os.makedirs(scripts_dir)
    install_obligation_fixture(scripts_dir, optional)
    path = os.path.join(scripts_dir, "required_fast.sh")
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def run(path, extra_env=None):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(["sh", path], capture_output=True, text=True,
                          timeout=20, env=env)
    return proc.returncode, proc.stdout + proc.stderr


class RequiredFastScript(unittest.TestCase):
    def test_script_exists_and_is_shell(self):
        self.assertTrue(os.path.exists(SCRIPT))
        with open(SCRIPT) as f:
            self.assertTrue(f.readline().startswith("#!/bin/sh"))

    def test_all_pass_exits_zero_and_counts_match(self):
        path = build_stub_script([
            'run_check "stub-a" true',
            'run_check "stub-b" true',
            'run_check "stub-c" true',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 0, out)
        self.assertIn("pass 3   fail 0   no-data 0", out)
        self.assertNotIn("FAILED:", out)

    def test_one_fail_exits_one_and_is_named(self):
        path = build_stub_script([
            'run_check "stub-a" true',
            'run_check "stub-fails" false',
            'run_check "stub-c" true',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("pass 2   fail 1   no-data 0", out)
        self.assertIn("FAILED:", out)
        self.assertIn("stub-fails", out)
        # the failing check's full output is captured to a named file
        m = re.search(r"\[full: (\S+)\]", out)
        self.assertIsNotNone(m, out)
        self.assertTrue(os.path.exists(m.group(1)), out)
        os.remove(m.group(1))

    def test_failure_detail_is_silent_by_default(self):
        """The gap this closes: on a GitHub runner the [full: /tmp/...]
        file is discarded with the job, so without the opt-in a failure's
        real output is unreachable. Plain local runs must stay unchanged:
        the detail block only appears when asked for."""
        path = build_stub_script([
            'run_check "stub-fails" sh -c "echo boom-detail-line; exit 1"',
        ])
        try:
            code, out = run(path, extra_env={"REQUIRED_FAST_PRINT_FAILURES": "",
                                             "GITHUB_ACTIONS": ""})
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertNotIn("failure detail", out, out)
        m = re.search(r"\[full: (\S+)\]", out)
        self.assertIsNotNone(m, out)
        os.remove(m.group(1))

    def test_print_failures_env_var_prints_the_saved_detail(self):
        path = build_stub_script([
            'run_check "stub-fails" sh -c "echo boom-detail-line; exit 1"',
        ])
        try:
            code, out = run(path, extra_env={"REQUIRED_FAST_PRINT_FAILURES": "1"})
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("---- stub-fails failure detail ----", out, out)
        self.assertIn("boom-detail-line", out, out)
        m = re.search(r"\[full: (\S+)\]", out)
        self.assertIsNotNone(m, out)
        os.remove(m.group(1))

    def test_github_actions_alone_also_prints_the_saved_detail(self):
        """The runner sets GITHUB_ACTIONS itself; nobody there types the
        opt-in flag, so it must trigger the same as the explicit var."""
        path = build_stub_script([
            'run_check "stub-fails" sh -c "echo boom-detail-line; exit 1"',
        ])
        try:
            code, out = run(path, extra_env={"GITHUB_ACTIONS": "true",
                                             "REQUIRED_FAST_PRINT_FAILURES": ""})
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("---- stub-fails failure detail ----", out, out)
        self.assertIn("boom-detail-line", out, out)
        m = re.search(r"\[full: (\S+)\]", out)
        self.assertIsNotNone(m, out)
        os.remove(m.group(1))

    def test_required_no_data_blocks_without_rewriting_the_verdict(self):
        path = build_stub_script([
            'run_check "stub-a" true',
            'run_check "stub-nodata" sh -c "exit 2"',
            'run_check "stub-c" true',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("pass 2   fail 0   no-data 1", out)
        self.assertIn("NO-DATA:", out)
        self.assertIn("stub-nodata", out)
        self.assertNotIn("FAILED:", out)
        self.assertIn("stub-nodata\tNO-DATA\tREQUIRED_FOR_MERGE\tBLOCKED", out)

    def test_optional_no_data_allows_without_rewriting_the_verdict(self):
        path = build_stub_script([
            'run_check "stub-nodata" sh -c "exit 2"',
        ], optional=("stub-nodata",))
        code, out = run(path)
        self.assertEqual(code, 0, out)
        self.assertIn("pass 0   fail 0   no-data 1", out)
        self.assertIn("stub-nodata\tNO-DATA\tOPTIONAL\tALLOWED", out)

    def test_fail_and_no_data_together_still_fails_on_the_fail(self):
        path = build_stub_script([
            'run_check "stub-fails" false',
            'run_check "stub-nodata" sh -c "exit 2"',
        ])
        try:
            code, out = run(path)
        finally:
            os.remove(path)
        self.assertEqual(code, 1, out)
        self.assertIn("pass 0   fail 1   no-data 1", out)


class TwoWorktreesShareOneTempDirectory(unittest.TestCase):
    """Row E100. Lane BM2's gate run read a traceback out of lane AW2's tree,
    because the failure capture was keyed by check name and pid inside one
    shared $TMPDIR. These drive the real script from two differently named
    parent directories, sharing one temp root, which is the collision itself.
    """

    def build_in_lane(self, lane, stub_lines):
        """A stub of the real script under <root>/<lane>/scripts, so the
        script's own `cd $(dirname $0)/..` resolves to a lane directory and
        its worktree key is that lane's name."""
        root = tempfile.mkdtemp(prefix="two-worktrees-")
        scripts_dir = os.path.join(root, lane, "scripts")
        os.makedirs(scripts_dir)
        install_obligation_fixture(scripts_dir)
        path = os.path.join(scripts_dir, "required_fast.sh")
        with open(path, "w") as fh:
            fh.write(HEADER + "\n".join(stub_lines) + "\n" + FOOTER)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def run_sharing(self, path, shared_tmp):
        env = dict(os.environ)
        env["TMPDIR"] = shared_tmp
        proc = subprocess.run(["sh", path], capture_output=True, text=True,
                              timeout=20, env=env)
        return proc.returncode, proc.stdout + proc.stderr

    def test_two_lanes_never_write_the_same_failure_file(self):
        shared = tempfile.mkdtemp(prefix="shared-tmpdir-")
        one = self.build_in_lane(
            "lane-one", ['run_check "export-public" sh -c '
                         '"echo LANE-ONE-TRACEBACK; exit 1"'])
        two = self.build_in_lane(
            "lane-two", ['run_check "export-public" sh -c '
                         '"echo LANE-TWO-TRACEBACK; exit 1"'])
        code_one, out_one = self.run_sharing(one, shared)
        code_two, out_two = self.run_sharing(two, shared)
        self.assertEqual((code_one, code_two), (1, 1), out_one + out_two)

        kept = sorted(n for n in os.listdir(shared)
                      if n.startswith("required-fast-fail-"))
        self.assertEqual(len(kept), 2, kept)
        self.assertTrue(any("lane-one" in n for n in kept), kept)
        self.assertTrue(any("lane-two" in n for n in kept), kept)
        for name in kept:
            with open(os.path.join(shared, name)) as fh:
                body = fh.read()
            lane = "ONE" if "lane-one" in name else "TWO"
            other = "TWO" if lane == "ONE" else "ONE"
            self.assertIn("LANE-%s-TRACEBACK" % lane, body)
            self.assertNotIn("LANE-%s-TRACEBACK" % other, body)

    def test_a_run_that_dies_early_says_no_data_and_not_a_pass(self):
        shared = tempfile.mkdtemp(prefix="shared-tmpdir-")
        path = self.build_in_lane("lane-killed", [
            'run_check "stub-a" true',
            'exit 7',
        ])
        code, out = self.run_sharing(path, shared)
        self.assertEqual(code, 7, out)
        self.assertIn("NO-DATA: required-fast stopped after 1 check(s)", out)
        self.assertIn("lane-killed", out)
        self.assertIn("This is NOT a pass", out)
        self.assertNotIn("pass 1   fail 0", out)

    def test_a_finished_run_says_nothing_about_no_data(self):
        """The positive control: without it the trap could fire always, or
        never, and the test above would pass either way."""
        shared = tempfile.mkdtemp(prefix="shared-tmpdir-")
        path = self.build_in_lane("lane-finished", ['run_check "stub-a" true'])
        code, out = self.run_sharing(path, shared)
        self.assertEqual(code, 0, out)
        self.assertIn("pass 1   fail 0   no-data 0", out)
        self.assertNotIn("required-fast stopped after", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
