#!/usr/bin/env python3
"""Unit A1 (I10, I12, I13): one proof for DONE. Run: python3 -B scripts/test_builder_preflight.py

Every case runs a real entry point: scripts/builder_preflight.sh over a lane made from a copy of this tree, or
scripts/close_unit.py's main(). One fixture per condition, so each refusal can only come from the lint it names:

  bl-owns            a lane adding a scripts/loop file unit BL does not name is refused
  silent-failure     a lane adding a silent except to a loop tool is refused
  release-invariant  a lane whose bundle version disagrees with its marketplace entry is refused
  hermetic           a lane adding a test that reads the real HOME is refused (a hermetic control passes)
  base-parity        a lane whose BASE carries a loop tool the stamp has not deployed is refused, while a lane whose
                     own diff changes a loop tool on a deployed base passes (the deploy step owns that parity, A2)
  close_unit         --lane-base refuses DONE when the preflight refuses; a test reading the real HOME passes bare and
                     is NOT DONE boxed; OK (skipped=1) leaves the unit PARTIAL with the skipping test named

WHERE IT CANNOT JUDGE, IT SAYS SO, AND NEVER SKIPS where the closer runs it (a skip is PARTIAL by this unit's own
rule). Inside a sandbox (the closer's boxed run) a nested sandbox is refused, so the hermetic row reads NO-DATA for
the control too, and the hermetic cases assert exactly that; bare (the gate) they assert the discriminating verdicts.
The one named skip is the export tree, which ships without docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json, the plan bl-owns
reads; the closer runs in the hub checkout, which carries it."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))
# THE FIXTURES OWN THEIR PLAN (2026-10-19): every fixture writes its plan at the default path, and close_unit.PLAN is
# read at import from BROTHER_WBS, so a caller that selected another plan (a 1.1.7 closer, a loop pass) turned seven
# cases red, found by the builder preflight's hermetic row. Dropped here, before the import, for this process and its
# children alike.
os.environ.pop("BROTHER_WBS", None)
import close_unit as C  # noqa: E402
import grade_build as G  # noqa: E402  (sandbox_ready: the one probe of whether a sandbox may run here)

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
LAUNCH_PLAN = os.path.join("docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
HUB_ONLY = os.path.isfile(os.path.join(ROOT, LAUNCH_PLAN))
TOOL = "scripts/loop/loop_report.py"          # a deployed loop tool the fixtures edit


def git(cwd, *args):
    r = subprocess.run(GIT + list(args), cwd=cwd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr.strip()[-300:]))
    return r.stdout.strip()


def tree_files(root):
    """The tree's files: tracked and untracked (not ignored) in a checkout, every file in an export tree (no .git)."""
    r = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
                       capture_output=True, timeout=120)
    if r.returncode == 0 and r.stdout:
        return [n.decode("utf-8") for n in r.stdout.split(b"\0") if n]
    out = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in (".git", "__pycache__", ".grade-sandbox")]
        out += [os.path.relpath(os.path.join(d, n), root) for n in names]
    return out


def stamp_for(lane, path):
    """A deploy stamp declaring the lane's current scripts/loop files deployed, at a revision no lane has."""
    files = {}
    loop = os.path.join(lane, "scripts", "loop")
    for d, _, names in os.walk(loop):
        for n in names:
            p = os.path.join(d, n)
            with open(p, "rb") as fh:
                files["bin/" + os.path.relpath(p, loop)] = hashlib.sha256(fh.read()).hexdigest()
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, ".deploy-stamp.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema": 1, "revision": "0" * 40, "files": files}, fh)


class Lane(unittest.TestCase):
    """One copy of this tree, committed as BASE, reset before every case."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="preflight-")
        cls.lane = os.path.join(cls.tmp, "lane")
        for rel in tree_files(ROOT):
            src = os.path.join(ROOT, rel)
            if os.path.islink(src) or not os.path.isfile(src):
                continue
            dst = os.path.join(cls.lane, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        git(cls.lane, "init", "-q", "-b", "main")
        git(cls.lane, "add", "-A")
        git(cls.lane, "commit", "-q", "-m", "base")
        cls.base = git(cls.lane, "rev-parse", "HEAD")
        cls.home = os.path.join(cls.tmp, "home")
        cls.bin = os.path.join(cls.tmp, "bin")
        stamp_for(cls.lane, cls.bin)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        git(self.lane, "reset", "-q", "--hard", self.base)
        git(self.lane, "clean", "-qfdx")
        shutil.rmtree(self.home, ignore_errors=True)
        os.makedirs(os.path.join(self.home, "tmp"))

    def env(self):
        return dict(os.environ, HOME=self.home, TMPDIR=os.path.join(self.home, "tmp"), BROTHER_DEPLOY_TARGET=self.bin)

    def preflight(self, *args, base=None):
        r = subprocess.run(["sh", "scripts/builder_preflight.sh", "--base", base or self.base] + list(args),
                           cwd=self.lane, env=self.env(), capture_output=True, text=True, timeout=1800)
        return r.returncode, r.stdout + r.stderr

    def write(self, rel, text, mode="w"):
        p = os.path.join(self.lane, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, mode, encoding="utf-8") as fh:
            fh.write(text)

    def commit(self, msg):
        git(self.lane, "add", "-A")
        git(self.lane, "commit", "-q", "-m", msg)
        return git(self.lane, "rev-parse", "HEAD")


@unittest.skipUnless(HUB_ONLY, "the export tree ships without %s, the plan bl-owns reads" % LAUNCH_PLAN)
class TheFiveLintsRefuseBeforeDone(Lane):
    def test_bl_owns_refuses_a_loop_file_unit_bl_does_not_name(self):
        self.assertEqual(self.preflight("--only", "bl-owns")[0], 0, "the clean base must pass first")
        self.write("scripts/loop/fixture_unowned_tool.py", "print('a tool nobody owns')\n")
        self.commit("unowned")
        code, out = self.preflight("--only", "bl-owns")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL unowned      scripts/loop/fixture_unowned_tool.py", out)

    def test_silent_failure_refuses_a_silent_except_in_a_loop_tool(self):
        self.assertEqual(self.preflight("--only", "silent-failure")[0], 0, "the clean base must pass first")
        self.write(TOOL, "\n\ndef _fixture_swallow():\n    try:\n        return int('x')\n    except Exception:\n"
                         "        pass\n", mode="a")
        self.commit("silent")
        code, out = self.preflight("--only", "silent-failure")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL     silent-failure", out)

    def test_release_invariant_refuses_a_bundle_version_its_marketplace_does_not_carry(self):
        self.assertEqual(self.preflight("--only", "release-invariant")[0], 0, "the clean base must pass first")
        p = os.path.join(self.lane, "bundle", ".claude-plugin", "plugin.json")
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["version"] = "9.9.9"
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        self.commit("stale")
        code, out = self.preflight("--only", "release-invariant")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL     release-invariant", out)

    def test_hermetic_refuses_a_test_that_reads_the_real_home(self):
        ready = G.sandbox_ready()
        self.write("scripts/test_fixture_hermetic_ok.py", "import unittest\n\nclass T(unittest.TestCase):\n"
                   "    def test_ok(self):\n        self.assertTrue(True)\n\nif __name__ == '__main__':\n    unittest.main()\n")
        self.commit("hermetic control")
        control, cout = self.preflight("--only", "hermetic")
        self.write("scripts/test_fixture_reads_home.py", "import os, unittest\n\nclass T(unittest.TestCase):\n"
                   "    def test_home(self):\n        with open(os.path.expanduser('~/.preflight-marker')) as fh:\n"
                   "            self.assertEqual(fh.read(), 'x')\n\nif __name__ == '__main__':\n    unittest.main()\n")
        with open(os.path.join(self.home, ".preflight-marker"), "w") as fh:
            fh.write("x")
        bare = subprocess.run([sys.executable, "-B", "scripts/test_fixture_reads_home.py"], cwd=self.lane,
                              env=self.env(), capture_output=True, text=True, timeout=120)
        self.assertEqual(bare.returncode, 0, "the fixture test must pass bare, with its HOME: " + bare.stderr[-300:])
        self.commit("reads home")
        code, out = self.preflight("--only", "hermetic", base=git(self.lane, "rev-parse", "HEAD~1"))
        if ready:   # nested: the hermetic check cannot confine, and says NO-DATA for the control as well
            self.assertEqual((control, code), (2, 2), "sandbox not ready (%s): %s | %s" % (ready, cout, out))
            return
        self.assertEqual(control, 0, cout)
        self.assertEqual(code, 1, out)
        self.assertIn("scripts/test_fixture_reads_home.py", out)

    def test_base_parity_refuses_a_base_carrying_an_undeployed_loop_tool(self):
        self.assertEqual(self.preflight("--only", "base-parity")[0], 0, "the deployed base must pass first")
        self.write(TOOL, "\n# a change the stamp has not deployed\n", mode="a")
        newer = self.commit("undeployed base")
        code, out = self.preflight("--only", "base-parity", base=newer)
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL undeployed   %s" % TOOL, out)

    def test_a_lanes_own_loop_tool_edit_on_a_deployed_base_passes(self):
        self.write(TOOL, "\n# the lane's own loop tool edit: the deploy step's parity, never refused DONE here\n", mode="a")
        self.commit("lane edit")
        code, out = self.preflight("--only", "base-parity")
        self.assertEqual(code, 0, out)
        self.assertIn("PASS: the base", out)

    def test_an_unreadable_stamp_is_no_data_never_a_pass(self):
        with mock.patch.dict(os.environ, {}):
            env = dict(self.env(), BROTHER_DEPLOY_TARGET=os.path.join(self.tmp, "no-bin"))
        r = subprocess.run(["sh", "scripts/builder_preflight.sh", "--base", self.base, "--only", "base-parity"],
                           cwd=self.lane, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("PREFLIGHT NO-DATA", r.stdout)

    def test_a_missing_or_hostile_base_is_no_data(self):
        for args in ([], ["--base", "-rf"], ["--base", "nosuchrev"], ["--base", self.base, "--only", "everything"]):
            r = subprocess.run(["sh", "scripts/builder_preflight.sh"] + args, cwd=self.lane, env=self.env(),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, (args, r.stdout))
            self.assertTrue(r.stdout.startswith("NO-DATA"), (args, r.stdout))

    def test_close_unit_refuses_done_when_the_preflight_refuses(self):
        """The ENTRY POINT: close_unit main with --lane-base on a lane the preflight refuses. The done check is faked to
        pass, so the unit stays open only because the preflight ran and refused (a closer that skipped it closes it)."""
        self.write("scripts/loop/fixture_unowned_tool.py", "print('a tool nobody owns')\n")
        self.commit("unowned")
        code, unit, said = self.close_with_base(self.base)
        self.assertEqual(code, 1, said)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("A     NOT CLOSED: the builder preflight refused (exit 1)", said)

    def test_judges_refuses_a_lane_changing_a_program_its_preflight_runs(self):
        """Security review of A1, 2026-10-19: the lint programs ran from the lane, so a lane could rewrite its own judge."""
        self.assertEqual(self.preflight("--only", "judges")[0], 0, "the clean base must pass first")
        self.write("scripts/test_bl_owns_complete.py", "\n# a lane quietly editing its own judge\n", mode="a")
        self.commit("judge edit")
        code, out = self.preflight("--only", "judges")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL judge changed   scripts/test_bl_owns_complete.py", out)

    def test_judges_refuses_a_change_deep_in_a_judges_import_closure(self):
        self.write("scripts/cut_preflight.py", "\n# imported by the hermetic check, so a judge too\n", mode="a")
        self.commit("closure edit")
        code, out = self.preflight("--only", "judges")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL judge changed   scripts/cut_preflight.py", out)

    def test_judges_refuses_a_file_shadowing_the_standard_library(self):
        """Security review, 2026-10-19: a judge run as python3 scripts/x.py has scripts/ on sys.path, so a lane's
        scripts/linecache.py would load wherever the standard library imports linecache (traceback does). No repository
        file imports linecache itself, so the closure cannot see it: only this rule refuses it (orthogonal fixture)."""
        self.write("scripts/linecache.py", "raise SystemExit(0)\n")
        self.commit("shadow")
        code, out = self.preflight("--only", "judges")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL judge changed   scripts/linecache.py", out)
        self.assertIn("named like a standard library module", out)

    def test_judges_refuses_a_compiled_module_anywhere_in_the_lane(self):
        """A path hook or bytecode file no import statement names, so the closure never resolves it: only this rule
        refuses it (orthogonal fixture; a .so beside a judge module is the closure's own case)."""
        self.write("scripts/loop/hook.pth", "import os\n")
        self.commit("path hook")
        code, out = self.preflight("--only", "judges")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL judge changed   scripts/loop/hook.pth", out)
        self.assertIn("compiled module, bytecode or path hook", out)

    def test_close_unit_runs_the_bases_preflight_never_the_lanes(self):
        """A lane that replaces its preflight with `exit 0` is still judged by the base's copy, whose first row refuses it."""
        self.write("scripts/builder_preflight.sh", "#!/bin/sh\necho 'PREFLIGHT PASS: trust me'\nexit 0\n")
        self.commit("forged preflight")
        code, unit, said = self.close_with_base(self.base)
        self.assertEqual(code, 1, said)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("FAIL judge changed   scripts/builder_preflight.sh", said)
        self.assertNotIn("trust me", said)

    def test_close_unit_refuses_done_when_the_preflight_cannot_judge(self):
        """A preflight that cannot judge (exit 2, here a base naming no commit) refuses DONE too: NO-DATA is never a
        pass. Measured 2026-10-19: the mutation reading exit 2 as a pass survived every other case."""
        code, unit, said = self.close_with_base("nosuchrev")
        self.assertEqual(code, 1, said)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("A     NOT CLOSED: the builder preflight refused (exit 2)", said)

    def close_with_base(self, base):
        """close_unit main(['A', '--lane-base', base]) in the lane, the boxed done check faked to pass: the unit stays
        open only if the preflight ran and refused (a closer that skipped it would close it)."""
        plan = os.path.join(self.tmp, "close-plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            json.dump({"units": [{"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "A.1 landed initial.",
                                  "done_check": "python3 -B scripts/test_close_unit.py"}]}, fh)
        here = os.getcwd()
        try:
            os.chdir(self.lane)
            with mock.patch.dict(os.environ, self.env()), mock.patch.object(C, "PLAN", plan), \
                    mock.patch.object(C, "run_done_check", return_value=(0, "Ran 3 tests\nOK")), \
                    mock.patch.object(C.provenance, "journal", return_value=True), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as said:
                code = C.main(["A", "--lane-base", base])
        finally:
            os.chdir(here)
        with open(plan, encoding="utf-8") as fh:
            return code, json.load(fh)["units"][0], said.getvalue()


class OneProofForDone(unittest.TestCase):
    """close_unit's own entry point (main) and the boxed runner, on small fixture trees."""

    def plan_tree(self, d, check_body):
        p = os.path.join(d, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
        os.makedirs(os.path.dirname(p))
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"units": [{"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "A.1 landed initial.",
                                  "done_check": "python3 -B check.py"}]}, fh)
        with open(os.path.join(d, "check.py"), "w", encoding="utf-8") as fh:
            fh.write(check_body)
        for argv in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "tree"]):
            git(d, *argv)
        return p

    def test_a_test_reading_the_real_home_passes_bare_and_is_not_done_boxed(self):
        with tempfile.TemporaryDirectory(prefix="close-home-") as d:
            p = self.plan_tree(d, "import os\nwith open(os.path.expanduser('~/.marker')) as fh:\n"
                                  "    assert fh.read() == 'x'\nprint('PASS: read the real home')\n")
            with open(os.path.join(d, ".marker"), "w") as fh:
                fh.write("x")
            env = dict(os.environ, HOME=d, TMPDIR=d)
            bare = subprocess.run([sys.executable, "-B", "check.py"], cwd=d, env=env, capture_output=True, text=True,
                                  timeout=60)
            self.assertEqual(bare.returncode, 0, bare.stderr)
            r = subprocess.run([sys.executable, "-B", C.__file__], cwd=d, env=env, capture_output=True, text=True,
                               timeout=300)
            with open(p, encoding="utf-8") as fh:
                unit = json.load(fh)["units"][0]
        self.assertNotEqual(unit["state"], "DONE", r.stdout + r.stderr)
        self.assertIn("A     NOT CLOSED: ", r.stdout)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def closed_by(self, output):
        """main(['A']) over a fixture plan whose done check printed `output` with exit 0 (the boxed runner faked)."""
        with tempfile.TemporaryDirectory(prefix="close-skip-") as d:
            p = os.path.join(d, "plan.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"units": [{"id": "A", "state": "PARTIAL", "sub_units": ["A.1"], "evidence": "A.1 landed initial.",
                                      "done_check": "python3 -B scripts/test_x.py -v"}]}, fh)
            with mock.patch.object(C, "PLAN", p), mock.patch.object(C, "run_done_check", return_value=(0, output)), \
                    mock.patch.object(C.provenance, "journal", return_value=True), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as said:
                code = C.main(["A"])
            with open(p, encoding="utf-8") as fh:
                return code, json.load(fh)["units"][0], said.getvalue()

    def test_ok_skipped_1_leaves_the_unit_partial_with_the_skipping_test_named(self):
        code, unit, said = self.closed_by("test_a (test_x.T) ... ok\ntest_b (test_x.T) ... skipped 'no network'\n\n"
                                          "Ran 2 tests in 0.010s\n\nOK (skipped=1)\n")
        self.assertEqual(code, 1, said)
        self.assertEqual(unit["state"], "PARTIAL")
        self.assertIn("A     NOT CLOSED: the done_check exited 0: PARTIAL: 1 test(s) skipped: test_x.T.test_b", said)

    def test_the_same_run_without_the_skip_closes(self):
        code, unit, said = self.closed_by("test_a (test_x.T) ... ok\n\nRan 1 test in 0.010s\n\nOK\n")
        self.assertEqual((code, unit["state"]), (0, "DONE"), said)

    def test_an_unnamed_skip_is_partial_and_says_how_to_name_it(self):
        ok, quoted = C.verdict(0, "Ran 15 tests in 1.2s\n\nOK (skipped=11)\n")
        self.assertFalse(ok)
        self.assertTrue(quoted.startswith("PARTIAL: 11 test(s) skipped: not named in the output"), quoted)

    def test_skips_add_up_over_a_chained_check_and_a_311_name_reads_once(self):
        n, names = C.skipped("test_b (m.T.test_b) ... skipped 'x'\nRan 1 test\nOK (skipped=1)\n"
                             "test_c (n.U)\nIts docstring ... skipped 'y'\nRan 3 tests\nOK (skipped=1)\n")
        self.assertEqual((n, names), (2, ["m.T.test_b", "n.U.test_c"]))

    def test_hostile_output_is_no_skip_never_a_crash(self):
        for out in (None, "", "OK (skipped=)", "skipped=3", "Ran 1 test\nOK"):
            self.assertEqual(C.skipped(out)[0], 0, out)


if __name__ == "__main__":
    unittest.main()
