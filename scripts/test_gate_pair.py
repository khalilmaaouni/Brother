"""test_gate_pair.py: drives scripts/gate_pair.sh at its entry point (unit SP6).

Each case builds a throwaway git repository holding a byte identical copy of scripts/gate_pair.sh plus a
stub gate, and runs `sh scripts/gate_pair.sh` there as a subprocess. The stub stands in for
scripts/required_fast.sh through the GATE_PAIR_GATE seam (the real gate is never run here): it prints the
rows it is told to print for the interpreter it sees on PATH, rewrites the board file the real gate
rewrites, and logs when it started and ended. Two fake interpreters answer the version probe as 3.13 and
3.9 and hand everything else to the interpreter running this test, so the case does not depend on which
Pythons the machine has. HOME and TMPDIR point into the case's own temp directory.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "gate_pair.sh")

FAKE_PY = """#!/bin/sh
case "$*" in
  *version_info*) echo %s ;;
  *) exec %s "$@" ;;
esac
"""

# The stub gate: mode file per interpreter version, under $STUB_DIR.
STUB = r"""#!/bin/sh
v=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
now() { python3 -c 'import time; print("%.3f" % time.time())'; }
echo "start $v $(now) pwd=$(pwd -P) tmp=$TMPDIR jobs=$REQUIRED_FAST_JOBS sha=$(git rev-parse HEAD)" >> "$STUB_DIR/log"
echo "rewritten by the gate" > docs/plan/READINESS-BOARD.html
sleep 1
mode=$(cat "$STUB_DIR/mode-$v" 2>/dev/null || echo green)
echo "end $v $(now)" >> "$STUB_DIR/log"
case "$mode" in
  green) echo "PASS    exit 0   lint-a                  1s  ok"
         echo "PASS    exit 0   lint-b                  1s  ok"
         echo "pass 2   fail 0   no-data 0"; exit 0 ;;
  red)   echo "PASS    exit 0   lint-a                  1s  ok"
         echo "FAIL    exit 1   lint-b                  1s  boom"
         echo "pass 1   fail 1   no-data 0"; exit 1 ;;
  nodata) echo "PASS    exit 0   lint-a                  1s  ok"
         echo "NO-DATA exit 2   lint-c                  1s  nothing"; exit 0 ;;
  rcfail) echo "PASS    exit 0   lint-a                  1s  ok"; exit 1 ;;
  crash) exit 3 ;;
esac
"""


def _write_exec(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class GatePairTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="gate-pair-test-")
        self.base = os.path.realpath(self._td.name)
        self.repo = os.path.join(self.base, "wt-main")
        self.stub_dir = os.path.join(self.base, "stub")
        self.tmp = os.path.join(self.base, "tmp")
        bin313 = os.path.join(self.base, "bin313")
        for d in (self.repo, self.stub_dir, self.tmp, bin313, os.path.join(self.base, "home")):
            os.makedirs(d)
        _write_exec(os.path.join(bin313, "python3"), FAKE_PY % ("3.13", sys.executable))
        self.py39 = os.path.join(self.base, "py39")
        _write_exec(self.py39, FAKE_PY % ("3.9", sys.executable))
        os.makedirs(os.path.join(self.repo, "scripts"))
        os.makedirs(os.path.join(self.repo, "docs", "plan"))
        shutil.copyfile(SCRIPT, os.path.join(self.repo, "scripts", "gate_pair.sh"))
        _write_exec(os.path.join(self.repo, "scripts", "stub_gate.sh"), STUB)
        with open(os.path.join(self.repo, "docs", "plan", "READINESS-BOARD.html"), "w") as f:
            f.write("committed board\n")
        self.env = dict(os.environ)
        self.env.update({
            "HOME": os.path.join(self.base, "home"), "TMPDIR": self.tmp, "STUB_DIR": self.stub_dir,
            "PATH": bin313 + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
            "GATE_PAIR_GATE": "sh scripts/stub_gate.sh", "GATE_PAIR_PY39": self.py39,
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        })
        for k in ("REQUIRED_FAST_JOBS", "GIT_DIR", "GIT_WORK_TREE"):
            self.env.pop(k, None)
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixture")

    def tearDown(self):
        self._td.cleanup()

    def git(self, *args):
        return subprocess.run(["git"] + list(args), cwd=self.repo, env=self.env, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True).stdout

    def mode(self, version, mode):
        with open(os.path.join(self.stub_dir, "mode-" + version), "w") as f:
            f.write(mode)

    def run_pair(self, *args):
        return subprocess.run(["sh", "scripts/gate_pair.sh"] + list(args), cwd=self.repo, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                              timeout=120)

    def log(self):
        path = os.path.join(self.stub_dir, "log")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [line.split() for line in f if line.strip()]

    def sibling_paths(self):
        out = self.git("worktree", "list", "--porcelain")
        return [l.split(" ", 1)[1] for l in out.splitlines()
                if l.startswith("worktree ") and os.path.realpath(l.split(" ", 1)[1]) != self.repo]

    def test_both_green_exits_0_and_runs_side_by_side_on_the_exact_tree(self):
        r = self.run_pair()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("GATE py3.13 rc=0 rows=2 bad=[]", r.stdout)
        self.assertIn("GATE py3.9 rc=0 rows=2 bad=[]", r.stdout)
        starts = {f[1]: f for f in self.log() if f[0] == "start"}
        ends = {f[1]: float(f[2]) for f in self.log() if f[0] == "end"}
        self.assertEqual(sorted(starts), ["3.13", "3.9"], self.log())
        # Overlap: each run started before the other ended.
        self.assertLess(max(float(s[2]) for s in starts.values()), min(ends.values()), self.log())
        a, b = starts["3.13"], starts["3.9"]
        self.assertEqual(a[3], "pwd=" + self.repo)
        self.assertNotEqual(a[3], b[3])
        self.assertNotEqual(a[4], b[4])  # separate TMPDIRs
        self.assertEqual(a[5], "jobs=2")
        self.assertEqual(b[5], "jobs=2")
        self.assertEqual(a[6], b[6])  # same commit
        self.assertEqual(self.git("status", "--porcelain"), "")  # the board the gate rewrote is restored
        self.assertEqual(self.sibling_paths(), [])  # removed without --keep-worktree

    def test_one_red_row_exits_1_naming_it(self):
        self.mode("3.9", "red")
        r = self.run_pair()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("GATE py3.9 rc=1 rows=2 bad=[FAIL lint-b]", r.stdout)
        self.assertIn("GATE py3.13 rc=0 rows=2 bad=[]", r.stdout)

    def test_a_nodata_row_under_exit_0_is_not_a_pass(self):
        self.mode("3.13", "nodata")
        r = self.run_pair()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("GATE py3.13 rc=0 rows=2 bad=[NO-DATA lint-c]", r.stdout)

    def test_a_nonzero_exit_with_only_pass_rows_is_red(self):
        self.mode("3.9", "rcfail")
        r = self.run_pair()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("GATE py3.9 rc=1 rows=1 bad=[]", r.stdout)

    def test_an_interpreter_crashing_with_no_output_is_nodata_exit_2(self):
        self.mode("3.13", "crash")
        self.mode("3.9", "red")  # a known red beside it does not hide the missing output
        r = self.run_pair()
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("GATE py3.13 rc=3 rows=0 bad=[]", r.stdout)
        self.assertIn("NO-DATA: py3.13 printed no gate rows", r.stdout)

    def test_a_dirty_tree_is_refused_before_anything_runs(self):
        with open(os.path.join(self.repo, "untracked.txt"), "w") as f:
            f.write("x\n")
        r = self.run_pair()
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("GATE REFUSED: tree not clean", r.stdout)
        self.assertEqual(self.log(), [])
        self.assertEqual(self.sibling_paths(), [])

    def test_the_same_interpreter_twice_is_refused(self):
        self.env["GATE_PAIR_PY39"] = os.path.join(self.base, "bin313", "python3")
        r = self.run_pair()
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("are both 3.13", r.stdout)
        self.assertEqual(self.log(), [])

    def test_keep_worktree_keeps_and_the_next_run_reuses_it(self):
        r = self.run_pair("--keep-worktree")
        self.assertEqual(r.returncode, 0, r.stdout)
        kept = self.sibling_paths()
        self.assertEqual(len(kept), 1, r.stdout)
        self.assertEqual(self.git("-C", kept[0], "status", "--porcelain"), "")
        self.git("commit", "-q", "--allow-empty", "-m", "next")
        r = self.run_pair()
        self.assertEqual(r.returncode, 0, r.stdout)
        shas = [f[6] for f in self.log() if f[0] == "start"]
        head = "sha=" + self.git("rev-parse", "HEAD").strip()
        self.assertEqual(shas[2:], [head, head], self.log())
        self.assertEqual(self.sibling_paths(), [])

    def test_a_kept_sibling_carrying_stray_files_is_refused_never_cleaned(self):
        self.assertEqual(self.run_pair("--keep-worktree").returncode, 0)
        stray = os.path.join(self.sibling_paths()[0], "stray.txt")
        with open(stray, "w") as f:
            f.write("x\n")
        r = self.run_pair()
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("carries files outside the commit", r.stdout)
        self.assertTrue(os.path.exists(stray))
        self.assertEqual(len([f for f in self.log() if f[0] == "start"]), 2)

    def test_a_live_lock_refuses_a_second_run(self):
        common = os.path.join(self.repo, ".git", "gate-pair")
        os.makedirs(common)
        holder = subprocess.Popen(["sleep", "30"])
        try:
            key = "wt-main-" + subprocess.run(
                ["sh", "-c", "printf '%s' \"$1\" | cksum | cut -d' ' -f1", "sh", self.repo],
                stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
            os.makedirs(os.path.join(common, key + ".lock"))
            with open(os.path.join(common, key + ".lock", "pid"), "w") as f:
                f.write(str(holder.pid))
            r = self.run_pair()
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("another gate_pair holds", r.stdout)
            self.assertEqual(self.log(), [])
        finally:
            holder.kill()
            holder.wait()
        r = self.run_pair()  # holder dead: the stale lock is taken over
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
